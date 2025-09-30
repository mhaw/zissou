# Makefile for Zissou

.PHONY: all dev setup install fmt lint test clean build run deploy help

# Variables
PYTHON = python3
VENV_DIR = .venv
VENV_ACTIVATE = source $(VENV_DIR)/bin/activate
PIP = $(VENV_DIR)/bin/pip
FLASK = $(VENV_DIR)/bin/flask
APP_NAME = zissou
AR_REPOSITORY = zissou-repo
GCP_PROJECT_ID ?= $(shell grep GCP_PROJECT_ID .env | cut -d '=' -f2 | xargs)
GCP_REGION ?= $(shell grep GCP_REGION .env | cut -d '=' -f2)
GCS_BUCKET ?= $(shell grep GCS_BUCKET .env | cut -d '=' -f2)
IMAGE_REGISTRY = $(GCP_REGION)-docker.pkg.dev/$(GCP_PROJECT_ID)/$(AR_REPOSITORY)
GIT_SHA = $(shell git rev-parse --short HEAD 2>/dev/null || /bin/date +%s)
IMAGE_TAG = $(IMAGE_REGISTRY)/$(APP_NAME):$(GIT_SHA)
LATEST_TAG = $(IMAGE_REGISTRY)/$(APP_NAME):latest
BLACK = $(VENV_DIR)/bin/black
RUFF = $(VENV_DIR)/bin/ruff
MYPY = $(VENV_DIR)/bin/mypy
PYTEST = $(VENV_DIR)/bin/pytest
PYTHON_BIN = $(VENV_DIR)/bin/python

help:
	@echo "Commands for Zissou:"
	@echo "  setup          - Create virtual environment and install dependencies"
	@echo "  install        - Install dependencies from requirements.txt"
	@echo "  fmt            - Format code with black and ruff"
	@echo "  lint           - Lint code with ruff"
	@echo "  typecheck      - Run type checks with mypy"
	@echo "  test           - Run tests with pytest"
	@echo "  dev            - Run Flask development server"
	@echo "  build          - Build Docker image (tags commit and latest)"
	@echo "  run            - Run application with Docker Compose"
	@echo "  deploy         - Deploy to Google Cloud Run"
	@echo "  cloud-setup    - Run initial infrastructure setup on GCP"
	@echo "  clean          - Remove virtual environment and pycache"

# Development
setup: $(VENV_DIR)/bin/activate
$(VENV_DIR)/bin/activate: requirements.txt
	$(PYTHON) -m venv $(VENV_DIR)
	$(PIP) install -r requirements.txt
	@echo "Virtual environment created and dependencies installed."
	@echo "Run 'source .venv/bin/activate' to activate."

install:
	$(PIP) install -r requirements.txt

dev: $(VENV_DIR)/bin/activate
	@echo "Starting Flask development server..."
	@cp -n .env.example .env || true
	$(PYTHON_BIN) -m dotenv run -- FLASK_APP=app/main.py $(FLASK) run -p 8080

# Quality & Testing
fmt: $(VENV_DIR)/bin/activate
	$(BLACK) .
	$(RUFF) check --fix .

lint: $(VENV_DIR)/bin/activate
	$(RUFF) check .

typecheck: $(VENV_DIR)/bin/activate
	$(MYPY) app/

test: $(VENV_DIR)/bin/activate
	$(PYTEST)

# Docker & Deployment
build:
	@echo "Building Docker image: $(IMAGE_TAG)..."
	docker build --platform linux/amd64 -t $(IMAGE_TAG) .
	docker tag $(IMAGE_TAG) $(LATEST_TAG)
	@echo "Tagged image as: $(LATEST_TAG)"

run:
	. ./.venv/bin/activate && gunicorn --bind 0.0.0.0:8080 --workers 2 --threads 4 --worker-class gthread app.main:app

grant-admin-role: $(VENV_DIR)/bin/activate
	@$(PYTHON_BIN) tools/manage_user.py --email $(email) --role admin

deploy:
	@echo "Deploying to Cloud Run..."
	./infra/deploy_cloud_run.sh

cloud-setup:
	@echo "Setting up GCP infrastructure..."
	./infra/setup.sh

# Cleanup
clean:
	rm -rf $(VENV_DIR)
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
