#!/bin/bash
# Optimized deploy script for Google Cloud Run.

set -euo pipefail
export PATH="/bin:/usr/bin:/usr/sbin:${PATH:-}"

LOG_FILE=${LOG_FILE:-deploy.log}
ENV_FILE=""
LOG_PIPE=""
TEE_PID=""

cleanup() {
    local status=$?
    if [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
        rm -f "$ENV_FILE"
    fi
    if [ -n "$LOG_PIPE" ] && [ -p "$LOG_PIPE" ]; then
        # Closing STDOUT/ERR before waiting ensures tee exits cleanly.
        exec >&-
        /bin/rm -f "$LOG_PIPE"
    fi
    if [ -n "$TEE_PID" ]; then
        wait "$TEE_PID" 2>/dev/null || true
    fi
    exit "$status"
}
trap cleanup EXIT

/usr/bin/touch "$LOG_FILE"
LOG_PIPE=$(/usr/bin/mktemp -u /tmp/zissou-deploy.XXXX.pipe)
/usr/bin/mkfifo "$LOG_PIPE"
/usr/bin/tee -a "$LOG_FILE" <"$LOG_PIPE" &
TEE_PID=$!
exec >"$LOG_PIPE" 2>&1

log() {
    printf '[%s] %s\n' "$(/bin/date '+%Y-%m-%d %H:%M:%S%z')" "$*"
}

section() {
    printf '\n[%s] === %s ===\n' "$(/bin/date '+%Y-%m-%d %H:%M:%S%z')" "$*"
}

section "Initializing deploy"

if [ -f .env.prod ]; then
    log "Loading environment variables from .env.prod"
    set -a
    # shellcheck disable=SC1091
    . ./.env.prod
    set +a
elif [ -f .env ]; then
    log "Loading environment variables from .env"
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
    log "DEBUG: GCP_PROJECT_ID after sourcing .env: ${GCP_PROJECT_ID}"
    log "DEBUG: GCP_REGION after sourcing .env: ${GCP_REGION}"
else
    log ".env file not found; relying on ambient environment"
fi

REQUIRED_VARS=(
    GCP_PROJECT_ID
    GCP_REGION
    GCS_BUCKET
    SECRET_KEY
    CLOUD_TASKS_QUEUE
    CLOUD_TASKS_LOCATION
    SERVICE_ACCOUNT_EMAIL
)

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var:-}" ]; then
        log "Error: environment variable $var is required"
        exit 1
    fi
done

