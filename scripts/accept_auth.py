#!/usr/bin/env python3
"""Real-account acceptance: device code -> MSA -> Xbox Live -> XSTS -> Minecraft -> profile.

Precondition: the Azure app must be approved for the **Minecraft Java Edition Game
Service API** (English account types / review). Until then `login_with_xbox` returns
`403 Invalid app registration` -- see `docs/auth-application.md` in the parent repo.

Prints step status only; never prints tokens. Usage:

    uv run --directory python python scripts/accept_auth.py
"""

from __future__ import annotations

import sys

from orzmc import AuthError, authenticate, resolve_client_id


def _on_device_code(device: object) -> None:
    uri = getattr(device, "verification_uri", "")
    code = getattr(device, "user_code", "")
    expires = getattr(device, "expires_in", "")
    print(f"\nOpen {uri} in a browser and enter code: {code} (valid ~{expires}s)\n", file=sys.stderr)


def main() -> int:
    client_id = resolve_client_id()
    if not client_id:
        print("[FAIL] client_id not configured (set ORZMC_AZURE_CLIENT_ID; see docs/spec/auth.md)", file=sys.stderr)
        return 2
    print(f"client_id = {client_id}")

    try:
        session = authenticate(client_id, on_device_code=_on_device_code)
    except AuthError as exc:
        print(f"[FAIL] {exc.code}: {exc.message}")
        if exc.code == "E_AUTH_NO_GAME":
            print("       This account does not own Minecraft: Java Edition.")
        return 1

    print("[ok] authentication succeeded")
    print(f"     name   = {session.name}")
    print(f"     uuid   = {session.uuid}")
    print(f"     source = {session.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
