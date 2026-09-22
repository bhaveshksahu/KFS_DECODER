<#
.SYNOPSIS
    Deploy KFS Decoder to Google Cloud Run.

.DESCRIPTION
    1. Enables required GCP APIs.
    2. Creates the GEMINI_API_KEY secret in Secret Manager if it does not exist
       (prompts for the value, never echoes it to the terminal or logs).
    3. Grants the Cloud Run service account access to the secret.
    4. Deploys the repo-root Dockerfile to Cloud Run via gcloud run deploy --source .
       (Cloud Build builds and pushes the image; no local Docker daemon needed).

.NOTES
    Prerequisites:
      - gcloud CLI authenticated:  gcloud auth login
      - Active project set:        gcloud config set project PROJECT_ID
      OR set PROJECT_ID below and the script sets it for you.

    Secrets:
      GEMINI_API_KEY is read from Secret Manager at runtime via --set-secrets.
      It is NEVER baked into the image or written to any file.

    Run from the repo root:
      .\deploy.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- Configuration -----------------------------------------------------------
# Edit PROJECT_ID if needed; REGION matches PRD section 20.

$PROJECT_ID = "genai-addk-bhavesh"
$REGION     = "asia-south1"

# Derived names - no need to change
$SERVICE_NAME = "kfs-decoder"
$SECRET_NAME  = "GEMINI_API_KEY"

# --- Guards ------------------------------------------------------------------

if ($PROJECT_ID -eq "YOUR_PROJECT_ID") {
    Write-Error "Edit `$PROJECT_ID at the top of deploy.ps1 before running."
    exit 1
}

# --- Set active project ------------------------------------------------------

Write-Host "==> Setting active project to $PROJECT_ID" -ForegroundColor Cyan
gcloud config set project $PROJECT_ID

# --- Enable required APIs ----------------------------------------------------

$APIS = @(
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com"
)

Write-Host "==> Enabling APIs (this is idempotent)" -ForegroundColor Cyan
foreach ($api in $APIS) {
    Write-Host "    $api"
    gcloud services enable $api --project $PROJECT_ID --quiet
}

# --- Create GEMINI_API_KEY secret if missing ---------------------------------

$secretExists = $false
try {
    $null = gcloud secrets describe $SECRET_NAME --project $PROJECT_ID 2>&1
    $secretExists = $true
} catch {
    $secretExists = $false
}

if (-not $secretExists) {
    Write-Host ""
    Write-Host "==> Secret '$SECRET_NAME' not found. Creating it now." -ForegroundColor Cyan
    Write-Host "    Enter your Gemini API key (input is hidden):" -ForegroundColor Yellow

    # Read-Host -AsSecureString keeps the value off the terminal and out of logs
    $secureKey = Read-Host -AsSecureString "Gemini API Key"
    $bstr     = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $plainKey  = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

    # Pipe via stdin so the key never appears in process arguments
    $plainKey | gcloud secrets create $SECRET_NAME `
        --project $PROJECT_ID `
        --replication-policy automatic `
        --data-file -

    # Null out the plaintext immediately
    $plainKey = $null

    Write-Host "    Secret created." -ForegroundColor Green
} else {
    Write-Host "==> Secret '$SECRET_NAME' already exists - skipping creation." -ForegroundColor Green
    Write-Host "    To rotate the key run:" -ForegroundColor DarkGray
    Write-Host "      gcloud secrets versions add $SECRET_NAME --data-file=-" -ForegroundColor DarkGray
    Write-Host "    (type or paste the new key then press Ctrl-D / Ctrl-Z)" -ForegroundColor DarkGray
}

# --- Grant Cloud Run SA access to the secret ---------------------------------
# The default Compute SA is PROJECT_NUMBER-compute@developer.gserviceaccount.com
# Doing this explicitly avoids the IAM propagation race on first deploy.

$PROJECT_NUMBER = (gcloud projects describe $PROJECT_ID --format "value(projectNumber)")
$CR_SA = "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

Write-Host "==> Granting Secret Accessor to $CR_SA" -ForegroundColor Cyan
gcloud secrets add-iam-policy-binding $SECRET_NAME `
    --project $PROJECT_ID `
    --member "serviceAccount:${CR_SA}" `
    --role "roles/secretmanager.secretAccessor" `
    --quiet

# --- Deploy to Cloud Run -----------------------------------------------------

Write-Host ""
Write-Host "==> Deploying $SERVICE_NAME to Cloud Run ($REGION)..." -ForegroundColor Cyan
Write-Host "    Cloud Build will build the image from the repo-root Dockerfile."
Write-Host "    This takes 3-5 minutes on first run."
Write-Host ""

gcloud run deploy $SERVICE_NAME `
    --source . `
    --project $PROJECT_ID `
    --region $REGION `
    --platform managed `
    --set-secrets "GEMINI_API_KEY=${SECRET_NAME}:latest" `
    --set-env-vars "GEMINI_BACKEND=developer,STORAGE_BACKEND=local,LOCATION=${REGION}" `
    --timeout 300 `
    --memory 1Gi `
    --cpu 1 `
    --min-instances 0 `
    --max-instances 10 `
    --allow-unauthenticated `
    --quiet

# --- Post-deploy verification ------------------------------------------------

Write-Host ""
Write-Host "==> Fetching service URL..." -ForegroundColor Cyan
$SERVICE_URL = (gcloud run services describe $SERVICE_NAME `
    --project $PROJECT_ID `
    --region $REGION `
    --format "value(status.url)")

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Deployment complete!" -ForegroundColor Green
Write-Host "  Service URL : $SERVICE_URL" -ForegroundColor Green
Write-Host "============================================================"
Write-Host ""
Write-Host "Post-deploy checks:" -ForegroundColor Cyan
Write-Host "  Health:   $SERVICE_URL/healthz"
Write-Host "  Samples:  $SERVICE_URL/api/v1/samples"
Write-Host "  Frontend: $SERVICE_URL/"
Write-Host ""
Write-Host "Quick smoke test (requires curl):"
Write-Host "  curl -sf $SERVICE_URL/healthz | python -m json.tool"
Write-Host "  curl -sf $SERVICE_URL/api/v1/samples | python -m json.tool"
