# Serein Core

The always-on local core for SereinOS.

## Development

This package uses a `src/` layout:

```text
serein-core/
├── src/serein_core/
├── tests/
└── pyproject.toml
```

Install the package in editable mode:

```powershell
python -m pip install -e . --no-deps
```

Run tests:

```powershell
python -m pytest -q
```

Local inference is optional at install time. The runtime uses an installed GGUF model
and `llama-cpp-python` when available; no paid API is required.
