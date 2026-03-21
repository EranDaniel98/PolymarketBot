#!/bin/sh
# Copy config to volume if it doesn't exist yet
if [ ! -f /app/data/config.yaml ]; then
    cp /app/config.yaml /app/data/config.yaml
fi
exec python -m polymarket_bot run -c /app/data/config.yaml
