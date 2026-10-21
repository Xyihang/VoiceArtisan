# Qwen3-TTS 云端推理服务镜像
# 构建：docker build -f cloud/Dockerfile -t qwentts-cloud:1.0 .
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu121

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_INDEX_URL=${PIP_INDEX} \
    HF_HOME=/app/.cache/hf \
    MODELSCOPE_CACHE=/app/.cache/modelscope \
    TORCH_HOME=/app/.cache/torch \
    QWENTTS_CONFIG=/app/config/config.yaml \
    PYTHONPATH=/app/backend

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 python3-pip python3.10-venv ffmpeg libsndfile1 openssh-client curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.10 /usr/bin/python

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir torch torchaudio --index-url ${TORCH_INDEX} \
    && python -m pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
COPY config /app/config
COPY scripts /app/scripts

RUN mkdir -p /app/data /app/models /app/.cache \
    && cp /app/config/config.example.yaml /app/config/config.yaml

EXPOSE 8443
HEALTHCHECK --interval=30s --timeout=8s --start-period=120s --retries=5 \
    CMD curl -fk https://127.0.0.1:8443/v1/health || exit 1

WORKDIR /app/backend
ENTRYPOINT ["python", "-m", "app.main", "--no-browser", "--host", "0.0.0.0"]
