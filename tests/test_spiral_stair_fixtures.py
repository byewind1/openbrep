from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from openbrep.hsf_project import HSFProject
from openbrep.source_fingerprint import compute_source_fingerprint

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "spiral_stair"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
EVENTS_PATH = FIXTURE_ROOT / "fake_tool_sequence.json"
STATE_NAMES = ("before_top_option", "partial_top_option", "after_top_option")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _changed_files(left: Path, right: Path, managed_files: list[str]) -> list[str]:
    return [rel for rel in managed_files if (left / rel).read_bytes() != (right / rel).read_bytes()]


@pytest.mark.parametrize("state_name", STATE_NAMES)
def test_f01_hsf_states_load_and_match_manifest(state_name: str) -> None:
    manifest = _load_json(MANIFEST_PATH)
    state = manifest["states"][state_name]
    state_root = FIXTURE_ROOT / state_name

    assert sorted(_file_hashes(state_root)) == sorted(manifest["managed_files"])
    assert _file_hashes(state_root) == state["files"]
    assert compute_source_fingerprint(state_root) == state["source_fingerprint"]

    project = HSFProject.load_from_disk(state_root)
    assert project.parameters
    assert project.scripts


def test_f02_partial_and_retry_diffs_are_exact_and_semantically_distinct() -> None:
    manifest = _load_json(MANIFEST_PATH)
    managed = manifest["managed_files"]
    before = FIXTURE_ROOT / "before_top_option"
    partial = FIXTURE_ROOT / "partial_top_option"
    after = FIXTURE_ROOT / "after_top_option"

    assert _changed_files(before, partial, managed) == ["paramlist.xml", "scripts/1d.gdl"]
    assert _changed_files(partial, after, managed) == ["scripts/3d.gdl"]
    assert manifest["expected_transitions"]["before_to_partial"]["changed_files"] == [
        "paramlist.xml",
        "scripts/1d.gdl",
    ]
    assert manifest["expected_transitions"]["partial_to_after"]["changed_files"] == [
        "scripts/3d.gdl"
    ]

    before_project = HSFProject.load_from_disk(before)
    partial_project = HSFProject.load_from_disk(partial)
    assert before_project.get_parameter("show_top_tread") is None
    assert sum(p.name == "show_top_tread" for p in partial_project.parameters) == 1

    partial_1d = (partial / "scripts/1d.gdl").read_text(encoding="utf-8-sig")
    partial_3d = (partial / "scripts/3d.gdl").read_text(encoding="utf-8-sig")
    after_3d = (after / "scripts/3d.gdl").read_text(encoding="utf-8-sig")
    assert "show_top_tread" in partial_1d
    assert "_tread_count" in partial_1d
    assert "_tread_count" not in partial_3d
    assert "GOSUB 4000" in partial_3d
    assert "MATERIAL mat_landing" in partial_3d
    assert "_tread_count" in after_3d
    assert "GOSUB 4000" not in after_3d
    assert "MATERIAL mat_landing" not in after_3d


def test_f03_fixture_contains_only_minimal_sanitized_source_and_events() -> None:
    manifest = _load_json(MANIFEST_PATH)
    expected_root_files = {"manifest.json", "fake_tool_sequence.json"}
    actual_root_files = {path.name for path in FIXTURE_ROOT.iterdir() if path.is_file()}
    assert actual_root_files == expected_root_files

    forbidden_suffixes = {".gsm", ".png", ".jpg", ".jpeg", ".pdf"}
    forbidden_parts = {".openbrep", "article", "screenshots"}
    forbidden_text = (
        "/Users/",
        "openbrep-workspace",
        "user_instruction",
        "Authorization: Bearer",
        "BEGIN PRIVATE KEY",
    )
    for path in FIXTURE_ROOT.rglob("*"):
        if not path.is_file():
            continue
        assert path.suffix.lower() not in forbidden_suffixes
        assert not forbidden_parts.intersection(path.parts)
        text = path.read_text(encoding="utf-8-sig")
        assert all(marker not in text for marker in forbidden_text)

    for state_name in STATE_NAMES:
        assert sorted(_file_hashes(FIXTURE_ROOT / state_name)) == sorted(manifest["managed_files"])


def test_f04_fake_failure_timeout_retry_sequence_is_offline_and_immutable(tmp_path: Path) -> None:
    manifest = _load_json(MANIFEST_PATH)
    sequence = _load_json(EVENTS_PATH)
    fixture_hashes_before = {
        state: _file_hashes(FIXTURE_ROOT / state) for state in STATE_NAMES
    }
    assert sequence["external_calls"] == {"network": 0, "model": 0, "archicad": 0}

    work = tmp_path / "spiral_stair"
    shutil.copytree(FIXTURE_ROOT / "before_top_option", work)
    assert compute_source_fingerprint(work) == manifest["states"]["before_top_option"][
        "source_fingerprint"
    ]

    current_state = "before_top_option"
    patch_misses = 0
    for event in sequence["events"]:
        if event["kind"] == "tool_result":
            assert event["state"] == current_state
            assert event["error_code"] == "MATCH_NOT_FOUND"
            assert event["match_count"] == 0
            assert not event["ok"]
            patch_misses += 1
        elif event["kind"] == "state_transition":
            assert event["from"] == current_state
            target = FIXTURE_ROOT / event["to"]
            for rel in event["changed_files"]:
                destination = work / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target / rel, destination)
            current_state = event["to"]
            assert compute_source_fingerprint(work) == manifest["states"][current_state][
                "source_fingerprint"
            ]
        elif event["kind"] == "interruption":
            assert event["error_code"] == "TIMEOUT"
            assert event["state"] == current_state == "partial_top_option"

    assert patch_misses == 2
    assert current_state == "after_top_option"
    skill_result = next(
        event for event in sequence["events"] if event["id"] == "skill_request_checked_only"
    )
    assert skill_result["result"] == "checked_without_artifact"
    assert skill_result["artifacts"] == []
    assert not (work / ".openbrep").exists()

    assert {
        state: _file_hashes(FIXTURE_ROOT / state) for state in STATE_NAMES
    } == fixture_hashes_before
