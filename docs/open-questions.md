# Open Questions

## 已有倾向

- 名称：DayTrace。
- 第一用户：Xingmin 自己。
- 产品方向：个人神器，不是企业管理工具。
- v0 应低摩擦接入少数高价值数据源，而不是一开始接入一堆平台。

## 待确认问题

1. v0 的第一批数据源是否确定为：Git/GitHub、Overleaf via Git、Calendar、Hermes sessions？
2. DayTrace 每天几点生成日报？
3. 日报默认只保存在本地，还是同时发到 Feishu？
4. 工作地点 v0 是否先手动确认，而不是自动定位？
5. Overleaf 项目是否可以开启 Git 同步，或是否已有本地 LaTeX repo？
6. GitHub 是否需要接入 API/gh CLI，还是先只扫本地 repo？
7. 哪些目录永远不应该进入 DayTrace？

## 高风险/后置问题

- 是否接入浏览器历史？
- 是否接入 macOS 前台 app/window title？
- 是否接入 Wi-Fi/位置推断？
- 是否做 localhost dashboard？
- 是否未来允许生成对外分享版本？
