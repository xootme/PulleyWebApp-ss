# Dockerfile — the pulley app on Google Cloud Run (ADR-008).
#
# Build and run locally:
#   docker build -t pulley .
#   docker run --rm -p 8080:8080 pulley          # then open http://localhost:8080
# Deploy (builds this file in Cloud Build):
#   gcloud run deploy pulley --source .
#
# small_step (the STEP engine) is a private repo, so its static Linux build
# is committed at bin/small_step_linux — rebuild it whenever small_step
# changes (small_step/RELEASE.md). The build fails here if it won't run.

FROM python:3.14-slim

# cairosvg needs the Cairo C library; fonts-dejavu gives SVG-to-PNG text a font.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libcairo2 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so code-only changes reuse this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x bin/small_step_linux && bin/small_step_linux --version

ENV SMALL_STEP_BIN=/app/bin/small_step_linux \
    PYTHONUNBUFFERED=1 \
    PULLEY_LOG_DIR=/tmp/pulley-logs \
    PORT=8080

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 app && mkdir -p /tmp/pulley-logs && chown app /tmp/pulley-logs
USER app

EXPOSE 8080
# Cloud Run sets PORT; gunicorn.conf.py sets workers (WEB_CONCURRENCY), timeout, etc.
CMD exec gunicorn app:app --config gunicorn.conf.py --bind "0.0.0.0:${PORT}"
