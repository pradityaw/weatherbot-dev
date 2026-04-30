#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    root = Path(__file__).resolve().parent
    load_env(root / ".env.weatherbot-live")

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    api_key = os.getenv("POLYMARKET_API_KEY", "").strip()
    api_secret = os.getenv("POLYMARKET_SECRET", "").strip()
    api_passphrase = os.getenv("POLYMARKET_PASSPHRASE", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "").strip()

    print("===============================================")
    print("  WEATHERBOT LIVE WALLET READINESS (READ-ONLY)")
    print("===============================================")
    print(f"  private key set:    {'yes' if bool(private_key) else 'no'}")
    print(f"  api key set:        {'yes' if bool(api_key) else 'no'}")
    print(f"  api secret set:     {'yes' if bool(api_secret) else 'no'}")
    print(f"  api passphrase set: {'yes' if bool(api_passphrase) else 'no'}")
    print(f"  funder address set: {'yes' if bool(funder) else 'no'}")

    if not all([private_key, api_key, api_secret, api_passphrase]):
        print("\nMissing required POLYMARKET_* credentials.")
        return 1

    try:
        from py_clob_client.client import ClobClient  # type: ignore
        from py_clob_client.clob_types import ApiCreds  # type: ignore
    except Exception as exc:
        print(f"\npy-clob-client unavailable: {exc}")
        return 2

    try:
        creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            creds=creds,
            funder=funder or None,
        )
        # Read-only smoke test.
        _ = client.get_markets()
        print("\nRead-only API check: OK (get_markets succeeded)")
        return 0
    except Exception as exc:
        print(f"\nRead-only API check failed: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
