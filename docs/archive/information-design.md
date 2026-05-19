# DayTrace Information Design

## 核心信息对象

DayTrace 每天围绕以下对象生成记录：

1. **Day**：某一天的整体轨迹。
2. **Activity Segment**：连续活动时间段，例如上午写作、下午 coding、晚上产品讨论。
3. **Project**：被推进的项目，例如 Daily Briefing、Hermes Agent、论文/文章项目。
4. **Artifact**：当天留下的产出，例如 commit、PR、LaTeX 章节、Markdown 文档、产品决策。
5. **Evidence**：支撑推断的证据，例如 git commit、文件修改、日历事件、Hermes 会话摘要。
6. **Correction**：用户对日报的修正，例如“这段不算工作”“这个文件属于 DayTrace”。

## 每日报告的基本结构

```markdown
# DayTrace · YYYY-MM-DD

## 今日概览

用 3-5 句话总结今天的主要工作、节奏和产出。

## 工作时间

- 活跃窗口
- 主要深度工作段
- 零散活动段
- 置信度与推断来源

## 工作地点

- 主要地点
- 辅助地点
- 推断方式：手动 / 日历 / Wi-Fi / 设备 / 未知

## 今日做了什么

按项目或主题组织，而不是按 app 列表机械输出。

## 代码提交

按 repo / PR / commit 汇总。

## 写作与文档

包括 Overleaf、Markdown、Feishu 文档、博客或产品文档。

## 今日模式

例如：深度创作日、代码产出日、产品设计日、碎片推进日、沟通密集日。

## 可修正项

列出低置信度判断，方便用户一句话修正。
```

## 推断策略

DayTrace 不应该直接把所有事件堆给用户，而是先做聚合：

1. 收集原始事件。
2. 归并到项目。
3. 识别连续活动段。
4. 提取当天 artifact。
5. 生成自然语言总结。
6. 附上证据和低置信度项。

## 数据包容与复杂度处理

DayTrace 不应被设计成只能接少量入口。更准确的原则是：

- **Small Data Mode**：只有 Git、Calendar、Hermes sessions 等少量数据时，也能生成有用日报。
- **Rich Data Mode**：当用户愿意接入 macOS activity、窗口标题、浏览器、文件系统、位置、聊天、邮件等更多数据时，系统也能承载。

因此，DayTrace 的核心不只是低摩擦接入，而是中央 Agent 对复杂信息的处理能力：

1. 不同来源统一成标准事件。
2. 每个来源先做局部摘要。
3. 再按时间段、项目、artifact 聚合。
4. 最后生成日报和长期项目记忆。

v0 可以先实现少量 connector，但信息架构要允许未来接入更多数据。

## 修正机制

用户可以用自然语言修正：

- “今天主要地点是办公室。”
- “这个 Overleaf 项目属于论文，不属于 DayTrace。”
- “以后 Downloads 不要进入统计。”
- “这类 Feishu 群聊不要计入工作。”

DayTrace 应将修正转化为规则，并在下一次报告中应用。
