import argparse
import asyncio
import sys


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument(
        "-c", "--config", default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    args = parser.parse_args()

    from polymarket_bot.app import run_bot
    try:
        asyncio.run(run_bot(config_path=args.config))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
