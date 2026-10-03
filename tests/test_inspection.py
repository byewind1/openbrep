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


def test_preview_coverage_does_not_call_missing_macro_empty_geometry_a_fact(tmp_path):
    project = HSFProject.create_new('Shelf', str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, 'CALL "missing"\n')
    report = inspect_snapshot(capture_snapshot(project, 1), previews=('preview_3d',))
    check = next(c for c in report.checks if c['kind'] == 'preview_3d')
    assert check['status'] == 'partial'
    assert check['coverage']['dependencies_resolved'] is False
    assert not any(f['id'].endswith(':empty') for f in check['findings'])
    assert any('MACRO' in f['message'] or '宏' in f['message'] for f in check['findings'])


def test_string_branch_and_supported_empty_preview_have_distinct_coverage(tmp_path):
    project = HSFProject.create_new('Shelf', str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, 'IF style = "wood" THEN BLOCK 1,1,1\n')
    report = inspect_snapshot(capture_snapshot(project, 1), previews=('preview_3d',))
    check = next(c for c in report.checks if c['kind'] == 'preview_3d')
    assert check['status'] == 'partial'
    assert check['coverage']['all_string_branches_evaluated'] is False
    assert not any(f['id'].endswith(':empty') for f in check['findings'])
    project.set_script(ScriptType.SCRIPT_3D, 'ADD 1, 0, 0\nDEL 1\n')
    report = inspect_snapshot(capture_snapshot(project, 1), previews=('preview_3d',))
    check = next(c for c in report.checks if c['kind'] == 'preview_3d')
    assert check['status'] == 'completed'
    assert any(f['id'].endswith(':empty') for f in check['findings'])


def test_inspection_cache_requires_dependency_version_and_rebinds_report(tmp_path, monkeypatch):
    import openbrep.runtime.inspection as module
    from unittest.mock import Mock
    project = HSFProject.create_new('Shelf', str(tmp_path))
    unknown = capture_snapshot(project, 1)
    known = capture_snapshot(project, 1, dependency_context_version='library-v1')
    fake = Mock(side_effect=lambda snapshot, **kw: module.InspectionReport('original', snapshot.source_version, 1, (module._check('static', 'completed'),)))
    monkeypatch.setattr(module, 'inspect_snapshot', fake)
    cache = module.InspectionCache()
    cache.inspect(unknown); cache.inspect(unknown)
    assert fake.call_count == 2
    cache.inspect(known)
    reused = cache.inspect(known)
    assert fake.call_count == 3
    assert reused.inspection_id != 'original'
    assert reused.source_version == known.source_version
    cache.inspect(capture_snapshot(project, 1, dependency_context_version='library-v2'))
    assert fake.call_count == 4


def test_recent_verification_is_source_bound_and_drafts_invalidate_it(tmp_path):
    project = HSFProject.create_new('Shelf', str(tmp_path));project.save_to_disk()
    snapshot = capture_snapshot(project, 1)
    old = {'source_fingerprint': 'stale', 'findings': [{'id': 'old'}]}
    report = inspect_snapshot(snapshot, requested=False, recent_verification=old)
    assert next(c for c in report.checks if c['kind']=='recent_verification')['status'] == 'not_requested'
    current = {'source_fingerprint': snapshot.source_fingerprint, 'findings': []}
    report = inspect_snapshot(snapshot, requested=False, recent_verification=current)
    assert next(c for c in report.checks if c['kind']=='recent_verification')['status'] == 'completed'
    report = inspect_snapshot(capture_snapshot(project, 1, {'3d.gdl': 'BLOCK 2,2,2'}), requested=False, recent_verification=current)
    assert next(c for c in report.checks if c['kind']=='recent_verification')['status'] == 'not_requested'
