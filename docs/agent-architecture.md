# Agent Architecture

DayTrace 的核心不是“接入少数几个数据源”，而是构建一个能够承载不同数据规模的中央个人 Agent。

这个 Agent 既能在只有少量数据时生成有用日报，也能在用户信任并授权更多数据后，处理更复杂、更海量的个人活动信息。

## 核心观点

DayTrace 应把数据接入和信息理解分开：

1. **数据接入层**：尽可能包容，允许接入 GitHub、Overleaf、Calendar、Feishu、macOS activity、浏览器、文件系统、位置、邮件、聊天、AI 会话等多种来源。
2. **事件归一层**：把不同来源统一成标准事件、证据、活动段和 artifact。
3. **中央 Agent 层**：负责理解、归因、压缩、去噪、合并冲突、生成日报和回应用户修正。
4. **输出层**：生成每日记录、周报、项目回顾、dashboard、Feishu 通知和可分享版本。

真正的产品壁垒不只是 connector 数量，而是中央 Agent 能否把复杂信息处理成可信、可读、有用的个人记录。

## 数据规模的双模式

DayTrace 需要支持两种模式：

### 1. Small Data Mode

适用于刚开始使用时：

- 只接入 Git / Calendar / Hermes sessions 等少量来源。
- 配置简单。
- 主要目标是先生成可用日报。

### 2. Rich Data Mode

适用于用户高度信任 DayTrace 后：

- 接入 macOS activity、窗口标题、浏览器、文件系统、位置、聊天、邮件等更多数据。
- 事件量显著增加。
- 中央 Agent 需要进行分层摘要、长期记忆、项目归因和噪音过滤。

DayTrace 不应该因为 v0 克制，就在产品设计上限制未来只能处理少量数据。

## 中央 Agent 的职责

### 1. 事件标准化

不同 connector 产生的数据必须转换成统一格式：

```yaml
id: evt_...
time:
  start: 2026-05-13T10:00:00+08:00
  end: 2026-05-13T10:25:00+08:00
source: macos_activity
kind: app_focus
summary: Cursor was active on the daytrace repo
raw_ref: local://events/2026-05-13/macos_activity.jsonl#123
project_guess: daytrace
confidence: 0.72
sensitivity: medium
evidence:
  - app: Cursor
  - window_title: docs/agent-architecture.md — daytrace
```

### 2. 分层压缩

当数据量变大时，不能把所有原始数据直接塞给 LLM。需要分层处理：

1. Raw events：原始事件，本地保存。
2. Source summaries：每个数据源先局部摘要。
3. Project summaries：按项目聚合。
4. Day narrative：生成每日叙事。
5. Long-term memory：只沉淀稳定规则、项目映射和用户修正。

### 3. 项目归因

DayTrace 需要判断一个事件属于哪个项目：

- git repo 名称
- 文件路径
- 文档标题
- 日历标题
- Hermes 会话主题
- app/window title
- 用户历史修正

归因应该有置信度，并允许用户纠正。

### 4. 冲突处理

多个数据源可能互相矛盾：

- Calendar 显示开会，但 macOS activity 显示在写代码。
- 前台 app 是 Chrome，但窗口标题是 Overleaf。
- 文件修改时间很多，但没有 commit。

中央 Agent 不应强行给唯一答案，而应输出更符合现实的解释，例如“会议中同时修改了文档”或“可能是会后整理”。

### 5. 噪音过滤

Rich Data Mode 下噪音会很多。Agent 需要学会：

- 忽略短暂切换 app。
- 合并连续相似活动。
- 降低低价值来源的权重。
- 只在日报中呈现与项目、产出、时间结构相关的信息。

### 6. 用户修正学习

用户修正是核心交互：

- “这段算 DayTrace。”
- “这个不算工作。”
- “以后 Chrome 的这个域名归到研究。”
- “窗口标题不要记录 Feishu 私聊。”

Agent 应将修正转化为规则、项目映射或过滤条件。

## Connector 设计原则

DayTrace 应有广泛 connector 能力，但 connector 不应该决定产品形态。

每个 connector 只负责：

1. 授权与配置。
2. 拉取或监听数据。
3. 转成标准事件。
4. 标注敏感度和证据引用。

不要让每个 connector 各自生成日报逻辑。

## 隐私策略的重新定位

DayTrace 的第一用户是用户自己。隐私不是阻止能力扩展的理由，而是让用户能控制接入范围和处理方式的机制。

因此设计重点应是：

- 清楚显示接入了什么。
- 支持用户自己开启更多数据源。
- 支持本地保存原始数据。
- 支持选择哪些数据可进入 LLM。
- 支持排除、暂停和删除数据源。

不是为了隐私而把系统设计得过窄。

## v0 与长期形态的关系

v0 可以先实现少量 connector，但架构必须面向 Rich Data Mode：

- 从一开始定义标准事件 schema。
- 从一开始保留 source summary / project summary 的分层。
- 从一开始允许配置多个 connector。
- 从一开始把中央 Agent 作为核心，而不是把日报逻辑写死在单个采集脚本里。

## 产品判断

DayTrace 的长期形态是：

> 一个用户信任后可以接入大量个人活动数据的中央个人 Agent，它把复杂、碎片化、高噪音的信息整合成每日轨迹、项目记忆和可行动的自我理解。

