FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY polymarket_bot/ polymarket_bot/
COPY config.railway.yaml config.yaml
COPY entrypoint.sh .

RUN pip install --no-cache-dir ".[web]" && chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1

CMD ["./entrypoint.sh"]
