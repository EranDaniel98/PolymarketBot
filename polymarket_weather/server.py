"""Railway entrypoint — runs FastAPI dashboard and bot loop in one process."""

import asyncio
import logging
import os
from pathlib import Path

import uvicorn

from polymarket_weather.app import run_bot
from polymarket_weather.logging_filters import install_on_root as install_log_redaction

logger = logging.getLogger("polymarket_weather.server")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # Install secret-redaction filter on the root logger BEFORE any other module
    # logs. This catches accidental leaks of private keys, telegram tokens, and
    # DB URLs with credentials in log messages and exception tracebacks.
    install_log_redaction()

    # Import dashboard (and therefore install_auth()) AFTER the redaction filter
    # is in place, so any startup-time auth errors are also redacted.
    from polymarket_weather.api.dashboard import app, mount_frontend
    from polymarket_weather.config import load_config

    config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    config = load_config(Path(config_path))

    # Mount built React frontend if present
    mount_frontend("frontend/dist")

    uvicorn_config = uvicorn.Config(
        app,
        host=config.web.host,
        port=config.web.port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(uvicorn_config)

    # Run bot loop and web server concurrently
    bot_task = asyncio.create_task(run_bot(config_path), name="bot")
    web_task = asyncio.create_task(server.serve(), name="web")

    done, pending = await asyncio.wait(
        {bot_task, web_task}, return_when=asyncio.FIRST_EXCEPTION
    )
    for task in pending:
        task.cancel()
    for task in done:
        exc = task.exception()
        if exc:
            logger.error("%s task crashed: %s", task.get_name(), exc)
            raise exc


if __name__ == "__main__":
    asyncio.run(main())
