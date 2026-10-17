FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 FASTEMBED_CACHE_PATH=/models NUMBA_CACHE_DIR=/tmp/numba
# libgomp1 is needed by the ONNX runtime that runs the embedding model
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock .
RUN pip install -r requirements.lock
# bake the embedding model into the image so first start does not need to download it
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
COPY app ./app
RUN useradd -m app && mkdir -p /data && chown -R app /data /models
USER app
ENV DATABASE_URL=sqlite:////data/mail.db
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)"
# ONE worker on purpose: the 20-minute sync scheduler runs inside the app and must not be duplicated
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
