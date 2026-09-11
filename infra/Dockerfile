FROM python:3.12-slim

# System deps for psycopg2, geoalchemy2, and OpenCV/FFmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev libgeos-dev ffmpeg libsm6 libxext6 libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps — model1 first, then model2 (VMS ingestion)
COPY model1-registry/requirements.txt /app/requirements.txt
COPY model2_analytics/requirements.txt /app/requirements-model2.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements-model2.txt

# ANPR pipeline deps (needed by model2_analytics/pipeline — plate detect + OCR)
RUN pip install --no-cache-dir ultralytics onnxruntime onnxocr

# Copy source
COPY shared/ /app/shared/
COPY model1-registry/app/ /app/app/
COPY model2_analytics/ /app/model2_analytics/

# Standard hardening: run as a non-root user instead of the container
# default (root). Not urgent on its own (this app doesn't execute
# genuinely untrusted input), but cheap to do and limits the blast
# radius of any future RCE-class bug (AuditReport1.md finding 21 / 4.3).
# /app/model2_analytics/{detection-image,uploads} need to stay writable -
# the detection pipeline and uploads router write into them at runtime -
# so they're chowned explicitly rather than relying on a blanket
# `chown -R` catching everything by coincidence.
RUN useradd --system --create-home --uid 1000 --shell /usr/sbin/nologin appuser && \
    chown -R appuser:appuser /app && \
    mkdir -p /app/model2_analytics/detection-image /app/model2_analytics/uploads && \
    chown -R appuser:appuser /app/model2_analytics/detection-image /app/model2_analytics/uploads
USER appuser

# AuditReport2.md finding 5: model2_analytics/ used to be COPY'd into the
# image three separate times under three different names/paths (the full
# hyphenated package, an app/-only copy, and the underscored shim) so that
# each of its three import spellings (`pipeline.*`, `ingestion.*`,
# `model2_analytics.app.ingestion.*` — see supervisor.py and
# model1-registry/app/main.py) would resolve. There's now exactly one copy
# on disk at /app/model2_analytics/; these three PYTHONPATH entries are
# what make all three spellings still resolve to that single copy:
#   /app                      -> model2_analytics.app.ingestion.* (dotted)
#   /app/model2_analytics     -> pipeline.* (top-level)
#   /app/model2_analytics/app -> ingestion.* (top-level)
ENV PYTHONPATH=/app:/app/model2_analytics:/app/model2_analytics/app
# Runtime config defaults — override via docker-compose environment or .env
ENV GRID_HOST=cctv.corp8.cloud
ENV MEDIAMTX_API=mediamtx:9997
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
