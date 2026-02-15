"""
LP_XMLConverter Wrapper — HSF-native compilation.

v0.4: Primary command is hsf2libpart (HSF directory → .gsm).
Also supports l2hsf / libpart2hsf for decompiling existing .gsm files.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class CompileResult:
    """Result of a compilation attempt."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    output_path: str = ""
    errors: list[str] = None
    warnings: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
        if self.stderr:
            self._parse_log()

    def _parse_log(self):
        """Extract structured error/warning info from stderr."""
        for line in self.stderr.splitlines():
            line_stripped = line.strip()
            if not line_stripped:
                continue
            lower = line_stripped.lower()
            if "error" in lower:
                self.errors.append(line_stripped)
            elif "warning" in lower:
                self.warnings.append(line_stripped)


class HSFCompiler:
    """
    Wraps LP_XMLConverter for HSF compilation.

    Usage:
        compiler = HSFCompiler()  # auto-detect path
        result = compiler.hsf2libpart("MyObject/", "output/MyObject.gsm")
    """

    def __init__(self, converter_path: Optional[str] = None, timeout: int = 60):
        self.converter_path = converter_path or self._detect_converter()
        self.timeout = timeout

    def hsf2libpart(self, hsf_dir: str, output_gsm: str) -> CompileResult:
        """
        Compile HSF directory → .gsm file.

        Args:
            hsf_dir: Path to HSF root directory (contains libpartdata.xml)
            output_gsm: Path to output .gsm file
        """
        hsf_path = Path(hsf_dir)
        if not hsf_path.is_dir():
            return CompileResult(
                success=False, exit_code=1,
                stderr=f"HSF directory not found: {hsf_dir}"
            )

        # Verify libpartdata.xml exists
        if not (hsf_path / "libpartdata.xml").exists():
            return CompileResult(
                success=False, exit_code=1,
                stderr=f"Not a valid HSF directory (missing libpartdata.xml): {hsf_dir}"
            )

        # Ensure output directory exists
        Path(output_gsm).parent.mkdir(parents=True, exist_ok=True)

        return self._run_converter("hsf2libpart", str(hsf_path), output_gsm)

    def libpart2hsf(self, gsm_path: str, output_dir: str) -> CompileResult:
        """
        Decompile .gsm → HSF directory.

        Args:
            gsm_path: Path to .gsm file
            output_dir: Path to output HSF directory
        """
        if not Path(gsm_path).exists():
            return CompileResult(
                success=False, exit_code=1,
                stderr=f"GSM file not found: {gsm_path}"
            )

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        return self._run_converter("libpart2hsf", gsm_path, output_dir)

    def _run_converter(self, command: str, source: str, dest: str) -> CompileResult:
        """Execute LP_XMLConverter with given command."""
        if not self.converter_path:
            return CompileResult(
                success=False, exit_code=127,
                stderr="LP_XMLConverter not found. Install ArchiCAD or set path in config."
            )

        cmd = [self.converter_path, command, source, dest]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
            )

            return CompileResult(
                success=(proc.returncode == 0),
                stdout=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
                output_path=dest if proc.returncode == 0 else "",
            )

        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False, exit_code=-1,
                stderr=f"Compilation timed out after {self.timeout}s"
            )
        except FileNotFoundError:
            return CompileResult(
                success=False, exit_code=127,
                stderr=f"LP_XMLConverter not found at: {self.converter_path}"
            )
        except Exception as e:
            return CompileResult(
                success=False, exit_code=-1,
                stderr=f"Compilation failed: {e}"
            )

    @staticmethod
    def _detect_converter() -> Optional[str]:
        """Auto-detect LP_XMLConverter path."""
        system = platform.system()

        if system == "Darwin":  # macOS
            # Search common ArchiCAD install locations
            base = Path("/Applications/GRAPHISOFT")
            if base.exists():
                for ac_dir in sorted(base.iterdir(), reverse=True):
                    converter = ac_dir / "LP_XMLConverter"
                    if converter.exists():
                        return str(converter)
            # Also check PATH
            result = shutil.which("LP_XMLConverter")
            if result:
                return result

        elif system == "Windows":
            # Common Windows install paths
            for drive in ("C:", "D:"):
                base = Path(f"{drive}/Program Files/GRAPHISOFT")
                if base.exists():
                    for ac_dir in sorted(base.iterdir(), reverse=True):
                        converter = ac_dir / "LP_XMLConverter.exe"
                        if converter.exists():
                            return str(converter)
            result = shutil.which("LP_XMLConverter")
            if result:
                return result

        else:  # Linux
            return shutil.which("LP_XMLConverter")

        return None

    @property
    def is_available(self) -> bool:
        """Check if LP_XMLConverter is available."""
        return self.converter_path is not None and Path(self.converter_path).exists()


