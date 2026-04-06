"""One-shot script to derive Polymarket CLOB API credentials from a private key.

Usage:
    1. Ensure POLYMARKET_PRIVATE_KEY is set in your local .env file (with 0x prefix).
    2. Run:  python scripts/generate_clob_creds.py
    3. Copy the three printed values into your password manager.
    4. Push them to Railway:
         railway variables --set "POLYMARKET_API_KEY=..." \
                           --set "POLYMARKET_API_SECRET=..." \
                           --set "POLYMARKET_API_PASSPHRASE=..."

This script:
  - Loads .env via python-dotenv
  - Reads POLYMARKET_PRIVATE_KEY
  - Asks the Polymarket CLOB to create-or-derive L2 API credentials via EIP-712 signature
  - Prints the three credentials to stdout (NOT to logs or any file)
  - Does NOT touch Railway, git, or config.yaml
"""

import os
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()

    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
    if not private_key:
        print("ERROR: POLYMARKET_PRIVATE_KEY not set in environment or .env", file=sys.stderr)
        return 1
    if not private_key.startswith("0x"):
        print("ERROR: POLYMARKET_PRIVATE_KEY must start with '0x'", file=sys.stderr)
        return 1

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON
    except ImportError:
        print("ERROR: py-clob-client not installed. Run: pip install py-clob-client", file=sys.stderr)
        return 1

    host = os.environ.get("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com")
    print(f"Connecting to {host} (chain_id={POLYGON})...", file=sys.stderr)

    client = ClobClient(host, key=private_key, chain_id=POLYGON)

    print("Requesting create-or-derive API credentials (signs an EIP-712 message)...", file=sys.stderr)
    try:
        creds = client.create_or_derive_api_creds()
    except Exception as exc:
        print(f"ERROR: credential derivation failed: {exc}", file=sys.stderr)
        return 2

    print("", file=sys.stderr)
    print("=== SAVE THESE THREE VALUES IN YOUR PASSWORD MANAGER ===", file=sys.stderr)
    print("", file=sys.stderr)
    print(f"POLYMARKET_API_KEY={creds.api_key}")
    print(f"POLYMARKET_API_SECRET={creds.api_secret}")
    print(f"POLYMARKET_API_PASSPHRASE={creds.api_passphrase}")
    print("", file=sys.stderr)
    print("Then set them on Railway with:", file=sys.stderr)
    print("  railway variables \\", file=sys.stderr)
    print('    --set "POLYMARKET_API_KEY=<value>" \\', file=sys.stderr)
    print('    --set "POLYMARKET_API_SECRET=<value>" \\', file=sys.stderr)
    print('    --set "POLYMARKET_API_PASSPHRASE=<value>"', file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
