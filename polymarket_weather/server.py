"""Railway entrypoint — runs FastAPI dashboard and bot loop in one process."""

import asyncio
import logging
import os
import signal
import sys
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

    # Phase 3.1/3.7: structured concurrency + graceful SIGTERM.
    # asyncio.TaskGroup gives us automatic cancellation propagation: if one
    # task crashes, all siblings get cancelled. A SIGTERM handler cancels the
    # tasks directly, giving interval_runner a chance to propagate
    # CancelledError through the bot's shutdown sequence.
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _handle_signal(sig_name: str) -> None:
        logger.info("received %s — initiating graceful shutdown", sig_name)
        shutdown_event.set()
        server.should_exit = True

    if sys.platform != "win32":
        # Windows ProactorEventLoop doesn't support add_signal_handler for SIGTERM.
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _handle_signal, sig.name)
            except NotImplementedError:
                pass

    try:
        async with asyncio.TaskGroup() as tg:
            bot_task = tg.create_task(run_bot(config_path), name="bot")
            tg.create_task(server.serve(), name="web")

            async def _wait_for_signal() -> None:
                await shutdown_event.wait()
                bot_task.cancel()
                # server.should_exit is already True — uvicorn will wind down.

            tg.create_task(_wait_for_signal(), name="signal_watcher")
    except* asyncio.CancelledError:
        logger.info("shutdown: all tasks cancelled cleanly")


if __name__ == "__main__":
    asyncio.run(main())
