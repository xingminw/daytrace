# DayTrace 架构

> 🌐 [English](architecture.md)

各部件如何拼在一起 —— 一个模块一个模块、一个 channel 一个 channel 地讲。
从头读到尾,以后哪里要改你就知道该看哪个文件。

## 流水线

```
┌─ 采集 ─────────────────────────┐   ┌─ 编排器 ────────┐   ┌─ 投递 ──────────────────┐
│ scripts/collect_*.py            │   │                  │   │                          │
│ scripts/run_daily.py catchup    │   │ regenerate_day_  │   │ /today, /weekly          │
│   ├ collect_from_config         │   │ from_db()        │   │   (dashboard/server.py)  │
│   ├ rsync 远端 ssh →            │ → │ 按依赖顺序跑     │ → │ /api/* JSON              │
│   │   data/inbox/<dev>/<dt>/    │   │ channel:         │   │ Markdown + PNG 图表       │
│   └ import_inbox.py             │   │  stats (廉价)    │   │   + 飞书云文档导入        │
│                                 │   │  ai (DeepSeek)   │   │   + Gmail SMTP            │
│ → events 表                     │   │ → day_channel    │   │ (scripts/export_report)  │
└─────────────────────────────────┘   └──────────────────┘   └──────────────────────────┘
```

一个 SQLite 文件(`data/daytrace.sqlite`)就是整个系统的真理来源。每一步
都从它读或往它写,**没有独立的缓存层**。

## 模块(去哪里找什么)

| 路径 | 行数 | 做什么 |
|---|---|---|
| `daytrace/schema.py`         | 74   | `TraceEvent` 数据结构 —— 所有 collector 必须产出的事件形状 |
| `daytrace/db.py`             | 817  | 全部 SQL:`init_db()`、`connect()`、`upsert_events()`、`query_events()`、`query_today()`、schema 迁移、`iso_week_to_date_range()` 等。18 张表的唯一定义点(详见 [data-model.zh.md](data-model.zh.md))。 |
| `daytrace/io.py`             | tiny | collectors 和 `import_inbox` 用的 JSONL 读写 helper |
| `daytrace/collector_config.py` | 133  | 解析 `config/devices/<device>.yaml`(每台机器的 source 开关 + 路径) |
| `daytrace/stats.py`          | 355  | 纯确定性的统计 channel(time_span / active_minutes / longest_focus_block / context_switches / peak_windows / dimension_counts / quality)。无 I/O、无 LLM |
| `daytrace/channels.py`       | 446  | channel 注册表 + 依赖排序 + `regenerate_day()` 编排器。stats channel 在 import 时注册,AI channel 在 import `ai_report` 时注册 |
| `daytrace/ai_client.py`      | 251  | 极简 DeepSeek HTTPS client(只用 stdlib)。JSON-mode + shape validator + 1 次重试。会自动加载 `~/.daytrace/secrets.env`,让 launchd 拉起的进程也能读到 `DEEPSEEK_API_KEY` |
| `daytrace/ai_report.py`      | 955  | 5 个 AI channel:`ai_overview`、`ai_continuity_day`、`ai_project_summary_batch`、`ai_project_continuity_batch`、`ai_activity_labels`。包含 prompt + JSON validator + 7 天 baseline 计算。bump `AI_VERSION` 会让整个缓存失效 |
| `daytrace/daily_report.py`   | 415  | 极薄门面:`regenerate_day_from_db(con, date, include_ai=True)` 和 `load_day_report(con, date)`。import 时副作用加载 `ai_report` 触发 AI channel 注册 |
| `daytrace/work_items.py`     | 577  | 飞书多维表格只读同步 + 事件↔任务链接器(URL 匹配 + alias yaml + AI)。`rebuild_links()` 在 catchup 和审计面板 POST 时调用 |
| `daytrace/remotes.py`        | 74   | `config/remotes.yaml` 的 loader |
| `daytrace/report_export.py`  | 376  | `archive_markdown_for_date()` / `archive_markdown_for_week()` —— 把一天或一周变成可以直接做邮件正文 / 飞书云文档导入的 Markdown 字符串 |
| `daytrace/report_charts.py`  | 429  | matplotlib PNG 图表,跟 dashboard 的堆叠柱图+饼图视觉一致。共用 `TIMELINE_PALETTE` 所以颜色对得上 |
| `daytrace/report_delivery.py`| 470  | 飞书云文档导入(用 `lark-cli drive +import` 然后 `docs +media-insert` 嵌入图表)+ Gmail SMTP(multipart HTML body + 内嵌图片) |
| `dashboard/server.py`        | 5086 | 所有 HTTP route + 页面渲染。除了 SQLite 连接外无状态 |

CLI 入口:

| 路径 | 用途 |
|---|---|
| `scripts/run_daily.py {status,catchup,work-items-sync,deploy}` | 主入口。`catchup` 是 launchd 每天跑的那个 |
| `scripts/collect_*.py`        | 一个 source 一个 —— 通过 `collect_from_config` 间接调用 |
| `scripts/import_inbox.py`     | JSONL → `events` 表。幂等 |
| `scripts/export_report.py`    | 渲染 + 可选上传 + 可选邮件。投递流水线的 CLI 版本 |
| `scripts/cleanup_feishu_reports.py` | 维护工具:清理飞书云盘里的旧版本 |
| `scripts/daytrace-{daily,weekly}.sh` | launchd 包装(设置 PATH、调 Python、日志写到 `data/logs/`) |

