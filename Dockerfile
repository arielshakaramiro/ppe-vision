# Tahap 1: build frontend React
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Tahap 2: backend FastAPI + pipeline
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.lock .
# Install PyTorch versi CPU agar image Docker sangat kecil dan tidak error I/O disk penuh
RUN pip install --no-cache-dir torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.lock \
    && pip check
COPY backend/ .
COPY --from=web /web/dist /app/frontend/dist

# Jalankan sebagai user biasa, bukan root. UID bisa disamakan dengan pemilik folder volume di host:
#   docker compose build --build-arg APP_UID=$(id -u)
ARG APP_UID=1000
RUN useradd --uid ${APP_UID} --create-home --shell /usr/sbin/nologin ppe \
    && mkdir -p /app/backend/data /app/backend/weights \
    && chown -R ppe:ppe /app/backend/data /app/backend/weights
ENV YOLO_CONFIG_DIR=/app/backend/data/.ultralytics
USER 1000:1000
EXPOSE 8000
# API satu worker; tugas background produksi jalan lewat service `python -m scripts.worker`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
