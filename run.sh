#!/bin/sh

# Start the web server
gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4 --worker-class gthread app.main:app
