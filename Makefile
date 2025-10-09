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
	@echo "  test:e2e-auth  - Run Playwright E2E authentication tests"
	@echo "  dev            - Run Flask development server"
	@echo "  build          - Build Docker image (tags commit and latest)"
	@echo "  run            - Run application with Docker Compose"
	@echo "  deploy         - Deploy to Google Cloud Run"
	@echo "  cloud-setup    - Run initial infrastructure setup on GCP"
	@echo "  auth:diag      - Display current authentication configuration"
	@echo "  auth:emulators - Instructions for running Firebase emulators"
	@echo "  clean          - Remove virtual environment and pycache"

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
PLAYWRIGHT = npx playwright

# Development
setup: $(VENV_DIR)/bin/activate
$(VENV_DIR)/bin/activate: requirements.txt
	$(PYTHON) -m venv $(VENV_DIR)
	$(PIP) install -r requirements.txt
	@echo "Virtual environment created and dependencies installed."
	@echo "Run 'source .venv/bin/activate' to activate."

install:
	$(PIP) install -r requirements.txt

dev: validate $(VENV_DIR)/bin/activate
	@echo "Starting Flask development server..."
	@cp -n .env.example .env || true
	$(PYTHON_BIN) -m dotenv run -- $(FLASK) --app app/main.py run -p 8080

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

test:e2e-auth:
	@echo "Running Playwright E2E authentication tests..."
	cd tests/e2e && $(PLAYWRIGHT) test login_flow.spec.js # Assuming a specific test file

# Authentication
auth:diag:
	@echo "--- Authentication Configuration ---"
	@echo "AUTH_ENABLED: $(shell grep AUTH_ENABLED .env | cut -d '=' -f2)"
	@echo "AUTH_BACKEND: $(shell grep AUTH_BACKEND .env | cut -d '=' -f2)"
	@echo "FIREBASE_PROJECT_ID: $(shell grep FIREBASE_PROJECT_ID .env | cut -d '=' -f2)"
	@echo "FIREBASE_WEB_API_KEY: $(shell grep FIREBASE_WEB_API_KEY .env | cut -d '=' -f2)"
	@echo "FIREBASE_AUTH_DOMAIN: $(shell grep FIREBASE_AUTH_DOMAIN .env | cut -d '=' -f2)"
	@echo "ADMIN_EMAILS: $(shell grep ADMIN_EMAILS .env | cut -d '=' -f2)"
	@echo "ALLOWED_ORIGINS: $(shell grep ALLOWED_ORIGINS .env | cut -d '=' -f2)"
	@echo "CANONICAL_HOST: $(shell grep CANONICAL_HOST .env | cut -d '=' -f2)"
	@echo "CSP_ADDITIONAL_CONNECT_SRC: $(shell grep CSP_ADDITIONAL_CONNECT_SRC .env | cut -d '=' -f2)"
	@echo "CSP_ADDITIONAL_SCRIPT_SRC: $(shell grep CSP_ADDITIONAL_SCRIPT_SRC .env | cut -d '=' -f2)"
	@echo "CSP_ADDITIONAL_STYLE_SRC: $(shell grep CSP_ADDITIONAL_STYLE_SRC .env | cut -d '=' -f2)"
	@echo "CSP_REPORT_URI: $(shell grep CSP_REPORT_URI .env | cut -d '=' -f2)"
	@echo "CSP_REPORT_TO_ENDPOINT: $(shell grep CSP_REPORT_TO_ENDPOINT .env | cut -d '=' -f2)"
	@echo "------------------------------------"

auth:emulators:
	@echo "To run Firebase Emulators:"
	@echo "1. Make sure you have the Firebase CLI installed: npm install -g firebase-tools"
	@echo "2. Navigate to your Firebase project directory (where firebase.json is located)."
	@echo "3. Run: firebase emulators:start --only auth,firestore"
	@echo "4. Configure your .env to point to the emulators (e.g., FIREBASE_AUTH_EMULATOR_HOST, FIRESTORE_EMULATOR_HOST)."

# Docker & Deployment
build:
	@echo "Building Docker image: $(IMAGE_TAG)..."
	docker build --platform linux/amd64 -t $(IMAGE_TAG) .
	docker tag $(IMAGE_TAG) $(LATEST_TAG)
	@echo "Tagged image as: $(LATEST_TAG)"

validate:
	@./validate_env.sh

run: validate
	. ./.venv/bin/activate && gunicorn --bind 0.0.0.0:8080 --workers 2 --threads 4 --worker-class gthread app.main:app

grant-admin-role: $(VENV_DIR)/bin/activate
	@$(PYTHON_BIN) tools/set_admin_claim.py --email $(email) --admin

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
