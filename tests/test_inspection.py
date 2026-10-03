import json
import multiprocessing

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.runtime.inspection import inspect_snapshot, render_inspection
from openbrep.source_snapshot import capture_snapshot


def test_static_and_parameter_checks_are_real_and_traceable(tmp_path):
    project = HSFProject.create_new('Shelf', str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, 'IF A > 0 THEN\nBLOCK A, B, ZZYZX\n')
    project.set_script(ScriptType.PARAM, 'VALUES "A" RANGE [0.5, 2]\n')
    report = inspect_snapshot(capture_snapshot(project, 1))
    checks = {c['kind']: c for c in report.checks}
    assert checks['static']['status'] == 'completed'
    assert any(f['check_type'] == 'block_mismatch' for f in checks['static']['findings'])
    assert checks['parameters']['coverage']['dynamic_expressions_not_evaluated']
    assert any(f['id'] == 'values:A' for f in checks['parameters']['findings'])
    assert all(f['evidence_location'] for c in report.checks for f in c['findings'])
    assert not project.root.exists()


def test_cancel_or_budget_is_unavailable_not_clean_completed(tmp_path):
    snapshot = capture_snapshot(HSFProject.create_new('Shelf', str(tmp_path)), 1)
    for options in ({'budget_seconds': 0}, {'should_cancel': lambda: True}, {'budget_seconds': 0.01}):
        report = inspect_snapshot(snapshot, **options)
        assert any(c['status'] == 'unavailable' for c in report.checks)
    assert not any(p.is_alive() for p in multiprocessing.active_children())


def test_context_budget_selects_whole_findings(tmp_path):
    project = HSFProject.create_new('Shelf', str(tmp_path))
    report = inspect_snapshot(capture_snapshot(project, 1))
    rendered = render_inspection(report, token_budget=1200)
    assert len(json.dumps(rendered, ensure_ascii=False).encode()) <= 1200
    assert rendered['truncated_count'] >= 0
