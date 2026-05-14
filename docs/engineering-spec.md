# DayTrace Engineering Spec

## v0 技术目标

构建一个 project-backed scheduled agent：项目中保存配置、采集脚本、提示词和输出模板，由 Hermes cron 每天运行，生成日报并发送到 Feishu。

同时，v0 的架构要面向长期的 Rich Data Mode：多个 connector 产生标准化事件，中央 Agent 负责归一、压缩、归因、去噪和日报生成。

## 初始目录建议

```text
daytrace/
  README.md
  config/
    sources.yaml
    rules.yaml
  scripts/
    collect_git.py
    collect_docs.py
    collect_calendar.py
    collect_hermes_sessions.py
    collect_macos_activity.py
    normalize_events.py
    summarize_sources.py
  prompts/
    daily_report.md
    source_summary.md
  outputs/
    YYYY-MM-DD.md
  docs/
    product-brief.md
    information-design.md
    data-sources-and-permissions.md
    engineering-spec.md
    output-examples.md
    open-questions.md
```

当前草稿先只创建文档，代码和 cron job 待确认后再实现。

## 数据采集与 Agent 处理流程

1. 按日期范围确定 `start_at` / `end_at`。
2. 读取配置中的数据源。
3. 每个 connector 产生标准化事件：

```yaml
- timestamp: 2026-05-13T10:30:00+08:00
  source: git
  project: daytrace
  artifact_type: commit
  title: "docs: initialize product brief"
  evidence:
    repo: ~/Projects/daytrace
    commit: abc123
  sensitivity: medium
```

4. 每个来源先生成 source summary，避免把海量原始事件直接交给 LLM。
5. 中央 Agent 将事件和 source summary 聚合为项目、活动段、artifact。
6. 中央 Agent 处理冲突、低置信度判断和用户历史修正。
7. 生成每日记录。
8. 保存 Markdown 到 `outputs/`。
9. 可选发送到 Feishu。

## Cron 运行方式

最终可使用 Hermes cron：

- schedule：每天晚上固定时间。
- workdir：`~/Projects/daytrace`。
- prompt：读取当天采集结果，生成日报。
- deliver：当前 Feishu 群或指定 home channel。

## 隐私策略

- 默认只读取用户配置的路径和平台。
- 默认只将摘要和必要片段交给 LLM。
- 高敏感数据源需要显式开启。
- 输出中保留低置信度和证据来源，方便用户纠正。

## v0 不做

- 不做企业团队管理功能。
- 不自动 commit 或 push。
- 不要求第一版就把所有 connector 做完。

但以下能力不应从架构上排除：

- 全盘文件监控。
- 浏览器历史采集。
- 精确位置历史。
- app/window title 采集。
- 邮件、聊天和更多平台 connector。

它们属于 Rich Data Mode，需要权限配置、分层摘要和更强的中央 Agent 处理能力。
