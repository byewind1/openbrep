from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ui import knowledge_access


@dataclass
class LicenseService:
    root: Path
    has_runtime_context_fn: Callable[[], bool] = lambda: True

    def empty_record(self) -> dict:
        return knowledge_access._empty_license_record()

    def load(self, work_dir: str) -> dict:
        return knowledge_access._load_license(work_dir)

    def save(self, work_dir: str, data: dict) -> None:
        if not self.has_runtime_context_fn():
            return
        knowledge_access._save_license(work_dir, data)

    def verify_pro_code(self, code: str) -> tuple[bool, str, dict | None]:
        return knowledge_access._verify_pro_code(code, root=self.root)

    def record_is_active(self, data: dict) -> tuple[bool, str, dict | None]:
        return knowledge_access._license_record_is_active(data, root=self.root)

    def import_pro_knowledge_zip(self, file_bytes: bytes, filename: str, work_dir: str) -> tuple[bool, str]:
        return knowledge_access._import_pro_knowledge_zip(
            file_bytes,
            filename,
            work_dir,
            root=self.root,
        )
