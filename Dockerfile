# ---------- stage 1: build the command-centre UI ----------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- stage 2: runtime ----------
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    AEGIS_ENV=production AEGIS_DATA_DIR=/data AEGIS_HOST=0.0.0.0
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home /app aegis
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/app backend/app
COPY --from=ui /ui/dist frontend/dist
RUN mkdir -p /data && chown -R aegis:aegis /data /app
USER aegis
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1
WORKDIR /app/backend
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "1"]