class MockHSFCompiler:
    """
    Mock compiler for testing without ArchiCAD.
    Validates HSF structure without calling LP_XMLConverter.
    """

    def hsf2libpart(self, hsf_dir: str, output_gsm: str) -> CompileResult:
        """Mock compile: validates structure, writes fake .gsm."""
        hsf_path = Path(hsf_dir)

        errors = []

        # Check directory exists
        if not hsf_path.is_dir():
            return CompileResult(
                success=False, exit_code=1,
                stderr=f"HSF directory not found: {hsf_dir}"
            )

        # Check required files
        if not (hsf_path / "libpartdata.xml").exists():
            errors.append("Missing libpartdata.xml")
        if not (hsf_path / "paramlist.xml").exists():
            errors.append("Missing paramlist.xml")

        # Validate paramlist.xml if it exists
        paramlist_path = hsf_path / "paramlist.xml"
        if paramlist_path.exists():
            content = paramlist_path.read_text(encoding="utf-8-sig")
            try:
                import xml.etree.ElementTree as ET
                ET.fromstring(content)
            except ET.ParseError as e:
                errors.append(f"paramlist.xml parse error: {e}")

        # Validate scripts (basic GDL checks)
        scripts_dir = hsf_path / "scripts"
        if scripts_dir.is_dir():
            for gdl_file in scripts_dir.glob("*.gdl"):
                script = gdl_file.read_text(encoding="utf-8-sig")
                script_errors = self._check_gdl_basic(gdl_file.name, script)
                errors.extend(script_errors)

        if errors:
            return CompileResult(
                success=False, exit_code=1,
                stderr="\n".join(errors)
            )

        # Success: write mock .gsm
        Path(output_gsm).parent.mkdir(parents=True, exist_ok=True)
        Path(output_gsm).write_text(
            f"[MOCK GSM] Compiled from {hsf_dir}",
            encoding="utf-8"
        )

        return CompileResult(
            success=True, exit_code=0,
            stdout=f"Successfully compiled: {output_gsm}",
            output_path=output_gsm,
        )

    def libpart2hsf(self, gsm_path: str, output_dir: str) -> CompileResult:
        """Mock decompile: not supported in mock mode."""
        return CompileResult(
            success=False, exit_code=1,
            stderr="Mock compiler does not support decompilation"
        )

    @staticmethod
    def _check_gdl_basic(filename: str, script: str) -> list[str]:
        """Basic GDL structure checks."""
        errors = []

        # Count IF/ENDIF (excluding single-line IF THEN)
        lines = script.splitlines()
        if_count = 0
        endif_count = 0

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("!"):
                continue

            # Multi-line IF (no executable code after THEN on same line)
            if re.match(r'\bIF\b', stripped, re.IGNORECASE):
                then_match = re.search(r'\bTHEN\b(.+)', stripped, re.IGNORECASE)
                if then_match:
                    after_then = then_match.group(1).strip()
                    if not after_then or after_then.startswith("!"):
                        if_count += 1  # Multi-line IF
                    # else: single-line IF THEN ..., no ENDIF needed
                else:
                    if_count += 1  # IF without THEN on same line

            if re.match(r'\bENDIF\b', stripped, re.IGNORECASE):
                endif_count += 1

        if if_count != endif_count:
            errors.append(
                f"Error in {filename}: IF/ENDIF mismatch "
                f"(IF: {if_count}, ENDIF: {endif_count})"
            )

        # Count FOR/NEXT
        for_count = len(re.findall(r'\bFOR\b', script, re.IGNORECASE))
        next_count = len(re.findall(r'\bNEXT\b', script, re.IGNORECASE))
        if for_count != next_count:
            errors.append(
                f"Error in {filename}: FOR/NEXT mismatch "
                f"(FOR: {for_count}, NEXT: {next_count})"
            )

        return errors
