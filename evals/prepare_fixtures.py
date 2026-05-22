"""Prepare deterministic GDL fixture variants for scorecards.

Usage:
  python evals/prepare_fixtures.py
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
VALID_DIR = ROOT / "fixtures" / "gdl_objects"
BROKEN_DIR = ROOT / "fixtures" / "broken_gdl"


def prepare_broken_fixtures(
    valid_dir: Path = VALID_DIR,
    broken_dir: Path = BROKEN_DIR,
) -> list[Path]:
    """Create broken GDL variants from valid fixture scripts."""
    broken_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for source in sorted(valid_dir.glob("*.gdl")):
        text = source.read_text(encoding="utf-8")
        variants = {
            "bad_command": _break_command(text),
            "stack_imbalance": _break_stack(text),
            "misspelled_variable": _break_variable(text),
            "missing_next": _break_for_next(text),
        }
        for variant, body in variants.items():
            target = broken_dir / f"{source.stem}__{variant}.gdl"
            target.write_text(body, encoding="utf-8")
            written.append(target)
    return written


def _break_command(text: str) -> str:
    if "BLOCK" in text:
        return text.replace("BLOCK", "BROKEN_CMD", 1)
    return "BROKEN_CMD A, B, ZZYZX\n" + text


def _break_stack(text: str) -> str:
    if "\nDEL 1" in text:
        return text.replace("\nDEL 1", "\n! removed DEL 1", 1)
    if "\nDEL 2" in text:
        return text.replace("\nDEL 2", "\n! removed DEL 2", 1)
    return "ADDX 1\n" + text


def _break_variable(text: str) -> str:
    if "BLOCK A" in text:
        return text.replace("BLOCK A", "BLOCK AA", 1)
    return text.replace(" A", " AA", 1)


def _break_for_next(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().upper().startswith("NEXT"):
            lines[index] = "! removed " + line.strip()
            return "\n".join(lines) + "\n"
    return text.replace("END", "FOR i = 1 TO 2\nEND", 1)


def main() -> int:
    written = prepare_broken_fixtures()
    print(f"prepared {len(written)} broken fixtures in {BROKEN_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
