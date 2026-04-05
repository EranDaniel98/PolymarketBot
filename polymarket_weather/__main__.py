import asyncio
import sys


def main():
    from polymarket_weather.app import run_bot
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
