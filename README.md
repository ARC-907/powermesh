# PowerMesh

<!-- markdownlint-disable MD060 -->

PowerMesh is a lightweight power-consumption monitor for workstations, homelab nodes, and private mesh deployments. It can run as a single-machine Lite app or as a collector plus multiple agents across a trusted network.

The app samples the best available local sensors, stores readings in SQLite, estimates wall power using PSU efficiency curves, and serves a browser dashboard with reports and export endpoints.

## Architecture

```text
  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
  │   Agent (A)   │   │   Agent (B)   │   │   Agent (C)   │
  │  nvidia-smi   │   │    RAPL       │   │  TDP est.    │
  │  psutil       │   │  psutil       │   │  psutil      │
  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
         │                  │                   │
         │  HTTP POST /api/power/ingest (HMAC)  │
         └──────────────────┼───────────────────┘
                            ▼
                   ┌────────────────┐
                   │   Collector    │
                   │   :8430        │
                   │  SQLite + API  │
                   │  Dashboard     │
                   └────────────────┘
```

Each node runs an agent that samples power via the best available sensor (`nvidia-smi` -> RAPL -> TDP estimation -> optional smart plug). Readings are pushed to a central collector over the private network and can be authenticated with HMAC-SHA256.

## Editions

| Edition | Use case                                         | Command                                           |
| ------- | ------------------------------------------------ | ------------------------------------------------- |
| Lite    | One machine, local dashboard, portfolio demo     | `powermesh-lite`                                  |
| Full    | Multi-node mesh with a central collector         | `powermesh-collector` + `powermesh-agent`         |

Lite mode binds to `127.0.0.1`, starts an embedded collector and agent in one process, and opens the dashboard in your browser.

## What Gets Measured

| Metric                            | Source                                    |
| --------------------------------- | ----------------------------------------- |
| GPU power, temp, VRAM, utilization | nvidia-smi                                |
| CPU power                         | RAPL (Linux) or TDP estimation            |
| RAM, disk I/O, network I/O        | psutil                                    |
| Wall power (optional)             | Kasa / Shelly smart plug                  |
| PSU efficiency                    | 80 Plus rating curves (bronze—titanium)   |

**Power formula:** `P_wall = (P_cpu + P_gpu + P_base) / eta_psu`

## Quick Start

```bash
# Install from the checkout
python -m pip install -e .[dev]

# Check the environment
powermesh-doctor

# Run the local all-in-one app
powermesh-lite
```

Open <http://127.0.0.1:8430/> if the browser does not open automatically.

### Full Mesh Mode

```bash
# Start collector on the central node
powermesh-collector config/mesh.yaml

# Start agent on each monitored node
powermesh-agent config/node.yaml
```

You can also use the module entry points during development:

```bash
python -m src.collector config/mesh.yaml
python -m src.agent config/node.yaml
```

## Configuration

- `config/node.yaml` — per-node agent settings (TDP, PSU rating, collector URL)
- `config/mesh.yaml` — collector settings (auth tokens, expected nodes, retention)
- `.env.example` — supported environment overrides

Common environment overrides:

| Variable                       | Purpose                        |
| ------------------------------ | ------------------------------ |
| `POWERMESH_DATA_DIR`           | Runtime database/log directory |
| `POWERMESH_COLLECTOR_PORT`     | Collector HTTP port            |
| `POWERMESH_HOST`               | Collector bind address         |
| `POWERMESH_NODE_ID`            | Agent node identifier          |
| `POWERMESH_COLLECTOR_URL`      | Agent upload target            |
| `POWERMESH_AUTH_TOKEN`         | Agent HMAC token               |

Do not commit real tokens. Use `config/*.local.yaml`, `.env`, or user config files for private settings.

## Deployment

### Windows

```powershell
.\scripts\install-agent.ps1             # Agent node
.\scripts\install-collector.ps1          # Collector node
```

### Linux

```bash
sudo bash scripts/install-agent.sh      # Creates systemd service
```

Collector installation for Linux is available in `scripts/install-collector.sh`.

## Dashboard

The built-in dashboard includes:

- live mesh summary cards
- per-node power, utilization, and last-seen status
- pause/resume auto-refresh
- aggregate recomputation
- CSV/JSON export links
- printable report view at `/report`
- settings view at `/settings`

Settings writes are restricted to loopback clients. For a public or shared network deployment, keep the collector behind Tailscale, VPN, or another trusted network boundary.

## API Endpoints

| Route                                      | Description                                      |
| ------------------------------------------ | ------------------------------------------------ |
| `GET /`                                    | HTML dashboard                                   |
| `GET /settings`                            | HTML settings view                               |
| `GET /report`                              | Printable HTML report                            |
| `GET /api/health`                          | Node count                                       |
| `GET /api/settings`                        | Effective collector settings with secrets masked |
| `GET /api/export?format=csv&range=24h`     | CSV readings export                              |
| `GET /api/export?format=json&range=24h`    | JSON readings export                             |
| `GET /api/mesh/summary`                    | Total power, kWh/day, cost                       |
| `GET /api/nodes`                           | All nodes with latest readings                   |
| `GET /api/node/latest?node_id=X`           | Latest reading for a node                        |
| `GET /api/node/history?node_id=X&limit=N`  | Historical readings                              |
| `GET /api/aggregates?node_id=X&period=hourly` | Hourly/daily summaries                        |
| `GET /api/cost?period=daily&days=30`       | Cost breakdown by node                           |
| `POST /api/power/ingest`                   | Agent upload endpoint                            |
| `POST /api/refresh`                        | Recompute hourly/daily aggregates                |
| `POST /api/settings`                       | Save local settings overrides from loopback only |

Supported export ranges: `1h`, `24h`, `7d`, `30d`.

## Live Test

Run a short real-sensor capture and generate a local report:

```bash
python scripts/live_test.py --cycles 3 --interval 5
```

Reports are written to the per-user PowerMesh reports directory, not the repository root. Optional webhook posting uses `POWERMESH_DEV_WEBHOOK_URL` and `--post-to-webhook`.

## Testing

```bash
python -m pip install -e .[dev]
python -m pytest tests/ -v
```

The current test suite covers sensors, SQLite storage, aggregation, config loading, and export/refresh API behavior.

## Security Notes

- Use HMAC tokens for multi-node deployments.
- Keep the Full collector on a trusted network.
- Never commit `.env`, generated SQLite databases, local history, or real hostnames.
- Root generated reports and `data/live_test/` are ignored by default.

## VS Code Tasks

Open the command palette → **Tasks: Run Task** to see PowerMesh tasks for starting the agent/collector, querying health, and running tests.
