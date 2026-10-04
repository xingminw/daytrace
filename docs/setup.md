# Local setup

Use Python 3.10 or later and install `requirements.txt` into `.venv`. Copy
`config/devices/mac-local.example.yaml` to the gitignored `mac-local.yaml` and edit
source paths. Configure index roots and aliases in `config/projects.yaml`.

- `make daily`: local collection with backup, idempotent import and statistics.
- `make sync-projects`: back up and refresh local repository attribution.
- `make dashboard`: foreground website at `127.0.0.1:8766`, with AI disabled.
- `bash scripts/install_launchd.sh`: install only the loopback dashboard using a
  launcher under `~/.local/bin` and logs under `~/Library/Logs/daytrace/`.

Keep the checkout volume mounted. Installing scheduled collection is a separate
explicit action: `scripts/install_daily_collection.py` uses 04:30 Asia/Shanghai
and refuses a different host timezone. It runs local collection followed by any
explicitly configured SSH source; failures are skipped and AI stays disabled.
Do not run installers just to inspect configuration.

Tailscale access requires a separately reviewed Serve/binding configuration.
These installation scripts do not modify Serve, ACLs or Funnel. Do not expose
private trace data publicly. The dashboard does not provide its own login.

The optional interactive `scripts/configure_deepseek.py` stores an existing key
locally without echoing it. It does not call the API or enable scheduled AI.
Generating a report requires an exact plan and budget via the
[evidence-brief CLI](evidence-briefs.md). Local collection and reading saved
reports do not need an API key.
