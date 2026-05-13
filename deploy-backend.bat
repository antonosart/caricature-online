@echo off
REM ═══════════════════════════════════════════════════════════════
REM  CARICATURE.ONLINE — Deploy Backend v2.0 (Windows)
REM  Run from: C:\caricature-deploy\
REM  Project:  caricature-online-495715
REM ═══════════════════════════════════════════════════════════════

SET PROJECT=caricature-online-495715
SET REGION=us-central1
SET SERVICE=caricature-api
SET BUCKET=caricature-files

echo.
echo ==============================================================
echo   CARICATURE.ONLINE — Backend Deploy v2.0
echo ==============================================================
echo.

gcloud config set project %PROJECT%

echo [Deploying to Cloud Run...]
gcloud run deploy %SERVICE% ^
  --source . ^
  --platform managed ^
  --region %REGION% ^
  --allow-unauthenticated ^
  --memory 1Gi ^
  --cpu 2 ^
  --concurrency 80 ^
  --max-instances 10 ^
  --timeout 120 ^
  --project %PROJECT% ^
  --set-env-vars="GCS_BUCKET=%BUCKET%,BASE_URL=https://caricature.online" ^
  --set-secrets="STRIPE_SECRET_KEY=STRIPE_SECRET_KEY:latest,STRIPE_PUBLISHABLE_KEY=STRIPE_PUBLISHABLE_KEY:latest,STRIPE_WEBHOOK_SECRET=STRIPE_WEBHOOK_SECRET:latest,FAL_API_KEY=FAL_API_KEY:latest,FAL_LORA_URL=FAL_LORA_URL:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,SENDGRID_API_KEY=SENDGRID_API_KEY:latest,ADMIN_SECRET=ADMIN_SECRET:latest"

echo.
echo [Health check...]
curl https://api.caricature.online/health
echo.
echo ==============================================================
echo   DONE — https://caricature.online
echo ==============================================================
pause
