#!/usr/bin/env python3
"""Generate RSA-2048 keypair for OpenBrep Pro package signing."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    keys_dir = root / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)

    private_path = keys_dir / "pro_private.pem"
    public_path = keys_dir / "pro_public.pem"

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path.write_bytes(private_bytes)
    public_path.write_bytes(public_bytes)

    print(f"✅ Private key: {private_path}")
    print(f"✅ Public key:  {public_path}")
    print("⚠️ 私钥请妥善保管，不要上传 GitHub")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
