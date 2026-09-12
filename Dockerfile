FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KALYX_DB=/app/data/kalyx.db

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY apps ./apps
COPY scripts ./scripts

RUN pip install --no-cache-dir . && mkdir -p /app/data

EXPOSE 8000
CMD ["uvicorn", "src.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