## 编排器(channels.py)

每一段计算 + 缓存的状态都是一个 **channel** —— `day_channel` 或
`day_project_channel` 里的一行。一个 channel 有:

- `name` —— `time_span`、`ai_overview` 等
- `table` —— `day` 或 `day_project`
- `generator` —— `stats`(便宜,经常重算)或 `ai`(花钱,被 `include_ai` 控制)
- `version` —— bump 一下就让全部缓存行失效
- `dependencies` —— 必须先跑的 channel 名字
- `compute` —— 纯函数,返回 JSON 值(AI 还附带 token 用量 + 成本)

`regenerate_day()` 按依赖顺序遍历所有已注册 channel。对每个 channel 检查:
是否已有同 events hash + 同 generator version 的行;有就跳过,没有就重算
并覆盖。stats channel 在事件变化时每次都重算,AI channel 同样有 hash
检查但额外被 `include_ai=False` 控制,允许 cron 跳过它们。

### Stats channel(始终运行)

| Channel | 算什么 |
|---|---|
| `time_span`           | 偏移日的首尾事件时间 |
| `active_minutes`      | 有事件的 5 分钟槽求和 |
| `longest_focus_block` | 最长无间断的 5 分钟槽连续段 + 主导 source/project |
| `context_switches`    | 跨槽的项目切换次数 |
| `peak_windows`        | 事件最多的几个小时 |
| `dimension_counts`    | 按(source/project/device/location)的事件计数 |
| `quality`             | `sensitive` / `missing_project` 的行数 |

每项目版本在 `day_project_channel` 里:`time_span`、`active_minutes`、
`source_mix`、`device_mix`、`top_titles`、`event_density`。

### AI channel(`include_ai=True`,每天约 $0.01-0.03)

| Channel | 成本 | 产出 |
|---|---|---|
| `ai_overview`                 | ~5K in / ~1K out | `{headline, overview.narrative, trend, highlights, work_pattern, suggestions}` —— 3 列 Insights + 叙事。看到:活跃任务清单、7 天 baseline、当日 stats、带 `[task:X]` / `[proj:Y]` 前缀的完整事件清单 |
| `ai_continuity_day`           | ~1K in / ~0.5K out | 今天 vs 昨天的 momentum chip + 关系句 |
| `ai_project_summary_batch`    | ~6K in / ~2K out | 一次 LLM 调用 → dict `{project → summary}`,覆盖每个活跃项目 |
| `ai_project_continuity_batch` | ~3K in / ~1K out | 每个项目 vs 该项目上一次活跃日的 momentum |
| `ai_activity_labels`          | ~5K in / ~2K out | 每个事件的活动标签(每天封顶 5-10 类的自由 taxonomy)。写到 `event_activity_labels` 表 |

这 5 个 channel 都在一次 `regenerate_day_from_db()` 里跑。

## 多设备 hub 模型

一台 Mac 是 hub。其它 Linux / Windows-WSL 机器列在 `config/remotes.yaml`
里。每天的流水线:

1. `run_daily.py deploy`(可选,无变化时是 fast no-op):
   `rsync scripts/ daytrace/ config/` → 每个远端的 `repo_path`
2. `run_daily.py catchup` 对每个 `(remote, 待处理 date)` pair:
   - ssh 进去,`cd repo_path`,`python scripts/collect_from_config.py
     --config <device.yaml> --date <date> --output data/inbox/<dev>/<date>/`
   - `rsync` 那一片 slice 回 hub 的 `data/inbox/<dev>/<date>/`
   - hub 上:`import_inbox.py data/inbox/<dev>/<date>/` → `events` 表
   - `regenerate_day_from_db(con, date, include_ai=True)`
   - 失败(远端离线、ssh 超时)记到 `device_pull_log`,下一次再试。其它
     remote / 日期照常推进

## Dashboard

`dashboard/server.py` 是纯 `http.server.BaseHTTPRequestHandler`,没用任何
framework。页面渲染 HTML 字符串,API 返回 JSON。所有 state 来自
`data/daytrace.sqlite`。

| Path | 返回 |
|---|---|
| `/`              | 跳到 `/today` |
| `/today?date=…`  | 每日 Report 卡片 + 图表 + insights + 任务面板 + 审计 |
| `/weekly?week=…` | 周报 + 图表 + insights + 泳道/热力 + 每日时间轴 + 任务 + 审计 |
| `/events?…`      | 原始事件浏览器(可筛选) |
| `/sources`       | 各 source 健康度 |
| `/api/today`     | `/today` 的 JSON 视图 |
| `/api/events`    | JSON 事件查询 |
| `/api/summary`   | 顶部数字 |
| `POST /api/work-items/alias` | 把审计面板的选择写入 `config/work_item_aliases.yaml` 并调 `rebuild_links()` |

投递(飞书 + 邮件)见 [setup.zh.md](setup.zh.md)。
