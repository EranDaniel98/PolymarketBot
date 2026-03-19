import argparse
import asyncio
import sys


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument(
        "command", nargs="?", default="run",
        choices=["run", "backtest"],
        help="Command to execute (default: run)",
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Number of days for backtesting (default: 30)",
    )
    parser.add_argument(
        "--balance", type=float, default=309.0,
        help="Starting balance for backtesting (default: 309.0)",
    )
    args = parser.parse_args()

    if args.command == "backtest":
        from polymarket_bot.backtesting.engine import run_backtest
        try:
            asyncio.run(run_backtest(days=args.days, balance=args.balance))
        except KeyboardInterrupt:
            sys.exit(0)
    else:
        from polymarket_bot.app import run_bot
        try:
            asyncio.run(run_bot(config_path=args.config))
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()
