# DayTrace 安装

> 🌐 [English](setup.md)

安装、配置、调度,从头到尾。

## 1. 安装

```bash
git clone https://github.com/xingminw/daytrace
cd daytrace
python3 -m pip install -r requirements.txt
```

Python ≥ 3.10。已在 macOS Sonoma + Sequoia 上测试。

飞书相关功能(任务表拉取、飞书云文档投递)需要 `lark-cli`。按
[lark-cli 文档](https://github.com/larksuite/lark-cli)安装;第一次运行会
有一次浏览器授权。

## 2. 配置文件

全在 `config/`。仓库附带 `*.example.yaml` 模板,先拷贝成真实文件名
(后者已 gitignore)再填值:

```bash
make install-config
# 或手动:
#   cp config/work_items.example.yaml        config/work_items.yaml
#   cp config/work_item_aliases.example.yaml config/work_item_aliases.yaml
#   cp config/remotes.example.yaml           config/remotes.yaml
```

下面逐个说。

### `config/devices/<device>.yaml` —— 每台机器采集什么

一台机器一个文件。Hub Mac 是 `config/devices/mac.yaml`;每个远端建一个
同级文件。

```yaml
device:
  id: Mac                    # 必须等于这台机器的身份;事件会被打上这个标
  name: Mac Hub
  location_id: unknown
  collector_id: hub-local

sources:
  codex:        { enabled: true, home: ~/.codex,            limit: 600 }
  claude_code:  { enabled: true, home: ~/.claude/projects,  limit: 800 }
  hermes:       { enabled: true, sessions_dir: ~/.hermes/sessions, limit: 700 }
  git:          { enabled: true, roots: [~/Projects],       limit: 300 }
```

### `config/remotes.yaml` —— 哪些远端往这个 hub 喂数据

先 `cp config/remotes.example.yaml config/remotes.yaml`。

```yaml
remotes:
  - device_id: omen-wsl              # 必须等于远端的 device.id
    ssh: mtl                          # ~/.ssh/config 里的别名
    repo_path: /mnt/d/research-programs/daytrace
    config: config/devices/omen-wsl.yaml
```

Hub 用这个文件来跑 `run_daily.py deploy`(把代码推到每个 remote)和
`run_daily.py catchup`(ssh 进去跑 collector,再把事件 rsync 回来)。

### `config/work_items.yaml` —— 飞书多维表格同步

先 `cp config/work_items.example.yaml config/work_items.yaml`,把 Feishu
Bitable 的 ID 填进去。每个表有自己的(字段名 → DayTrace 列)映射。文件
顶部有详细注释;简要:

```yaml
tables:
  - key: tasks            # 主任务表,驱动「任务」维度聚合
    app_token: bsccTXXXX
    table_id: tblXXXX
    field_map:
      title: 任务
      status: 状态
      priority: 优先级
      # …
  - key: reviews
    collapse_in_dim: true
    collapsed_label: 审稿  # 所有审稿行在「任务」维度折叠成一个 bucket
```

`run_daily.py work-items-sync` 把这些表拉进 SQL `work_items` 表,然后对
已有事件重建 `event_work_item_links`。

### `config/work_item_aliases.yaml` —— 手动 project → task 兜底

先 `cp config/work_item_aliases.example.yaml config/work_item_aliases.yaml`。

```yaml
aliases:
  "My project alias": rec_REPLACE_ME_1
  "Another alias":    rec_REPLACE_ME_2
```

`work_items.rebuild_links()` 在 URL/路径匹配不上时用这个。Dashboard 的
审计面板能把识别错的项目映射写到这里。

### `config/sources.yaml`, `config/rules.yaml`

prototype 时期留下的轻量默认 —— 项目别名、隐私开关、source 启用列表。
现在大部分行为在 per-device yaml 里,这俩留着是为了 `import_inbox.py` 的
向后兼容。

### `config/feishu_drive.yaml`(gitignored)

第一次跑 `export_report.py --upload-feishu` 时自动生成。存着自动创建
的 `DayTrace 报告 / { daily, weekly }/` 结构的 folder token。

## 3. Secrets

DeepSeek + Gmail 凭据在 `~/.daytrace/secrets.env`,chmod 600。**绝对不进 git**。

```env
# DeepSeek(AI 速读必需)
DEEPSEEK_API_KEY=sk-...

# 可选:Gmail SMTP 投递周报
DAYTRACE_GMAIL_USER=your-agent@gmail.com
DAYTRACE_GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx   # 16 位 app password,不是登录密码
DAYTRACE_EMAIL_TO=you@example.com

# 可选:Markdown / 邮件 / 飞书文档导出语言(独立于 dashboard 的语言切换)
# 默认 'en';要中文设为 'zh'
DAYTRACE_REPORT_LANG=zh
```

16 位 app password 在 <https://myaccount.google.com/apppasswords> 生成
(Google 要求账号开 2FA)。用一个**专门的 agent Gmail 账号** —— 密码落盘
的影响面就只到这一个邮箱。

`daytrace/ai_client.py` 在运行时会把这个文件 merge 进 `os.environ`,
所以 launchd 拉起的进程(它们不继承 shell profile)也能拿到
`DEEPSEEK_API_KEY`。

## 4. 第一次运行 —— catchup + dashboard

```bash
# 拉数据 + import + 重建昨天(顺便补任意 backfill)
python3 scripts/run_daily.py catchup --config config/devices/mac.yaml

# 启动本地 dashboard
python3 dashboard/server.py --db data/daytrace.sqlite --port 8765
open http://127.0.0.1:8765/today
```

## 5. 定时任务(macOS launchd)

`deploy/` 下三个 plist 模板:

| Job | 周期 | 脚本 |
|---|---|---|
| `com.daytrace.dashboard` | 始终在线(`KeepAlive=true`) | dashboard server 在 `127.0.0.1:8765` |
| `com.daytrace.daily`     | 每天 04:30 | `scripts/daytrace-daily.sh` → catchup + 飞书 |
| `com.daytrace.weekly`    | 每周一 06:00 | `scripts/daytrace-weekly.sh` → 周报 + 飞书 + Gmail |

安装 —— `scripts/install_launchd.sh` 会把 `deploy/*.plist.template` 里的
`__REPO__` / `__PYTHON__` 替换好,写到 `~/Library/LaunchAgents/`,然后
bootstrap 起来。脚本幂等(已加载的会先 unload)。

```bash
bash scripts/install_launchd.sh
launchctl list | grep daytrace   # 确认
```

手动触发(不等定时):

```bash
launchctl kickstart gui/$(id -u)/com.daytrace.daily
```

日志:`data/logs/{daily,weekly,dashboard}.log`。Mac 在触发时间睡着了
没关系,launchd 会在下次唤醒时跑。

卸载:

```bash
for label in dashboard daily weekly; do
  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.daytrace.${label}.plist
  rm ~/Library/LaunchAgents/com.daytrace.${label}.plist
done
```

## 6. 远程访问实时 dashboard(Tailscale Serve)

想从手机或另一台笔记本打开 `/today` / `/weekly`,把本地 dashboard 通过
Tailscale 暴露出去(只在你的 tailnet 内部 —— 不是公网):

```bash
# 一次性:在 Tailscale 后台开 Serve
#   登录 https://login.tailscale.com/admin/settings/features → 开 HTTPS

tailscale serve --bg --https=443 http://127.0.0.1:8765
tailscale serve status   # 确认
```

得到 `https://<your-mac>.<tailnet>.ts.net/`,你 tailnet 里任何设备都
能访问。`tailscale serve` 配置会跨重启持久化。

`report_delivery.dashboard_url()` 自动检测这个,并把 URL 加到周报邮件的
`🖥 完整 Dashboard` 链接里。

## 7. 投递 —— 飞书云文档 + Gmail

`secrets.env` + `lark-cli` 授权都到位后,每日 / 每周 job 会自动投递。
要手动触发一次:

```bash
# 只本地 Markdown,不上传不发邮件
python3 scripts/export_report.py --week 2026-W20

# 完整流水线:渲染 → 飞书云文档 → 邮件
python3 scripts/export_report.py --week 2026-W20 --upload-feishu --email

# 日报(默认不发邮件;周报负责投递)
python3 scripts/export_report.py --date 2026-05-17 --upload-feishu
```

每个 channel 产出什么:

- **飞书云文档**:Markdown 通过 `lark-cli drive +import` 导成原生 docx。
  图表随后用 `lark-cli docs +media-insert --selection-with-ellipsis=
  <图表标题>` 嵌入 —— 这个路径产生的 `image_token` 是持久的(`+import`
  那条路径嵌入的是约 1 小时过期的临时 stream URL —— 踩坑后改成现在
  这样)。存在 `DayTrace 报告 / { daily, weekly }/`。
- **Gmail**:multipart/alternative —— text/plain 兜底 + 富 HTML 正文 +
  PNG 图表用 `Content-ID` 内嵌。主题自动拼上 AI headline。顶部链接框包含:
  - `🖥 完整 Dashboard` —— Tailscale URL(Serve 在线时)
  - `📄 飞书文档` —— 本次跑的 docx URL

## 8. 维护工具

```bash
# 看看会删什么(dry-run)
python3 scripts/cleanup_feishu_reports.py

# 实际删除残留旧版本(每名只保留最新 docx;扔掉 v8 之前的 .html / .md
# 上传残留)
python3 scripts/cleanup_feishu_reports.py --apply
```
