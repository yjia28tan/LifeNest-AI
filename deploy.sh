#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Configuration
PROJECT_ID="lifenest-ai"
REGION="asia-southeast1"
REPO_NAME="lifenest-repo"
IMAGE_NAME="lifenest-app"
SERVICE_NAME="lifenest-service"
SERVICE_ACCOUNT="lifenest-runner@${PROJECT_ID}.iam.gserviceaccount.com"

echo "🚀 Initiating LifeNest AI Deploy Pipeline..."

# 1. Build the Docker image via Google Cloud Build
echo "📦 Building Docker image on Google Cloud Build..."
gcloud builds submit --tag ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest .

# 2. Deploy the container to Google Cloud Run
echo "🚢 Deploying container to Google Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
    --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest \
    --platform managed \
    --region ${REGION} \
    --service-account ${SERVICE_ACCOUNT} \
    --allow-unauthenticated \
    --set-env-vars GCP_PROJECT_ID=${PROJECT_ID},GCP_REGION=${REGION} \
    --port 8501 # Streamlit default port

echo "✅ Deployment successful!"
echo "🔗 Access your application at: $(gcloud run services describe ${SERVICE_NAME} --platform managed --region ${REGION} --format='value(status.url)')"