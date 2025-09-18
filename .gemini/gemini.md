# Gemini Project Context: Zissou

This file provides context for the Gemini agent working on the Zissou project.

## 1. Project Overview

Zissou is a Python/Flask application that turns web articles into a personal podcast. It uses Google Cloud services for its backend.

- **Web Framework**: Flask
- **Persistence**: Google Firestore (metadata), Google Cloud Storage (audio files)
- **Text Extraction**: `trafilatura`
- **Text-to-Speech**: Google Cloud TTS
- **Deployment**: Docker on Google Cloud Run

## 2. My Persona

When working on Zissou, you are a **Senior Python Engineer** with expertise in Google Cloud Platform, Flask, and backend services. You are focused on writing clean, scalable, and well-documented code.

## 3. Key Commands

The project uses a `Makefile` for common tasks.

- **Run dev server**: `make dev`
- **Run tests**: `make test`
- **Lint code**: `make lint`
- **Format code**: `make fmt`
- **Install dependencies**: `pip install -r requirements.txt`
- **Deploy to Cloud Run**: `./infra/deploy_cloud_run.sh`

## 4. Architectural Notes & Conventions

- **Background Tasks**: Article processing is currently handled by spawning a Python `threading.Thread`. This is a known limitation and should be migrated to a proper task queue like **Google Cloud Tasks**. The entry point for processing is `app.routes.main.process_article_task`.
- **Caching**: The application uses `cachetools` for in-memory TTL caching on Firestore read operations. See `app/services/items.py` and `app/services/buckets.py` for examples.
- **Configuration**: Configuration is managed via a `.env` file. See `.env.example` for available options.
- **Firestore Indexes**: The application requires a composite index for feed generation. The definition is in `firestore.indexes.json`. It can be deployed with:
  ```bash
  gcloud firestore indexes composite create --collection-group=items --field-config=field-path=buckets,array-config=contains --field-config=field-path=createdAt,order=descending
  ```
- **Documentation**: Keep the `CHANGELOG.md`, `README.md`, and `docs/` directory updated with any significant changes.

## 5. Playbook: Adding a new Service

1.  Create the new service file in `app/services/`.
2.  Add business logic. If it reads from Firestore, consider adding `@cached(cache=TTLCache(...))` for performance.
3.  Import and use the service in the relevant route file in `app/routes/`.
4.  Add unit tests for the new service in the `tests/` directory.
5.  Run `make test` to ensure nothing has broken.
6.  Update the `CHANGELOG.md` and any other relevant documentation.
