#!/usr/bin/env bash
# Deploy LifeNest AI: Cloud Run API + Firestore + Cloud Storage + Vertex AI Gemini, then Firebase Hosting for web/.
#
#   PROJECT_ID=my-proj GEMINI_MODEL=<gemini model id> EMBEDDING_MODEL=<embedding model id> ./deploy.sh
#   Optional: FIRESTORE_DATABASE=<named database> (default "(default)"), REGION, BUCKET, WEB_ORIGIN.
#
# Idempotent: re-running updates the service and skips resources that already exist.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
GEMINI_MODEL="${GEMINI_MODEL:?set GEMINI_MODEL}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:?set EMBEDDING_MODEL}"
EMBEDDING_DIM="${EMBEDDING_DIM:-768}"
REGION="${REGION:-asia-southeast1}"
FIRESTORE_DATABASE="${FIRESTORE_DATABASE:-(default)}"
SERVICE="${SERVICE:-lifenest-api}"
REPO="${REPO:-lifenest}"
BUCKET="${BUCKET:-${PROJECT_ID}-lifenest-docs}"
SA_NAME="${SA_NAME:-lifenest-api}"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
WEB_ORIGIN="${WEB_ORIGIN:-https://${PROJECT_ID}.web.app}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/api:$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

gcloud config set project "$PROJECT_ID" >/dev/null

if [[ "${SKIP_INFRA:-0}" != "1" ]]; then
echo "== 1/8 Enable APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  firestore.googleapis.com storage.googleapis.com aiplatform.googleapis.com iamcredentials.googleapis.com \
  firebase.googleapis.com identitytoolkit.googleapis.com logging.googleapis.com

echo "== 2/8 Firestore (Native mode), TTL policies and indexes"
DB=(--database="$FIRESTORE_DATABASE")
gcloud firestore databases describe "${DB[@]}" >/dev/null 2>&1 \
  || gcloud firestore databases create "${DB[@]}" --location="$REGION" --type=firestore-native
for group in sessions documents pending_actions; do
  gcloud firestore fields ttls update expires_at "${DB[@]}" --collection-group="$group" --enable-ttl --async >/dev/null || true
done
index() { gcloud firestore indexes composite create "${DB[@]}" --query-scope=COLLECTION --async "$@" >/dev/null 2>&1 || true; }
printf '[{"field-path":"embedding","vector-config":{"dimension":%s,"flat":{}}}]' \
  "$EMBEDDING_DIM" > "$TMP/kb-vector-index.json"
printf '[{"field-path":"category","order":"ASCENDING"},{"field-path":"embedding","vector-config":{"dimension":%s,"flat":{}}}]' \
  "$EMBEDDING_DIM" > "$TMP/kb-category-vector-index.json"
index --collection-group=kb_chunks --field-config="$TMP/kb-vector-index.json"
index --collection-group=kb_chunks --field-config="$TMP/kb-category-vector-index.json"
index --collection-group=audit_logs --field-config=field-path=session_id,order=ASCENDING --field-config=field-path=ts,order=ASCENDING
index --collection-group=audit_logs --field-config=field-path=event,order=ASCENDING --field-config=field-path=ts,order=ASCENDING
index --collection-group=audit_logs --field-config=field-path=session_id,order=ASCENDING \
  --field-config=field-path=event,order=ASCENDING --field-config=field-path=ts,order=ASCENDING
index --collection-group=pending_actions --field-config=field-path=session_id,order=ASCENDING --field-config=field-path=status,order=ASCENDING
index --collection-group=property_stock --field-config=field-path=area,order=ASCENDING --field-config=field-path=price,order=ASCENDING

echo "== 3/8 Private document bucket: 24h lifecycle delete, browser PUT CORS"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":1}}]}' > "$TMP/lifecycle.json"
echo "[{\"origin\":[\"$WEB_ORIGIN\"],\"method\":[\"PUT\"],\"responseHeader\":[\"Content-Type\"],\"maxAgeSeconds\":3600}]" > "$TMP/cors.json"
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file="$TMP/lifecycle.json" --cors-file="$TMP/cors.json"

echo "== 4/8 Least-privilege runtime service account"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SA_NAME" --display-name="LifeNest API runtime"
for role in roles/datastore.user roles/aiplatform.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$role" \
    --condition=None >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" \
  --role=roles/storage.objectAdmin >/dev/null
# Signed upload URLs are signed via IAM signBlob with the service's own identity (no key files).
gcloud iam service-accounts add-iam-policy-binding "$SA" --member="serviceAccount:$SA" \
  --role=roles/iam.serviceAccountTokenCreator >/dev/null
fi

echo "== 5/8 Build image"
gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION"
gcloud builds submit --tag "$IMAGE" .

echo "== 6/8 Seed property stock, demo profiles and the regulation KB (Vertex embeddings)"
# KB_EMBEDDINGS_PATH points at a temp file so the committed offline embeddings are not overwritten.
DB_BACKEND=firestore KB_BACKEND=firestore LLM_BACKEND=vertex GOOGLE_CLOUD_PROJECT="$PROJECT_ID" FIRESTORE_DATABASE="$FIRESTORE_DATABASE" \
  GOOGLE_CLOUD_LOCATION="$REGION" EMBEDDING_MODEL="$EMBEDDING_MODEL" EMBEDDING_DIM="$EMBEDDING_DIM" \
  KB_EMBEDDINGS_PATH="$TMP/kb_embeddings.json" python scripts/seed_firestore.py --with-kb

echo "== 7/8 Deploy Cloud Run"
# Public ingress is intended: every route except /health verifies a Firebase ID token in the app.
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --service-account "$SA" \
  --allow-unauthenticated --port 8080 --cpu 1 --memory 1Gi --concurrency 40 --timeout 120 \
  --min-instances 0 --max-instances 5 \
  --set-env-vars "^@^APP_ENV=prod@AUTH_BACKEND=firebase@FIREBASE_PROJECT_ID=$PROJECT_ID@GOOGLE_CLOUD_PROJECT=$PROJECT_ID@GOOGLE_CLOUD_LOCATION=$REGION@DB_BACKEND=firestore@FIRESTORE_DATABASE=$FIRESTORE_DATABASE@KB_BACKEND=firestore@STORAGE_BACKEND=gcs@LLM_BACKEND=vertex@GCS_BUCKET=$BUCKET@GEMINI_MODEL=$GEMINI_MODEL@EMBEDDING_MODEL=$EMBEDDING_MODEL@EMBEDDING_DIM=$EMBEDDING_DIM@GROUNDING_CHECK=enforce@CORS_ALLOWED_ORIGINS=[\"$WEB_ORIGIN\"]"
API_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
curl -fsS "$API_URL/health" && echo

echo "== 8/8 Web front end (Firebase Hosting)"
cat <<EOF
1. Firebase console > Authentication > Sign-in method: enable Anonymous.
2. Edit web/config.js: apiBaseUrl: "$API_URL", authMode: "firebase", and the web app's firebase config.
3. firebase deploy --only hosting --project $PROJECT_ID
API: $API_URL
EOF