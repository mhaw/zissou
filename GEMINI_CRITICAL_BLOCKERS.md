# Critical Deployment Blockers

This document outlines the most critical issues that could block a successful and stable deployment of the Zissou application.

## 1. Configuration Sprawl

*   **Blocker:** Configuration is scattered across `pydantic-settings`, direct `os.getenv()` calls, and the `run.sh` script. This creates a high risk of misconfiguration during deployment, leading to application instability or startup failures.
*   **Fix:** Consolidate all configuration into the `AppSettings` class in `app/config.py`. Remove all `os.getenv()` calls from the application and rely on the `settings` object.

## 2. Redundant and Error-Prone `run.sh` Script

*   **Blocker:** The `run.sh` script manually passes a long list of environment variables to `gunicorn`. This is redundant when using `pydantic-settings` and is a common source of deployment failures when a variable is missed or incorrect.
*   **Fix:** Remove the `--env` flags from the `gunicorn` command in `run.sh`. Let `pydantic-settings` handle the loading of environment variables automatically.

## 3. Inefficient Docker Caching

*   **Blocker:** The `Dockerfile` copies the entire application in a single `COPY` command. This invalidates the Docker cache on every code change, leading to slow build times and potentially delaying deployments.
*   **Fix:** Optimize the `Dockerfile` to copy the `requirements.txt` file and install dependencies *before* copying the rest of the application code. This will allow Docker to cache the dependencies layer and significantly speed up builds.
