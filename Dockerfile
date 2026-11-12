FROM node:22-slim AS notation
WORKDIR /notation
COPY tools/notation/package.json tools/notation/package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim

ENV UV_LINK_MODE=copy DATA_DIR=/data WORKERS=1
RUN pip install uv
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY transc_service ./transc_service
COPY requirements.txt ./
COPY requirements-tablature.txt ./
COPY tools ./tools
COPY --from=notation /notation/node_modules ./tools/notation/node_modules
COPY THIRD_PARTY_NOTICES.md ./
# Keep Torch and torchaudio ABI-matched; default image is CPU-only.
RUN uv pip install --system torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
RUN uv pip install --system -r requirements.txt -r requirements-tablature.txt && uv pip check --system
RUN python tools/install_open_fret.py /usr/local/lib/python3.12/site-packages
ENV HF_HOME=/data/cache/huggingface XDG_CACHE_HOME=/data/cache
ENV OPEN_FRET_ALLOW_DOWNLOAD=false OPEN_FRET_DEVICE=cpu OMP_NUM_THREADS=2

VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "transc_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
