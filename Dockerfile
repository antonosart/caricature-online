# ═══════════════════════════════════════════════
# Dockerfile — Caricature.online Backend v2
# ═══════════════════════════════════════════════
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY main.py .

# Cloud Run sets PORT env variable (default 8080)
ENV PORT=8080

# Run with gunicorn — 2 workers, 4 threads, 120s timeout
CMD exec gunicorn \
    --bind :$PORT \
    --workers 2 \
    --threads 4 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    main:app
