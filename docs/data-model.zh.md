# DayTrace 数据模型

> 🌐 [English](data-model.md)

全部在一个 SQLite 文件里(`data/daytrace.sqlite`,2026-05-18 时是 schema
版本 12)。定义在 `daytrace/db.py`;迁移在 `init_db()` 里。加表或改列
类型时 bump 一下 `SCHEMA_VERSION`。

## 表

### `events` —— 原始时间线
最基础对象。每个 collector 最终都往这里写。

| 列                | 类型 | 备注 |
|---|---|---|
| `id`              | TEXT PK | collector 给的稳定 ID |
| `date`            | TEXT    | YYYY-MM-DD 偏移日(04:00 边界) |
| `source`          | TEXT    | `claude_code`、`codex`、`git`、`hermes`、`docs`、… |
| `kind`            | TEXT    | source 内部的子类型 |
| `start`, `end`    | TEXT    | ISO 时间戳 |
| `title`, `summary`| TEXT    | 这条事件是什么 |
| `project_guess`   | TEXT    | collector 的项目归因(可为空;AI 会填 "misc") |
| `sensitivity`     | TEXT    | `normal` / `private` / `sensitive` |
| `evidence_json`   | TEXT    | collector 想附加什么都行(文件路径、repo、commit SHA、…) |
| `raw_ref`         | TEXT    | 指回原始数据行(调试用) |
| `device_id`       | TEXT    | 哪台机器产生的(`Mac`、`omen-wsl`、…) |
| `location_id`     | TEXT    | 粗粒度位置(`home`、`office`、`unknown`) |
| `collector_id`    | TEXT    | 哪个 collector 写的(`hub-local`、`ssh-omen-wsl-rsync`) |
| `inserted_at`     | TEXT    | 入库时间 |

`date`、`source`、`project_guess`、`start` 上有索引。

### `day_report` —— 每天的 summary header
每个偏移日一行。聚合计数 + 编排器用来判断"事件变了,要重算"的
`events_hash`。

### `day_channel` —— 每天计算出来的 JSON
多语种缓存。Stats / AI 计算结果都写这里。Schema:

| 列                  | 备注 |
|---|---|
| `date`              | PK 一部分 |
| `channel`           | PK 一部分 —— 例如 `time_span`、`ai_overview` |
| `value_json`        | JSON 载荷(每个 channel 形状不同) |
| `generator`         | `stats` 或 `ai` |
| `generator_version` | bump 让缓存行失效 |
| `source_hash`       | 计算时的 `events_hash` |
| `tokens_in/out/cost_usd` | AI 行的成本核算 |
| `error`             | 上次失败信息(如有) |

当前注册的 channel(每个算什么详见 [architecture.zh.md](architecture.zh.md)):
`time_span`、`active_minutes`、`longest_focus_block`、`context_switches`、
`peak_windows`、`dimension_counts`、`quality`、`ai_overview`、
`ai_continuity_day`、`ai_project_summary_batch`、
`ai_project_continuity_batch`、`ai_activity_labels`。

### `day_project_report`, `day_project_channel`
同样思路,但 PK 是 `(date, project, channel)`。`project='misc'` 装那些
之前 project_guess 为 NULL 的事件。Channel:`time_span`、`active_minutes`、
`source_mix`、`device_mix`、`top_titles`、`event_density`。

### `event_activity_labels`
每个被打标签的事件一行。由 `ai_activity_labels` channel 填。在
「活动」维度视图里 JOIN 进来。

### `work_items` —— 飞书多维表格镜像
多表(`table_key` 列标记来自哪个 Bitable —— `tasks`、`reviews`、…)。
**对飞书是只读**;数据单向流动:飞书 → DayTrace,触发点是
`run_daily.py work-items-sync`。

| 列 | 备注 |
|---|---|
| `record_id` PK | 飞书 record id |
| `table_key`    | 来自哪个配置过的 Bitable |
| `title`        | 任务名 / 题目 |
| `subtitle`, `status`, `priority`, `tags`, `due_date`, … | 常规字段 |
| `external_links` | JSON 数组(GitHub / Overleaf / 文档 URL) |
| `raw_fields_json` | 整行 dump(调试用) |

### `event_work_item_links` —— 桥接表
`(event_id, record_id, match_type, confidence)`。`match_type` 是:

- `github_url` —— collector evidence URL 匹配 `work_items.external_links`
- `local_path` —— 本地 repo 路径匹配某个已知 overleaf / git 项目
- `alias`      —— `config/work_item_aliases.yaml` 映射的
- `manual`     —— 审计面板 POST 加的
- `ai`         —— (未来) LLM 推断的

`rebuild_links()` 在可配置的 lookback 窗口内,把整张表从头重算。

### `device_pull_log` —— 每(设备,日期)的 catchup 状态
`run_daily.py catchup` 用这个表来知道哪些 `(remote, date)` 还没拉成功。
远端离线时会停在 `pending`,下一次再试。

### 基础表
`meta`、`sources`、`source_rules`、`devices`、`locations`、
`event_corrections`、`ingest_runs`、`imported_files`、`runs` ——
大部分是原型时期的,当前用得不多。`meta` 存 schema 版本号供迁移用。

## events_hash —— 怎么判断"过期了"

```python
events_hash = sha256(sorted(event.id for event in date_events))[:16]
```

每个 `day_channel` 行记录算它时用的 `source_hash`。编排器**跳过**一个
channel 的条件:

  - 行存在 AND
  - `source_hash` 等于今天的 events_hash AND
  - `generator_version` 等于注册时的 spec.version

任何一项不匹配 → 重算并覆盖。

## Schema 迁移

很轻量。`init_db()` 做的事:

1. 对每张表 `CREATE TABLE IF NOT EXISTS`(幂等)
2. 对每个后加的列调 `_ensure_column()`(用 `PRAGMA table_info` 检测,
   不存在就 `ALTER TABLE ADD COLUMN`)
3. 更新 `meta.schema_version`

所以升级就是 `git pull` + `python -c "from daytrace.db import connect,
init_db; init_db(connect('data/daytrace.sqlite'))"` —— 或者下次
dashboard / cron 起来时自动跑。
