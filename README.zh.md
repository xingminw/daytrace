# DayTrace

本地工作痕迹与日周统计网站。采集 Codex 用户输入和 Git 提交等记录，保存到
SQLite，并按本地仓库身份组织项目。历史飞书快照保留只读，当前不再同步、上传或清理。

```sh
cp config/devices/mac-local.example.yaml config/devices/mac-local.yaml
# 编辑本机配置，将 Git 根目录改为自己的项目目录。
make daily           # 本地采集、备份、导入和统计，不调用 AI
make sync-projects   # 更新仓库索引及历史归属
make dashboard       # loopback 网站；浏览页面不会触发付费生成
make test
```

速读使用显式的“离线计划 → 获批后限额生成 → 核读后本地发布”流程，见
[证据速读说明](docs/evidence-briefs.md)。网站和采集启动器保持 AI 关闭；配置密钥
不等于批准处理。旧的无预算生成及历史批处理执行入口已停用，历史结果继续可读。

本机配置、数据、备份和人工私密小样不进入 Git。可选 SSH 采集仅使用显式配置的
本地设备登记，不自动发现其他设备，也不自动投递邮件。

参见[本地仓库工作流](docs/local-projects.md)、[安装](docs/setup.zh.md)。历史架构文档
和 demo 可能展示早期集成，不代表当前启用的功能。
