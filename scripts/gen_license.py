#!/usr/bin/env python3
"""Generate OpenBrep Pro license code and append to licenses.csv."""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import secrets
import string
from datetime import datetime
from pathlib import Path

FIELDS = ["buyer_id", "email", "plan", "issued_at", "expire_date", "status", "license_code", "salt"]


def _to_base36(num: int) -> str:
    chars = string.digits + string.ascii_uppercase
    if num == 0:
        return "0"
    out: list[str] = []
    n = num
    while n:
        n, r = divmod(n, 36)
        out.append(chars[r])
    return "".join(reversed(out))


def _get_secret(root: Path) -> bytes:
    env = __import__("os").environ.get("OPENBREP_LICENSE_SECRET", "")
    if env:
        return env.encode("utf-8")

    key_file = root / "keys" / "license_secret.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip().encode("utf-8")

    key_file.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(32)
    key_file.write_text(secret, encoding="utf-8")
    return secret.encode("utf-8")


def _gen_code(secret: bytes, buyer_id: str, expire_date: str) -> tuple[str, str]:
    salt = secrets.token_hex(4)
    payload = f"{buyer_id}|{expire_date}|{salt}".encode("utf-8")
    digest = hmac.new(secret, payload, hashlib.sha256).hexdigest()[:12].upper()
    token = _to_base36(int(digest, 16)).zfill(12)[:12]
    code = f"OBR-{token[0:4]}-{token[4:8]}-{token[8:12]}"
    return code, salt


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate OpenBrep Pro license code")
    parser.add_argument("--buyer-id", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--plan", required=True, choices=["annual", "lifetime"])
    parser.add_argument("--expire", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    secret = _get_secret(root)

    code, salt = _gen_code(secret, args.buyer_id, args.expire)
    row = {
        "buyer_id": args.buyer_id,
        "email": args.email,
        "plan": args.plan,
        "issued_at": datetime.now().isoformat(timespec="seconds"),
        "expire_date": args.expire,
        "status": "active",
        "license_code": code,
        "salt": salt,
    }

    csv_path = root / "licenses.csv"
    exists = csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)

    print("✅ License generated")
    print(f"Code: {code}")
    print(f"Saved: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
