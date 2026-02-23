"""
Sandbox Compilation — protect source files from AI corruption.

The #1 production risk: a failed LLM attempt overwrites the source XML,
leaving the project in an uncompilable state. This module ensures that:

1. AI-generated code is ALWAYS written to a temp file first
2. Compilation happens on the temp file
3. Source is updated ONLY after compile success + validation pass
4. Failed attempts are preserved for debugging (temp/attempt_N.xml)

Design: The source file is treated as a "golden copy" — immutable until
proven correct. This is the same philosophy as database transactions:
write to WAL first, commit on success.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SandboxPaths:
    """Paths for a sandboxed compilation attempt."""
    source_original: Path      # The golden source file (read-only during attempts)
    temp_xml: Path             # Where AI output is written for compilation
    temp_output: Path          # Where .gsm is compiled to
    final_output: Path         # Where .gsm goes after success
    attempt: int = 0

    @property
    def attempt_archive(self) -> Path:
        """Path to archive this attempt's XML for debugging."""
        return self.temp_xml.parent / f"attempt_{self.attempt:03d}.xml"


class Sandbox:
    """
    Manages sandboxed compilation workspace.

    Usage:
        sandbox = Sandbox(src_dir="./src", temp_dir="./temp", output_dir="./output")

        # Get paths for an attempt
        paths = sandbox.prepare("window.xml", "window.gsm", attempt=1)

        # Write AI output to temp
        sandbox.write_temp(paths, ai_generated_xml)

        # Compile from temp (NOT from source)
        compiler.compile(str(paths.temp_xml), str(paths.temp_output))

        # On success: promote temp to source + output
        sandbox.promote(paths)

        # On failure: archive the attempt for debugging
        sandbox.archive_attempt(paths)
    """

    def __init__(
        self,
        src_dir: str = "./src",
        temp_dir: str = "./temp",
        output_dir: str = "./output",
    ):
        self.src_dir = Path(src_dir)
        self.temp_dir = Path(temp_dir)
        self.output_dir = Path(output_dir)

    def ensure_dirs(self) -> None:
        """Create sandbox directories."""
        for d in [self.src_dir, self.temp_dir, self.output_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def prepare(self, source_name: str, output_name: str, attempt: int = 1) -> SandboxPaths:
        """
        Prepare sandbox paths for a compilation attempt.

        Args:
            source_name: Filename of the source XML (e.g., "window.xml")
            output_name: Filename of the output GSM (e.g., "window.gsm")
            attempt: Current attempt number

        Returns:
            SandboxPaths with all resolved paths.
        """
        self.ensure_dirs()

        return SandboxPaths(
            source_original=self.src_dir / source_name,
            temp_xml=self.temp_dir / f"_wip_{source_name}",
            temp_output=self.temp_dir / f"_wip_{output_name}",
            final_output=self.output_dir / output_name,
            attempt=attempt,
        )

    def read_source(self, paths: SandboxPaths) -> str:
        """Read the golden source file (if it exists)."""
        if paths.source_original.exists():
            return paths.source_original.read_text(encoding="utf-8")
        return ""

    def write_temp(self, paths: SandboxPaths, content: str) -> None:
        """Write AI-generated content to the temp location (NOT source)."""
        paths.temp_xml.parent.mkdir(parents=True, exist_ok=True)
        paths.temp_xml.write_text(content, encoding="utf-8")

    def promote(self, paths: SandboxPaths) -> None:
        """
        Promote a successful temp build to source + output.

        This is the ONLY way source files get modified.
        Called only after compile success + all validations pass.
        """
        # Copy temp XML → source (atomic-ish update)
        if paths.temp_xml.exists():
            paths.source_original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(paths.temp_xml), str(paths.source_original))

        # Copy temp output → final output
        if paths.temp_output.exists():
            paths.final_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(paths.temp_output), str(paths.final_output))

    def archive_attempt(self, paths: SandboxPaths) -> None:
        """Archive a failed attempt for debugging."""
        if paths.temp_xml.exists():
            archive = paths.attempt_archive
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(paths.temp_xml), str(archive))

    def cleanup(self) -> None:
        """Remove temp working files (keep archives)."""
        for f in self.temp_dir.glob("_wip_*"):
            f.unlink(missing_ok=True)

    def get_attempt_history(self) -> list[Path]:
        """List all archived attempt files."""
        if not self.temp_dir.exists():
            return []
        return sorted(self.temp_dir.glob("attempt_*.xml"))
