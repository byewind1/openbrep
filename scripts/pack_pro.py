#!/usr/bin/env python3
"""Pack Pro knowledge docs into signed .obrk package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _watermark(buyer_id: str, rel_path: str) -> str:
    checksum = hashlib.sha256(f"{buyer_id}:{rel_path}".encode("utf-8")).hexdigest()[:12]
    return f"<!-- obr:buyer:{buyer_id}:{checksum} -->"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create signed Pro knowledge package (.obrk)")
    parser.add_argument("--buyer-id", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--plan", required=True, choices=["annual", "lifetime"])
    parser.add_argument("--expire", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    src_docs = root / "knowledge" / "ccgdl_dev_doc" / "docs"
    private_key_path = root / "keys" / "pro_private.pem"
    out_dir = root / "releases"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not src_docs.exists():
        raise FileNotFoundError(f"Pro docs not found: {src_docs}")
    if not private_key_path.exists():
        raise FileNotFoundError(f"Private key not found: {private_key_path}")

    md_files = sorted(src_docs.glob("*.md"))
    if not md_files:
        raise RuntimeError("No .md files found in knowledge/ccgdl_dev_doc/docs/")

    with tempfile.TemporaryDirectory(prefix="openbrep_pro_") as td:
        temp_root = Path(td)
        docs_out = temp_root / "docs"
        docs_out.mkdir(parents=True, exist_ok=True)

        for md in md_files:
            rel_name = md.name
            content = md.read_text(encoding="utf-8").rstrip() + "\n\n" + _watermark(args.buyer_id, rel_name) + "\n"
            (docs_out / rel_name).write_text(content, encoding="utf-8")

        manifest = {
            "buyer_id": args.buyer_id,
            "email": args.email,
            "plan": args.plan,
            "expire_date": args.expire,
            "issued_at": datetime.now().isoformat(timespec="seconds"),
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        (temp_root / "manifest.json").write_bytes(manifest_bytes)

        private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
        signature = private_key.sign(
            manifest_bytes,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        (temp_root / "signature.sig").write_bytes(signature)

        out_file = out_dir / f"{args.buyer_id}_pro.obrk"
        if out_file.exists():
            out_file.unlink()

        with zipfile.ZipFile(out_file, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(temp_root / "manifest.json", arcname="manifest.json")
            zf.write(temp_root / "signature.sig", arcname="signature.sig")
            for p in sorted(docs_out.glob("*.md")):
                zf.write(p, arcname=f"docs/{p.name}")

    print(f"✅ Pro package created: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
