# Security Policy

PowerMesh is designed for trusted private networks such as a Tailscale mesh or localhost-only Lite mode.

## Supported Use

- Bind Full collectors only on trusted networks.
- Use HMAC authentication for multi-node deployments.
- Keep `auth_tokens` out of committed config files.
- Rotate shared tokens if a config file or log exposes one.

## Reporting Issues

Open a private security advisory or contact the maintainer directly if you find a vulnerability.

## Notes

- `/api/power/ingest` supports HMAC-SHA256 signatures.
- Settings writes are restricted to loopback clients.
- Generated databases, local history, logs, and `.env` files are ignored by default.
