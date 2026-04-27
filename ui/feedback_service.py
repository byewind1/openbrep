from __future__ import annotations

import datetime
import json
from pathlib import Path


def save_feedback(work_dir: str, msg_idx: int, rating: str, content: str, comment: str = "") -> None:
    """Save local feedback without letting persistence failures break the UI."""
    try:
        feedback_path = Path(work_dir) / "feedback.jsonl"
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.datetime.now().isoformat(),
            "rating": rating,
            "msg_idx": msg_idx,
            "preview": content[:300],
            "comment": comment.strip(),
        }
        with feedback_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
