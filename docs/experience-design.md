# DayTrace Experience Design

## 体验定位

DayTrace 的体验不是“填写日报”，而是“每天被一个可信的个人 Agent 温柔地照一下”。

它应该让用户感觉：

- 我不需要手动记录一整天。
- 我可以接入很多数据，但不会被数据淹没。
- 系统能从复杂痕迹里看出今天真正推进了什么。
- 我能纠正它，并且它会越来越懂我。

## 每日使用节奏

### 1. 白天：无感采集

用户正常工作，不需要主动打开 DayTrace。

后台 connector 采集：

- Git / GitHub activity
- Overleaf / LaTeX writing activity
- Calendar events
- Hermes sessions
- macOS activity：frontmost app、active/idle、可选 window title
- 未来更多 connector：browser、file system、location、mail、chat

采集层不直接生成判断，只产生标准事件和证据引用。

### 2. 晚上：生成每日记录

每天固定时间，DayTrace Agent 执行：

1. 收集当天事件。
2. 每个来源生成 source summary。
3. 按项目、时间段、artifact 聚合。
4. 处理冲突和低置信度判断。
5. 生成三层输出：Feishu 短摘要、Markdown 归档、Dashboard 详情。

### 3. 次日或当晚：用户修正

用户可以用自然语言修正：

- “今天主要地点是办公室。”
- “这段 Chrome 其实是在写 Overleaf。”
- “这个 repo 归到 Daily Briefing，不是 Hermes Agent。”
- “以后这个窗口标题不要记录。”
- “这个不算工作，但算 personal research。”

DayTrace 将修正转化为规则、项目映射或过滤条件。

## 三层输出

### 1. Feishu 短摘要

目标：低打扰、快速知道今天发生了什么。

示例：

```markdown
# DayTrace · 2026-05-13

今天主要推进了 DayTrace 的产品定义和 Daily Briefing 的实现工作。整体是“产品设计 + 原型推进”型工作日。

- 活跃时间：约 6.5h，晚间仍有明显工作段
- 主要项目：DayTrace、Daily Briefing
- 代码：Daily Briefing 原型继续推进，DayTrace repo 初始化
- 文档：新增 DayTrace 产品/Agent 架构文档
- 待确认：今天地点、部分 Chrome/Feishu 活动归因

查看完整记录：localhost dashboard / markdown archive
```

### 2. Markdown 归档

目标：长期保存、可搜索、可回顾。

内容包括：

- 今日概览
- 时间轨迹
- 工作地点
- 项目进展
- 代码提交
- 写作与文档
- AI 协作
- 今日模式
- 证据与低置信度项
- 用户修正记录

Markdown 文件应写入：

```text
outputs/YYYY-MM-DD.md
```

### 3. Dashboard 详情

目标：探索、修正、追溯证据。

Dashboard 应包含：

- Timeline：一天的活动段
- Projects：项目维度聚合
- Artifacts：代码、文档、文章、决策
- Sources：不同 connector 的贡献
- Evidence：每条判断对应的证据
- Corrections：可修正项和历史规则
- Permissions：数据源开关和范围

## Dashboard 初始页面结构

```text
DayTrace Dashboard

[Today Summary]
  今日概览 + 今日模式

[Timeline]
  09:30-10:40  Cursor / DayTrace docs
  10:50-11:30  Calendar meeting + Feishu follow-up
  14:00-16:20  Daily Briefing prototype

[Projects]
  DayTrace
    - 产品结构定义
    - Agent Architecture
    - macOS activity 讨论
  Daily Briefing
    - 原型开发
    - subscription panel 设计

[Artifacts]
  Code commits
  Markdown docs
  Overleaf sections
  Hermes decisions

[Needs Review]
  - 工作地点未知
  - Chrome 活动是否属于 Overleaf？
  - 这个 Feishu 对话是否计入 DayTrace？

[Data Sources]
  Git: enabled
  Calendar: planned
  macOS activity: enabled/basic
  Browser: disabled/rich mode
```

## 交互原则

### 不做机械打卡

DayTrace 不问：

> 今天你工作了几个小时？

它应该先生成推断，再让用户修正。

### 不用一次性配置所有数据源

用户可以从少量数据开始，但系统架构允许逐步打开更多 connector。

### 修正比设置更重要

很多规则不应该靠前置表单配置，而应该从用户修正中学习。

### 证据必须可见

如果 DayTrace 判断“今天主要推进 DayTrace”，它应该能展示证据：

- 修改了哪些文件
- 哪些 Hermes 会话提到 DayTrace
- 哪些 app/window 活动指向 DayTrace
- 哪些 commit 或文档 artifact 支撑这个判断

## 产品气质

- 温暖但不幼稚。
- 有技术感但不 dry。
- 像个人记忆助手，不像绩效工具。
- 帮用户理解自己，不审判用户。

## v0 体验目标

v0 不必做完所有 connector，但必须完成一条端到端链路：

1. 收集至少 Git / Markdown / Hermes sessions / macOS activity 的部分数据。
2. 转成统一事件。
3. 聚合成当天项目与 artifact。
4. 生成 Markdown 日报。
5. 发送 Feishu 短摘要。
6. 标出低置信度项，允许用户后续修正。
