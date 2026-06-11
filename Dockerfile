# Python 3.11 is required — 3.12+ breaks Mem0 and ChromaDB (see README/CLAUDE.md).
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Bake the local embedding model into the image so the first startup doesn't
# stall downloading it at runtime. Must match EMBEDDING_MODEL in config/settings.py.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

COPY . .

RUN mkdir -p data/chroma_db data/uploads

EXPOSE 7860
CMD ["python", "main.py"]
