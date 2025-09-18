# Zissou Infrastructure (`/infra`)

This directory contains scripts for setting up and deploying the Zissou application on Google Cloud Platform.

## Scripts

### `setup.sh`

This script is an idempotent way to provision all the necessary GCP resources for the application. It will:

1.  **Enable APIs**: Activates Cloud Run, GCS, Firestore, TTS, Secret Manager, and IAM APIs.
2.  **Create GCS Bucket**: Creates the storage bucket specified in your `.env` file if it doesn't already exist.
3.  **Create Firestore Database**: Initializes a Firestore database in Native Mode in your specified region.
4.  **Create Service Account**: Creates a dedicated service account (`zissou-runner`) for the application to use.
5.  **Assign IAM Roles**: Grants the service account the necessary permissions to run, access storage, and use Firestore.
6.  **Seed Database**: Creates a default "General" bucket in Firestore to make the app usable immediately.

**Usage:**

```bash
# Make sure you are authenticated with gcloud
gcloud auth login

# Run the script from the project root
./infra/setup.sh
```

### `deploy_cloud_run.sh`

This script containerizes the application and deploys it to Cloud Run.

1.  **Create Artifact Registry**: Ensures a Docker repository exists in Artifact Registry to store the image.
2.  **Build & Push Image**: Builds the Docker image and pushes it to your project's Artifact Registry.
3.  **Deploy to Cloud Run**: Deploys the container image as a new revision to a Cloud Run service. It configures:
    - Public (unauthenticated) access.
    - The service account for runtime identity.
    - CPU, memory, and port settings.
    - Essential environment variables.

**Usage:**

```bash
# Run from the project root
./infra/deploy_cloud_run.sh
```

## Manual `gcloud` Commands

The scripts automate these steps, but here are the underlying `gcloud` commands for reference.

**Build and Push:**
```bash
# Build
docker build -t "us-central1-docker.pkg.dev/your-project/zissou-repo/zissou:latest" ..

# Push
docker push "us-central1-docker.pkg.dev/your-project/zissou-repo/zissou:latest"
```

**Deploy:**

```bash
gcloud run deploy zissou \
  --image "us-central1-docker.pkg.dev/your-project/zissou-repo/zissou:latest" \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated
```
