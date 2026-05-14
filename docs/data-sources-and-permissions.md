# Data Sources & Permissions

DayTrace 的信任基础是：用户知道它接入了什么、读取了什么、哪些数据会被总结、哪些数据不会离开本地。

## 数据源策略

DayTrace 应支持“少量数据可用、海量数据更强”的设计。

v0 可以从少数高价值 connector 开始，但产品边界不应限制在少数入口。长期上，DayTrace 应允许用户在信任系统后接入尽可能多的个人活动数据。

关键问题不是“数据源能不能很多”，而是中央 Agent 能不能把多源、高噪音、互相冲突的信息处理成有效记录。

### 1. GitHub / 本地 Git

**价值**：高。代码提交、活跃 repo、PR、分支和变更摘要是最清晰的工作证据。

**接入方式**：

- 本地扫描 `~/Projects` / `~/projects` 下的 git repo。
- 使用 `git log --since` 读取当天 commits。
- 可选接入 GitHub CLI/API，用于 PR、issue、review 信息。

**权限风险**：中等。commit message 和 diff 可能包含敏感信息。v0 默认只读取元数据和摘要，必要时再读取 diff。

### 2. Overleaf

**价值**：高。适合记录论文、LaTeX 文档和学术写作产出。

**接入方式候选**：

- 优先方案：Overleaf 项目开启 Git 同步后，作为本地 git repo 处理。
- 次优方案：定期导出或同步 `.tex` 文件目录。
- 不优先依赖网页自动化，因为登录、权限和稳定性成本较高。

**权限风险**：中等到高。论文草稿和未公开内容较敏感。默认只统计文件变更与章节摘要，不应随意上传全文。

### 3. Calendar / Feishu Calendar

**价值**：高。日历能提供会议、时间块、地点线索和当天上下文。

**接入方式候选**：

- 如果 Apple Calendar 同步到 Feishu，则优先通过 Feishu 或日历导出读取。
- macOS 本地 Calendar 数据可作为后续方案，但权限和数据格式需要单独验证。
- 也可先用 `.ics` 导出/订阅作为低摩擦方案。

**权限风险**：中等。会议标题、参会人和地点可能敏感。v0 应只读取当天事件摘要。

### 4. Hermes Sessions

**价值**：高。Xingmin 很多产品设计、调试和协作都发生在 Hermes 对话里。

**接入方式**：

- 搜索当天本地 Hermes session。
- 只提取任务主题、项目名、关键决策和产出。

**权限风险**：中等。对话可能包含隐私和密钥上下文。需要避免把原始全文直接外发。

### 5. Apple / macOS Screen Time 类数据

**价值**：中到高。可以理解 app 使用和活动时间。

**接入难度**：中到高。Apple 的 Screen Time 数据通常不提供稳定公开 API。可以考虑替代方案：

- 本地小 agent 记录前台 app / 窗口标题。
- 使用 macOS Accessibility 权限。
- 使用系统日志或第三方工具。
- 先手动记录工作地点和活动时间，不在 v0 强依赖 Screen Time。

**权限风险**：高。app/window/browser 信息非常私密。应作为 v1+ 功能，并且默认关闭。

## 不作为 v0 核心依赖，但保留为 Rich Data Mode connector

- 浏览器完整历史。
- 全量聊天记录。
- 全盘文件监控。
- 精确位置历史。
- 所有 app 的窗口标题。

这些数据很有用，不应从产品设计上排除；只是它们需要更强的权限控制、分层摘要、噪音过滤和用户修正机制。

## 权限面板草案

```text
Source                  Status      Scope                       LLM policy
Git repos               enabled     ~/Projects/*                metadata + selected diffs
Overleaf via Git         planned     selected repos only          metadata + selected tex snippets
Calendar                 planned     today events only            event summaries only
Hermes sessions          enabled     local sessions today         summarized locally first
macOS app usage          later       foreground apps only         aggregate only
Browser history          later       selected domains only        aggregate only
Location/Wi-Fi           later       coarse location only         no raw history
```

## 设计结论

v0 可以先从以下入口启动：

1. Git / GitHub
2. Overleaf via Git
3. Calendar
4. Hermes sessions
5. macOS activity agent
6. 用户手动修正

但 DayTrace 的长期能力应面向更丰富的数据接入。权限面板不是为了限制数据源，而是为了让用户清楚地选择、开启、暂停和审计数据源。
