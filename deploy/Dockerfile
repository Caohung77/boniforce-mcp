FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

RUN useradd -r -u 10001 boniforce && mkdir -p /var/lib/boniforce-mcp \
    && chown -R boniforce:boniforce /var/lib/boniforce-mcp
USER boniforce

EXPOSE 8000
CMD ["uvicorn", "boniforce_mcp.server:app", "--host", "0.0.0.0", "--port", "8000"]
