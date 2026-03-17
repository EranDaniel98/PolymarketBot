import asyncio
import sys

def main():
    from polymarket_bot.app import run_bot
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == "__main__":
    main()
