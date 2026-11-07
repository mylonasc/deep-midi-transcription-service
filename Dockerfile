FROM python:3.12-slim

ENV UV_LINK_MODE=copy DATA_DIR=/data WORKERS=1
RUN pip install uv
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY transc_service ./transc_service
COPY requirements.txt ./
RUN uv pip install --system -r requirements.txt
ENV HF_HOME=/data/cache/huggingface XDG_CACHE_HOME=/data/cache

VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "transc_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
