#!/bin/bash
# Idempotent script to set up required GCP infrastructure.

set -e

# Load environment variables from .env file if it exists
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | sed -e 's/#.*//' -e '/^$/d' | xargs)
fi

# Check for required variables
if [ -z "$GCP_PROJECT_ID" ] || [ -z "$GCS_BUCKET" ] || [ -z "$GCP_REGION" ]; then
    echo "Error: GCP_PROJECT_ID, GCS_BUCKET, and GCP_REGION must be set."
    echo "Please create a .env file from .env.example and fill in the values."
    exit 1
fi

echo "Configuring gcloud to use project $GCP_PROJECT_ID..."
gcloud config set project $GCP_PROJECT_ID --quiet

# APIs to enable
SERVICES=(
    "run.googleapis.com"
    "storage-component.googleapis.com"
    "firestore.googleapis.com"
    "texttospeech.googleapis.com"
    "secretmanager.googleapis.com"
    "iam.googleapis.com"
    "artifactregistry.googleapis.com"
    "cloudtasks.googleapis.com"
)

echo "Enabling required GCP APIs..."
for SERVICE in "${SERVICES[@]}"; do
    echo "Enabling $SERVICE..."
    gcloud services enable $SERVICE
done

# Create GCS Bucket if it doesn't exist
if gsutil ls -b gs://$GCS_BUCKET >/dev/null 2>&1; then
    echo "GCS bucket gs://$GCS_BUCKET already exists."
else
    echo "Creating GCS bucket gs://$GCS_BUCKET..."
    gsutil mb -p $GCP_PROJECT_ID -l $GCP_REGION gs://$GCS_BUCKET
    gsutil uniformbucketlevelaccess set on gs://$GCS_BUCKET
fi

# Create Firestore database if it doesn't exist
# NOTE: This is a simplified check. `gcloud firestore databases list` is a more robust way.
if gcloud firestore databases describe >/dev/null 2>&1; then
    echo "Firestore database already exists in $GCP_REGION."
else
    echo "Creating Firestore database in Native mode..."
    gcloud firestore databases create --location=$GCP_REGION
fi

# Create Cloud Tasks Queue
QUEUE_NAME="zissou-tasks"
DLQ_NAME="zissou-tasks-dlq"

# Create the main queue
if gcloud tasks queues describe $QUEUE_NAME --location=$GCP_REGION >/dev/null 2>&1;
    then
    echo "Cloud Tasks queue '$QUEUE_NAME' already exists."
else
    echo "Creating Cloud Tasks queue '$QUEUE_NAME'..."
    gcloud tasks queues create $QUEUE_NAME --location=$GCP_REGION
fi

# Create the dead-letter queue
if gcloud tasks queues describe $DLQ_NAME --location=$GCP_REGION >/dev/null 2>&1;
    then
    echo "Dead-letter queue '$DLQ_NAME' already exists."
else
    echo "Creating dead-letter queue '$DLQ_NAME'..."
    gcloud tasks queues create $DLQ_NAME --location=$GCP_REGION
fi

# Set queue configuration for the main queue
echo "Configuring Cloud Tasks queue '$QUEUE_NAME'..."
# We add `|| true` at the end because this command can fail if the settings
# are already correctly applied, and we want the script to be idempotent.
gcloud tasks queues update $QUEUE_NAME \
    --location=$GCP_REGION \
    --max-dispatches-per-second=20 \
    --max-concurrent-dispatches=10 \
    --max-attempts=8 \
    --max-retry-duration=3600s \
    --min-backoff=10s \
    --max-backoff=600s \
    --max-doublings=16 \
    --dead-letter-queue=$DLQ_NAME || true

# Create Service Account
SA_NAME="zissou-runner"

SA_EMAIL="${SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

if gcloud iam service-accounts describe $SA_EMAIL >/dev/null 2>&1; then
    echo "Service account $SA_EMAIL already exists."
else
    echo "Creating service account $SA_EMAIL..."
    gcloud iam service-accounts create $SA_NAME \
        --display-name="Zissou Application Runner"
    echo "Waiting 10 seconds for service account to propagate..."
    sleep 10
fi

# Assign Roles to Service Account
ROLES=(
    "roles/run.invoker"             # To invoke the Cloud Run service
    "roles/storage.objectAdmin"     # For writing audio files
    "roles/datastore.user"          # For Firestore access
    "roles/iam.serviceAccountUser"
    "roles/cloudtasks.enqueuer"     # To create tasks
)

echo "Assigning roles to service account $SA_EMAIL..."
for ROLE in "${ROLES[@]}"; do
    gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$ROLE" \
        --condition=None
done

# Grant the deploying user the ability to act as the service account
DEPLOYING_USER=$(gcloud config get-value account)
echo "Granting 'Service Account User' role to $DEPLOYING_USER on service account $SA_EMAIL..."
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
    --member="user:$DEPLOYING_USER" \
    --role="roles/iam.serviceAccountUser"

# --- Update .env file ---
echo "\nUpdating .env file with created resource names..."

# Function to add or update a key-value pair in .env
update_env_file() {
    KEY=$1
    VALUE=$2
    if grep -q "^${KEY}=" .env; then
        # Update existing key
        sed -i.bak "s|^${KEY}=.*|${KEY}=${VALUE}|" .env
    else
        # Add new key
        echo "${KEY}=${VALUE}" >> .env
    fi
}

update_env_file "CLOUD_TASKS_QUEUE" $QUEUE_NAME
update_env_file "CLOUD_TASKS_LOCATION" $GCP_REGION
update_env_file "SERVICE_ACCOUNT_EMAIL" $SA_EMAIL

# Remove backup file created by sed
rm -f .env.bak

echo "\nInfrastructure setup complete! .env file has been updated."
echo "IMPORTANT: You must now deploy the application to get the SERVICE_URL."
echo "After the first deployment, add the SERVICE_URL to your .env file."