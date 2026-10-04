# 本机部署

使用 Python 3.10 以上和仓库 `.venv`，依赖见 `requirements.txt`。

1. 将 `config/devices/mac-local.example.yaml` 复制为被 Git 忽略的 `mac-local.yaml`，修改本机来源路径；在 `config/projects.yaml` 配置索引目录。
2. 运行 `make daily` 进行本地采集、备份、幂等导入和统计。历史范围使用 `scripts/run_local.py --start YYYY-MM-DD --end YYYY-MM-DD`。
3. `make sync-projects` 更新仓库归属，保留原始内容及历史快照。
4. `make dashboard` 前台启动 `127.0.0.1:8766`。安装常驻网站使用 `bash scripts/install_launchd.sh`；日志位于 `~/Library/Logs/daytrace/`。项目所在磁盘需保持挂载。

定时采集是单独的显式操作。`scripts/install_daily_collection.py` 使用北京时间每日
04:30，并检查主机时区；运行本地采集后再读取明确配置的 SSH 来源，失败时跳过，
AI 保持关闭。仅查看配置时无需运行安装器。

Tailscale Serve、绑定和访问规则需要另外确认；安装脚本不会自动修改它们或启用
Funnel。网站没有独立登录认证，不应把私密活动记录公开发布。

可选的 `scripts/configure_deepseek.py` 交互式保存已有密钥，不回显、不联网、不启用
定时 AI。真实生成必须通过[证据速读 CLI](evidence-briefs.md)批准具体计划和预算；
采集及阅读已有报告不需要 API 密钥。飞书写入和清理入口已停用，旧快照保留。
