FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt asyncpg

# pre-bake the similarity models: no internet needed at runtime, instant warm start
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-base-en-v1.5')" && \
    python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('jinaai/jina-reranker-v1-turbo-en')"

COPY app ./app
COPY alembic.ini .
COPY migrations ./migrations

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["sh", "-c", "alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