APP_NAME=${APP_NAME:-zissou}
APP_NAME_SLUG=$(echo "${APP_NAME}" | tr '[:upper:]' '[:lower:]')
APP_NAME_SLUG=$(echo "${APP_NAME_SLUG}" | tr -c 'a-z0-9-' '-')
APP_NAME_SLUG=${APP_NAME_SLUG#-}
APP_NAME_SLUG=${APP_NAME_SLUG%-}
if [ -z "${APP_NAME_SLUG}" ]; then
    APP_NAME_SLUG=zissou
fi
SERVICE_NAME=${SERVICE_NAME:-$APP_NAME_SLUG}
: "${TTS_VOICE:=}"
: "${SERVICE_URL:=}"
: "${CLOUD_RUN_MEMORY:=1024Mi}"
: "${CLOUD_RUN_CPU:=2}"
: "${CLOUD_RUN_MAX_INSTANCES:=5}"
: "${CLOUD_RUN_MIN_INSTANCES:=0}"
: "${CLOUD_RUN_CONCURRENCY:=40}"
: "${CLOUD_RUN_TIMEOUT:=480}"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    GIT_SHA=${GIT_SHA:-$(git rev-parse --short HEAD)}
else
    GIT_SHA=${GIT_SHA:-$(/bin/date +%s)}
fi

REGISTRY_HOST="${GCP_REGION}-docker.pkg.dev"
AR_REPOSITORY="zissou-repo"
IMAGE_PATH="${REGISTRY_HOST}/${GCP_PROJECT_ID}/${AR_REPOSITORY}/${APP_NAME_SLUG}"
IMAGE_TAG="${IMAGE_PATH}:${GIT_SHA}"
LATEST_TAG="${IMAGE_PATH}:latest"
REVISION_SUFFIX=$(printf '%s-%s' "$APP_NAME_SLUG" "$GIT_SHA" | tr '[:upper:]' '[:lower:]')
REVISION_SUFFIX=${REVISION_SUFFIX//_/}
REVISION_SUFFIX=${REVISION_SUFFIX:0:62}
REVISION_SUFFIX=${REVISION_SUFFIX%-}
USE_REVISION_SUFFIX=true

if [ -z "$REVISION_SUFFIX" ]; then
    USE_REVISION_SUFFIX=false
else
    FULL_REVISION_NAME="${SERVICE_NAME}-${REVISION_SUFFIX}"
    if gcloud run revisions describe "$FULL_REVISION_NAME" \
        --platform=managed \
        --region="$GCP_REGION" \
        --project="$GCP_PROJECT_ID" >/dev/null 2>&1; then
        EXTRA_TAG=$(/bin/date +%Y%m%d%H%M%S)
        CANDIDATE_SUFFIX=$(printf '%s-%s' "$REVISION_SUFFIX" "$EXTRA_TAG")
        CANDIDATE_SUFFIX=${CANDIDATE_SUFFIX:0:62}
        CANDIDATE_SUFFIX=${CANDIDATE_SUFFIX%-}
        if [ -n "$CANDIDATE_SUFFIX" ] && [ "$CANDIDATE_SUFFIX" != "$REVISION_SUFFIX" ]; then
            log "Revision suffix '$REVISION_SUFFIX' exists; using '$CANDIDATE_SUFFIX' instead"
            REVISION_SUFFIX="$CANDIDATE_SUFFIX"
        else
            log "Revision suffix '$REVISION_SUFFIX' exists and cannot be safely adjusted; allowing Cloud Run to auto-generate"
            USE_REVISION_SUFFIX=false
        fi
    fi
fi

section "Pre-flight checks"

if ! gcloud artifacts repositories describe "$AR_REPOSITORY" --location="$GCP_REGION" --project="$GCP_PROJECT_ID" >/dev/null 2>&1; then
    log "Artifact Registry repository '$AR_REPOSITORY' not found in $GCP_REGION. Run 'make cloud-setup' first."
    exit 2
fi

if [ -z "$SERVICE_URL" ]; then
    EXISTING_URL=$(gcloud run services describe "$SERVICE_NAME" \
        --platform=managed \
        --region="$GCP_REGION" \
        --project="$GCP_PROJECT_ID" \
        --format='value(status.url)' 2>/dev/null || true)
    if [ -n "$EXISTING_URL" ]; then
        SERVICE_URL="$EXISTING_URL"
        log "Discovered existing service URL: $SERVICE_URL"
    fi
fi

log "Authenticating Docker client for ${REGISTRY_HOST}"
gcloud auth configure-docker "$REGISTRY_HOST" --quiet

section "Building container image"
export DOCKER_BUILDKIT=${DOCKER_BUILDKIT:-1}
export BUILDKIT_PROGRESS=${BUILDKIT_PROGRESS:-plain}

if [ "${SKIP_BUILD:-false}" = "true" ]; then
    log "SKIP_BUILD is true, skipping Docker image build and push."
else
    log "Building image $IMAGE_TAG"
    docker build --platform linux/amd64 -t "$IMAGE_TAG" .
    docker tag "$IMAGE_TAG" "$LATEST_TAG"

    log "Pushing image tags"
    docker push "$IMAGE_TAG"
    docker push "$LATEST_TAG"
fi

section "Preparing runtime configuration"
ENV_FILE=$(/usr/bin/mktemp -t zissou-prod-env.XXXX.yaml)

: "${AUTH_BACKEND:=iap}"
: "${AUTH_ENABLED:=false}"
: "${FIREBASE_PROJECT_ID:=}"
: "${FIREBASE_WEB_API_KEY:=}"
: "${FIREBASE_AUTH_DOMAIN:=}"
: "${FLASK_SESSION_COOKIE_SECURE:=${SESSION_COOKIE_SECURE:-true}}"
: "${FLASK_SESSION_COOKIE_NAME:=${SESSION_COOKIE_NAME:-flask_session}}"

write_env_entry() {
    local key=$1
    local value=$2
    if [ -n "$value" ]; then
        local escaped=${value//\\/\\\\}
        escaped=${escaped//\"/\\\"}
        printf '%s: "%s"\n' "$key" "$escaped" >> "$ENV_FILE"
    fi
}

printf 'ENV: "production"\n' > "$ENV_FILE"
write_env_entry "GCP_PROJECT_ID" "$GCP_PROJECT_ID"
write_env_entry "GCS_BUCKET" "$GCS_BUCKET"
write_env_entry "SECRET_KEY" "$SECRET_KEY"
write_env_entry "TTS_VOICE" "$TTS_VOICE"
write_env_entry "CLOUD_TASKS_QUEUE" "$CLOUD_TASKS_QUEUE"
write_env_entry "CLOUD_TASKS_LOCATION" "$CLOUD_TASKS_LOCATION"
write_env_entry "SERVICE_ACCOUNT_EMAIL" "$SERVICE_ACCOUNT_EMAIL"
write_env_entry "SERVICE_URL" "$SERVICE_URL"
write_env_entry "AUTH_BACKEND" "$AUTH_BACKEND"
write_env_entry "AUTH_ENABLED" "$AUTH_ENABLED"
write_env_entry "FIREBASE_PROJECT_ID" "$FIREBASE_PROJECT_ID"
write_env_entry "FIREBASE_WEB_API_KEY" "$FIREBASE_WEB_API_KEY"
write_env_entry "FIREBASE_AUTH_DOMAIN" "$FIREBASE_AUTH_DOMAIN"
write_env_entry "FLASK_SESSION_COOKIE_SECURE" "$FLASK_SESSION_COOKIE_SECURE"
write_env_entry "FLASK_SESSION_COOKIE_NAME" "$FLASK_SESSION_COOKIE_NAME"
write_env_entry "ALLOWED_ORIGINS" "$ALLOWED_ORIGINS"

section "Deploying to Cloud Run"
DEPLOY_CMD=(
    gcloud run deploy "$SERVICE_NAME"
    --project="$GCP_PROJECT_ID"
    --image="$IMAGE_TAG"
    --platform=managed
    --region="$GCP_REGION"
    --service-account="$SERVICE_ACCOUNT_EMAIL"
    --allow-unauthenticated
    --port=8080
    --memory="$CLOUD_RUN_MEMORY"
    --cpu="$CLOUD_RUN_CPU"
    --concurrency="$CLOUD_RUN_CONCURRENCY"
    --max-instances="$CLOUD_RUN_MAX_INSTANCES"
    --min-instances="$CLOUD_RUN_MIN_INSTANCES"
    --timeout="$CLOUD_RUN_TIMEOUT"
    --env-vars-file="$ENV_FILE"
    --labels="env=production,service=${SERVICE_NAME},commit=${GIT_SHA}"
)

if [ "$USE_REVISION_SUFFIX" = true ] && [ -n "$REVISION_SUFFIX" ]; then
    DEPLOY_CMD+=("--revision-suffix=$REVISION_SUFFIX")
fi

DEPLOY_CMD+=("--quiet")

"${DEPLOY_CMD[@]}"

section "Configuring health checks"
gcloud run services update "$SERVICE_NAME" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --update-liveness-probe=http-path=/health,initial-delay-seconds=30 \
    --quiet

section "Post-deploy steps"
NEW_SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
    --platform=managed \
    --region="$GCP_REGION" \
    --project="$GCP_PROJECT_ID" \
    --format='value(status.url)')

LATEST_REVISION=$(gcloud run services describe "$SERVICE_NAME" \
    --platform=managed \
    --region="$GCP_REGION" \
    --project="$GCP_PROJECT_ID" \
    --format='value(status.latestCreatedRevision)')

IMAGE_DIGEST=$(gcloud run revisions describe "$LATEST_REVISION" \
    --platform=managed \
    --region="$GCP_REGION" \
    --project="$GCP_PROJECT_ID" \
    --format='value(status.imageDigest)')

DEPLOY_METADATA_FILE=${DEPLOY_METADATA_FILE:-deploy-history.jsonl}
/usr/bin/touch "$DEPLOY_METADATA_FILE"

gcloud run revisions describe "$LATEST_REVISION" \
    --platform=managed \
    --region="$GCP_REGION" \
    --project="$GCP_PROJECT_ID" \
    --format=json | /usr/bin/tee -a "$DEPLOY_METADATA_FILE" >/dev/null

if [ -n "$NEW_SERVICE_URL" ]; then
    log "Service available at: $NEW_SERVICE_URL"
    if [ -f .env ] && [ "$NEW_SERVICE_URL" != "${SERVICE_URL:-}" ]; then
        log "Updating SERVICE_URL in .env"
        if grep -q '^SERVICE_URL=' .env; then
            /usr/bin/sed -i.bak "s|^SERVICE_URL=.*|SERVICE_URL=\"${NEW_SERVICE_URL}\"|" .env
        else
            printf 'SERVICE_URL="%s"\n' "$NEW_SERVICE_URL" >> .env
        fi
        rm -f .env.bak
    fi
fi

log "Latest revision: $LATEST_REVISION"
log "Image digest: $IMAGE_DIGEST"
log "Image tags pushed: $IMAGE_TAG, $LATEST_TAG"
log "Deployment metadata appended to $DEPLOY_METADATA_FILE"
log "Cloud Console logs: https://console.cloud.google.com/run/detail/$GCP_REGION/$SERVICE_NAME/logs?project=$GCP_PROJECT_ID"

section "Deployment complete"
