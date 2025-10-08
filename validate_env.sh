#!/bin/bash

# Load environment variables from .env file
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Default required variables
required_vars=("GCP_PROJECT_ID" "GCP_REGION" "GCS_BUCKET" "SECRET_KEY" "CLOUD_TASKS_QUEUE" "CLOUD_TASKS_LOCATION" "SERVICE_ACCOUNT_EMAIL")

# Check for AUTH_BACKEND and add firebase variables if needed
if [ "$AUTH_BACKEND" == "firebase" ]; then
  required_vars+=("FIREBASE_PROJECT_ID" "FIREBASE_WEB_API_KEY" "FIREBASE_AUTH_DOMAIN")
fi

# Loop through the required variables and check if they are set
missing_vars=()
for var in "${required_vars[@]}"; do
  if [ -z "${!var}" ]; then
    missing_vars+=("$var")
  fi
done

# If there are missing variables, print an error and exit
if [ ${#missing_vars[@]} -ne 0 ]; then
  echo "Error: The following required environment variables are not set or are empty:"
  for var in "${missing_vars[@]}"; do
    echo "  - $var"
  done
  echo "Please define them in your .env file."
  exit 1
fi

echo "Environment variables validated successfully."
