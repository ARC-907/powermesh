# Contributing

Thanks for improving PowerMesh.

## Development Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
python -m pytest tests/ -v
```

On Linux or macOS, activate the virtual environment with `source .venv/bin/activate`.

## Local Checks

Run these before opening a pull request:

```bash
python -m pytest tests/ -v
powermesh-doctor
```

## Security and Privacy

- Do not commit `.env` files, local SQLite databases, generated reports, or real hostnames.
- Use clearly fictional fixtures in tests and docs.
- Keep generated screenshots sanitized.
