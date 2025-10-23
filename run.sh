#!/bin/sh

# Start the web server
WORKERS=${GUNICORN_WORKERS:-1}
THREADS=${GUNICORN_THREADS:-4}
exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers "${WORKERS}" --threads "${THREADS}" --worker-class gthread app.main:app
