# Security Policy

PowerMesh ships as **loopback-only by default**. The Full collector refuses to bind a non-loopback address on first run; the Lite edition pins `127.0.0.1` and cannot be reconfigured to expose itself.

## First-Run Posture

- **Default bind:** `127.0.0.1` (loopback) on port `8430`.
- **Default auth:** none required (loopback only — the OS gates access).
- **Settings writes (`POST /api/settings`):** restricted to loopback clients.
- The shipped `config/mesh.yaml` mirrors these defaults.

If `host` is set to anything other than `127.0.0.1` / `localhost` / `::1`, the collector will **refuse to start** unless **both** of the following are true:

1. The `--public` flag was passed on the command line (or `public=True` was passed to `run_collector()` programmatically).
2. The `auth_tokens` map in `mesh.yaml` is non-empty.

A missing flag produces:

```
ERROR: Non-loopback bind (<host>) requires explicit opt-in.
See SECURITY.md and README.md "Public/LAN deployment" for how to set this up.
Refusing to start.
```

A missing token map produces:

```
ERROR: Non-loopback bind (<host>) requires auth_tokens to be configured.
See SECURITY.md and README.md "Public/LAN deployment" for how to set this up.
Refusing to start.
```

In both cases the process exits with status `2`.

## Public / LAN / Tailscale Deployment (Opt-In)

To run the Full collector across more than one host (e.g. behind Tailscale, on a homelab LAN, or on a trusted office network):

1. Generate a strong random secret. A 32-byte hex string is fine:

   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

2. Edit `mesh.yaml`:

   ```yaml
   # The address the collector binds. Loopback by default; set to a routable
   # address (e.g. your Tailscale IP, or 0.0.0.0 if you know what you are doing)
   # only when --public is also passed.
   host: "100.x.y.z"        # e.g. Tailscale interface

   auth_tokens:
     "*": "<the secret you generated>"
     # Or, per-node:
     # desktop-01: "<per-node secret>"
   ```

3. Configure the agent with the matching token. Either edit `node.yaml` or set:

   ```bash
   export POWERMESH_AUTH_TOKEN="<the secret you generated>"
   ```

4. Start the collector with the explicit opt-in:

   ```bash
   powermesh-collector config/mesh.yaml --public
   ```

   Or programmatically:

   ```python
   from src.collector import run_collector
   run_collector(config_path="config/mesh.yaml", public=True)
   ```

5. Lock the collector behind a trusted network boundary (Tailscale ACLs, VPN, firewall rules). The HMAC check protects writes; the bind itself is still exposed to anything that can route to it.

## Authentication Model — Current Scope and Limits

`/api/power/ingest` accepts an `X-PowerMesh-Signature` header containing an HMAC-SHA256 of the JSON body. Signature comparison uses `hmac.compare_digest` (constant-time).

**Known limitation:** the signed envelope is the payload body only — there is no timestamp or nonce, so a captured request is replayable indefinitely until the shared secret is rotated. Adding `timestamp` + `nonce` to the signed envelope (with a recency window and nonce-dedup table) is planned. Until then, treat the HMAC as integrity-only, not freshness.

GET endpoints (`/api/export`, `/api/nodes`, `/api/cost`, `/api/aggregates`, `/api/settings`, etc.) are unauthenticated. They rely entirely on the trusted-network boundary; do not expose the collector to anything you would not also trust to read your power-usage timeseries.

`POST /api/settings` is gated to loopback clients regardless of bind address.

## Operational Notes

- Keep `auth_tokens` out of committed config files. Use `config/mesh.local.yaml`, `.env` (see `.env.example`), or your secrets manager.
- Rotate shared tokens if a config file, log, or shell history exposes one.
- Generated SQLite databases, local history, and `.env` files are excluded by `.gitignore`.
- The masked-config view (`GET /api/settings`) and the redacting log filter both scrub `auth_token`, `auth_tokens`, `token`, `password`, and `secret` keys before exposure.

## Reporting Issues

Open a private security advisory on the repository, or contact the maintainer directly. Please do not file public issues for unpatched vulnerabilities.
