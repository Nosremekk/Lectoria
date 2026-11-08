# syntax=docker/dockerfile:1
# ==============================================================================
# Lectoria — Dockerfile Oficial de Produção
# Servidor Pessoal de Livros Digitais (FastAPI + SQLite + PDF.js + EPUB.js)
# ==============================================================================

# Estágio 1: Construção do ambiente virtual isolado
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY requirements.txt ./

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# Estágio 2: Imagem final enxuta de execução
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Lectoria" \
      org.opencontainers.image.description="Servidor pessoal auto-hospedado para gerenciamento e leitura de livros digitais (PDF/EPUB)" \
      org.opencontainers.image.source="https://github.com/Nosremekk/Lectoria" \
      org.opencontainers.image.licenses="MIT"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MALLOC_ARENA_MAX=2 \
    LECTORIA_HOST="0.0.0.0" \
    LECTORIA_PORT="8765" \
    LECTORIA_DATA_DIR="/app/data" \
    LECTORIA_LIBRARY_DIR="/app/books" \
    LECTORIA_BACKUPS_DIR="/app/backups"

# Instala sqlite3 CLI para inspeção e rotinas de backup/integridade
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia o ambiente virtual preparado no estágio builder
COPY --from=builder /opt/venv /opt/venv

# Copia o código-fonte da aplicação e recursos estáticos
COPY *.py ./
COPY static ./static
COPY scripts ./scripts
COPY deployment ./deployment
COPY requirements.txt .env.example LICENSE README.md CHANGELOG.md ./

# Pré-compila bytecode e garante a estrutura de diretórios para volumes persistentes
RUN python -m compileall -q /app \
    && mkdir -p /app/data /app/data/covers /app/books /app/backups \
    && chmod +x /app/scripts/*.py /app/deployment/*.sh

VOLUME ["/app/data", "/app/books", "/app/backups"]

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)"]

# 1 worker: SQLite opera em modo WAL com escritor único e tarefas agendadas no processo
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
