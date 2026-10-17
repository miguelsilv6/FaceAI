# Imagem única: build do frontend + backend FastAPI que o serve.
# CPU:  docker compose up --build
# GPU:  docker compose --profile gpu up --build faceai-gpu

FROM node:22-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS app
# cpu (onnxruntime) | gpu (onnxruntime-gpu + CUDA/cuDNN via pip) | "cpu,deepface" para incluir o motor DeepFace
ARG EXTRA=cpu
ENV PYTHONUNBUFFERED=1 \
    FACEAI_DATA_DIR=/data \
    FACEAI_FRONTEND_DIST=/app/frontend/dist \
    FACEAI_MODELS_DIR=/models \
    DEEPFACE_HOME=/models
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libgl1 libglib2.0-0 libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/ ./
RUN pip install --no-cache-dir ".[${EXTRA}]"
COPY --from=frontend /src/dist /app/frontend/dist
VOLUME ["/data", "/models"]
EXPOSE 8000
ENV FACEAI_SHUTDOWN_GRACE_S=45
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
