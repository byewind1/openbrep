"""
BS2G warning collector.

Accumulates warnings for unsupported operations encountered during
parsing.  Warnings are surfaced as GDL comments in the generated
output and optionally reported to the LLM completion layer.
"""

from __future__ import annotations

from openbrep.importers.blender_script.ir import IRUnsupported


class WarningCollector:
    """Collects IRUnsupported nodes during parsing."""

    def __init__(self) -> None:
        self._warnings: list[IRUnsupported] = []

    def add(
        self,
        operation: str,
        reason: str,
        line: int,
        source_line: str = "",
    ) -> IRUnsupported:
        node = IRUnsupported(
            operation=operation,
            reason=reason,
            line=line,
            source_line=source_line,
        )
        self._warnings.append(node)
        return node

    @property
    def warnings(self) -> list[IRUnsupported]:
        return list(self._warnings)

    def __len__(self) -> int:
        return len(self._warnings)

    def __bool__(self) -> bool:
        return len(self._warnings) > 0
