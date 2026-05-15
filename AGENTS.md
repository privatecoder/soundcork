# Repository Guidelines

## Project Structure & Module Organization
Core application code lives in `soundcork/`. `soundcork/main.py` boots the FastAPI app and wires the Marge, BMX, admin, and miniapp routes. Keep API logic in focused modules such as `bmx.py`, `marge.py`, `groups_service.py`, and `datastore.py`. UI templates and assets live in `soundcork/templates/`, `soundcork/static/`, and `soundcork/media/`. Tests belong in `soundcork/tests/`. Longer technical notes and deployment docs belong in `docs/`.

## Build, Test, and Development Commands
Use Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
fastapi dev soundcork/main.py
pytest
black --target-version py312 .
isort .
mypy .
python -m build
```

`fastapi dev` starts the local server on `http://127.0.0.1:8000`; `/docs` exposes the OpenAPI UI. `pytest` includes coverage reporting by default from `pyproject.toml`. Docker deployments should use host networking so UPnP discovery and speaker callbacks work on the LAN.

## Coding Style & Naming Conventions
Follow Black formatting and isort import order; do not hand-format around them. Use type hints on new or changed Python code. Prefer small, single-purpose functions and keep Bose-compatible response shapes exact, especially for XML endpoints. Use `snake_case` for modules, functions, and variables; use `PascalCase` for classes and Pydantic models.

## Testing Guidelines
Add or update pytest coverage for every behavior change. Place tests in `soundcork/tests/` and name files `test_<module>.py`. Favor targeted unit tests around parsing, datastore behavior, and response generation. Run a focused test file during iteration, for example `pytest soundcork/tests/test_datastore.py`.

## Commit & Pull Request Guidelines
Recent history favors short imperative subjects, often with a PR reference, for example `Fix miniapp preset playback and cookies (#311)`. Keep commits scoped to one change. Pull requests should explain the behavior change, note any config or protocol impact, link the issue when relevant, and include screenshots for UI changes.

## Security & Configuration Tips
Never commit secrets or local paths. Store overrides in `soundcork/.env.private`; common defaults belong in `.env.shared`. Key settings are `BASE_URL` and `DATA_DIR`. `BASE_URL` must be reachable from the speakers, not just the host. Read `SECURITY.md` before changing networking behavior: this service is intended for trusted home-network deployment behind a firewall.
