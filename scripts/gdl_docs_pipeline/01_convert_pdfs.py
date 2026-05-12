"""
Step 1: Convert local GDL PDF references to Markdown.

Set GDL_PDF_DIR to the directory containing these files before running:
- GDL_Reference_Guide_28.pdf
- GDLHandbook.pdf
- COOKBK31.PDF
- CookBook4_complete.pdf

The installed opendataloader-pdf package exposes ``convert(...)`` rather than
the older/nonexistent ``PDFConverter`` class shown in the original plan.
Outputs are normalized to /tmp/gdl_raw/*.md for the next pipeline steps.
"""

from __future__ import annotations

import os
from pathlib import Path

from opendataloader_pdf import convert


PDF_DIR = Path(os.environ.get("GDL_PDF_DIR", ""))
OUTPUT_DIR = Path(os.environ.get("GDL_RAW_OUTPUT_DIR", "/tmp/gdl_raw"))
ARCHIVE_DIR = Path(os.environ.get("GDL_PDF_ARCHIVE_DIR", "generated/gdl_pdf_knowledge/raw"))
OPENJDK_BIN = Path(os.environ.get("OPENJDK_BIN", "/opt/homebrew/opt/openjdk/bin"))

PDF_FILES = {
    "reference_guide": PDF_DIR / "GDL_Reference_Guide_28.pdf",
    "handbook": PDF_DIR / "GDLHandbook.pdf",
    "cookbook_v3": PDF_DIR / "COOKBK31.PDF",
    "cookbook_v4": PDF_DIR / "CookBook4_complete.pdf",
}


def _ensure_java_on_path() -> None:
    if OPENJDK_BIN.exists():
        os.environ["PATH"] = f"{OPENJDK_BIN}{os.pathsep}{os.environ.get('PATH', '')}"


def _find_markdown_output(directory: Path) -> Path:
    outputs = sorted(directory.glob("*.md"))
    if not outputs:
        raise FileNotFoundError(f"No markdown output generated in {directory}")
    if len(outputs) > 1:
        raise RuntimeError(f"Expected one markdown file in {directory}, found: {outputs}")
    return outputs[0]


def convert_pdf(name: str, pdf_path: Path) -> Path | None:
    if not pdf_path.exists():
        print(f"[{name}] missing: {pdf_path}")
        return None

    print(f"\n[{name}] converting: {pdf_path.name}")
    archive_doc_dir = ARCHIVE_DIR / name
    archive_doc_dir.mkdir(parents=True, exist_ok=True)
    for old_file in archive_doc_dir.glob("*.md"):
        old_file.unlink()

    convert(
        input_path=str(pdf_path),
        output_dir=str(archive_doc_dir),
        format="markdown",
        quiet=False,
        threads="1",
    )
    source_md = _find_markdown_output(archive_doc_dir)
    target_md = OUTPUT_DIR / f"{name}.md"
    target_md.write_text(source_md.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

    size_kb = target_md.stat().st_size // 1024
    line_count = target_md.read_text(encoding="utf-8", errors="replace").count("\n")
    print(f"  -> {target_md} ({size_kb} KB, {line_count} lines)")
    return target_md


def main() -> int:
    _ensure_java_on_path()
    if not PDF_DIR.is_dir():
        print("Set GDL_PDF_DIR to the directory containing the source GDL PDFs.")
        return 1
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    converted = 0
    for name, pdf_path in PDF_FILES.items():
        if convert_pdf(name, pdf_path):
            converted += 1

    print(f"\nDone. Converted {converted}/{len(PDF_FILES)} PDFs.")
    return 0 if converted == len(PDF_FILES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
