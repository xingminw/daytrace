# DayTrace

DayTrace 是一个 local-first 的个人每日轨迹系统。

它通过你明确授权的数据源，自动整理每天的时间、地点、项目进展、代码提交、文档/文章产出与 AI 协作痕迹，生成一份可信、可回顾、可修正的个人日报。

## 核心定位

DayTrace 不是企业工时管理工具，也不是为了“证明你工作了多久”。

它首先服务个人：帮助你看见自己一天的时间、注意力和创造力流向哪里，以及最终留下了什么产出。

## 初始目标

DayTrace 的核心是一个中央个人 Agent：它可以在少量数据下工作，也可以在用户信任后处理海量、多源、高噪音的个人活动数据。

v0 可以先实现少量 connector，但架构上要面向更多数据源：

1. GitHub / 本地 Git：代码提交、活跃 repo、变更摘要。
2. 文档与写作：本地 Markdown、Overleaf/LaTeX 项目、可导出的文档变更。
3. 日历：当天会议、时间块、地点线索。
4. Hermes 会话：当天与 AI 协作推进过的任务。
5. macOS activity：前台 app、活跃/空闲状态、可选窗口标题。
6. 未来更多 connector：浏览器、文件系统、位置、邮件、聊天等。
7. 手动修正：地点、项目归因、排除规则。

## 文档

- [Product Brief](docs/product-brief.md)
- [Information Design](docs/information-design.md)
- [Agent Architecture](docs/agent-architecture.md)
- [Experience Design](docs/experience-design.md)
- [Data Sources & Permissions](docs/data-sources-and-permissions.md)
- [Engineering Spec](docs/engineering-spec.md)
- [Output Examples](docs/output-examples.md)
- [Implementation Plan v0](docs/implementation-plan-v0.md)
- [Connection Inventory](docs/connection-inventory.md)
- [Prototype Run 2026-05-13](docs/prototype-run-2026-05-13.md)
- [Delivery Setup](docs/delivery-setup.md)
- [Dashboard & Database Prototype](docs/dashboard-database-prototype.md)
- [Frontend QA 2026-05-13](docs/frontend-qa-2026-05-13.md)
- [Multi-Device Sync](docs/multi-device-sync.md)
- [DayTrace Next Plan](docs/daytrace-next-plan.md)
- [Single-Machine Focus Plan](docs/single-machine-focus-plan.md)
- [Open Questions](docs/open-questions.md)
