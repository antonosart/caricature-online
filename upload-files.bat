@echo off
REM ═══════════════════════════════════════════════════════════════
REM  CARICATURE.ONLINE — Upload all frontend files to GCS
REM  Run from: C:\caricature-deploy\
REM ═══════════════════════════════════════════════════════════════

SET PROJECT=caricature-online-495715
SET BUCKET=gs://caricature.online

echo.
echo === Uploading HTML + JS files ===
gcloud storage cp index.html %BUCKET%/index.html --project=%PROJECT%
gcloud storage cp order.html %BUCKET%/order.html --project=%PROJECT%
gcloud storage cp privacy.html %BUCKET%/privacy.html --project=%PROJECT%
gcloud storage cp terms.html %BUCKET%/terms.html --project=%PROJECT%
gcloud storage cp cookies.html %BUCKET%/cookies.html --project=%PROJECT%
gcloud storage cp cookie-banner.js %BUCKET%/cookie-banner.js --project=%PROJECT%

echo.
echo === Setting Content-Type headers ===
gcloud storage objects update %BUCKET%/index.html --content-type="text/html; charset=utf-8" --project=%PROJECT%
gcloud storage objects update %BUCKET%/order.html --content-type="text/html; charset=utf-8" --project=%PROJECT%
gcloud storage objects update %BUCKET%/privacy.html --content-type="text/html; charset=utf-8" --project=%PROJECT%
gcloud storage objects update %BUCKET%/terms.html --content-type="text/html; charset=utf-8" --project=%PROJECT%
gcloud storage objects update %BUCKET%/cookies.html --content-type="text/html; charset=utf-8" --project=%PROJECT%
gcloud storage objects update %BUCKET%/cookie-banner.js --content-type="application/javascript; charset=utf-8" --project=%PROJECT%

echo.
echo === Health check ===
curl https://api.caricature.online/health

echo.
echo === DONE — Visit https://caricature.online ===
pause
