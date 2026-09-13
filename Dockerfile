FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KALYX_ENV=production \
    KALYX_DB=/app/data/kalyx.db

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY apps ./apps
COPY scripts ./scripts
COPY migrations ./migrations

# Install runtime + optional postgres driver so production images can use
# KALYX_DATABASE_URL without a separate build stage. Credentials come only
# from environment at runtime — never baked into the image.
RUN pip install --no-cache-dir ".[postgres]" && mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["uvicorn", "src.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
