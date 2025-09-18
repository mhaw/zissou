# Repository Guidelines

## Project Structure & Module Organization
Zissou ships as a Flask service; `app/main.py` boots the app and blueprints are kept in `app/routes`. Models and Firestore schemas live in `app/models`, service adapters in `app/services`, helpers in `app/utils`, and presentation assets in `app/templates` plus `app/static`. Tests mirror behaviour under `tests/`, while `infra/` holds deployment scripts, `docs/` captures architectural decisions, and `tools/validate_feed.py` checks RSS output.

## Build, Test, and Development Commands
- `make setup` provisions the `.venv` and installs `requirements.txt`.
- `make dev` loads `.env` defaults and runs the Flask server on `http://localhost:8080` (equivalent to `flask --app app/main.py run -p 8080`).
- `make fmt` and `make lint` wrap Black and Ruff; run both before submitting changes.
- `make test` executes pytest; `make build` and `make run` cover container builds and Docker Compose smoke tests; use `./infra/setup.sh` once per project and `./infra/deploy_cloud_run.sh` to ship to Cloud Run.

## Coding Style & Naming Conventions
Target Python 3.11 with 4-space indentation and type hints at service boundaries. Keep modules and functions in `snake_case`, data models in `PascalCase`, and template partials in `kebab-case`. Always run `make fmt`; Ruff enforces import order, unused symbols, and docstring hygiene—fix warnings rather than silencing them.

## Testing Guidelines
Pytest suites live in `tests/test_*.py`; mirror new behaviours beside `test_parser.py` and `test_feeds.py`. Name cases `test_<feature>_<expectation>`. Use `make test` before pushing, and `pytest tests/test_parser.py -k chunking` for focused runs. For RSS updates, backstop with `python tools/validate_feed.py` against a staged feed.

## Commit & Pull Request Guidelines
This checkout lacks `.git`, but upstream commits use short imperative subjects with optional scopes (e.g., `tasks: reuse shared TTS client`). Keep bodies under 72 characters per line, noting context, migrations, or roll-out steps. PRs should link issues, summarize user-visible change, flag environment updates, and attach UI screenshots when templates shift. Update `CHANGELOG.md` for anything user-facing.

## Configuration & Secrets
Copy `.env.example` to `.env`, fill in GCP project, bucket, and service account values, and set `ENV=development` for synchronous local work. Run `gcloud auth login` before infra scripts. Do not commit secrets; prefer Secret Manager or local environment injection.
