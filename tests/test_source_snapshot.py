import copy
from pathlib import Path

import pytest

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.source_snapshot import capture_snapshot
from openbrep.source_fingerprint import compute_source_fingerprint


def test_drafts_are_independent_and_save_aware(tmp_path):
    project = HSFProject.create_new('Shelf', str(tmp_path))
    project.save_to_disk()
    before = compute_source_fingerprint(project.root)
    snapshot = capture_snapshot(project, 1, {'3d.gdl': 'BLOCK 1, 2, 3\n'})
    assert compute_source_fingerprint(project.root) == before
    assert snapshot.project_copy().get_script(ScriptType.SCRIPT_3D) == 'BLOCK 1, 2, 3\n'
    assert project.get_script(ScriptType.SCRIPT_3D) != 'BLOCK 1, 2, 3\n'
    project.set_script(ScriptType.SCRIPT_3D, 'BLOCK 1, 2, 3\n')
    project.save_to_disk()
    assert snapshot.matches(project, 1)
    assert capture_snapshot(project, 1).context_fingerprint == snapshot.context_fingerprint
    assert compute_source_fingerprint(project.root) != before


@pytest.mark.parametrize('draft', [{'../x': 'x'}, {'3d.gdl': 42}, {'unknown.gdl': 'x'}, ['x']])
def test_invalid_drafts_rejected(tmp_path, draft):
    with pytest.raises(ValueError):
        capture_snapshot(HSFProject.create_new('Shelf', str(tmp_path)), 1, draft)


def test_xml_parameter_epoch_and_dependency_changes_invalidate(tmp_path):
    project = HSFProject.create_new('Shelf', str(tmp_path))
    project.save_to_disk()
    snapshot = capture_snapshot(project, 1)
    assert not snapshot.matches(project, 2)
    assert not snapshot.matches(project, 1, 'new-library')
    project.parameters[0].value = '2'
    assert not snapshot.matches(project, 1)
    project.parameters[0].value = '1.00'
    path = project.root / 'libpartdata.xml'
    path.write_text(path.read_text(encoding='utf-8-sig').replace('</LibPart>', '<UnknownThing value="x"/></LibPart>'), encoding='utf-8-sig')
    # Inject a standalone unknown XML file independent of serializer tag spelling.
    (project.root / 'unknown.xml').write_text('<unknown value="x"/>')
    assert not snapshot.matches(project, 1)


def test_bom_change_is_byte_sensitive_but_context_stable(tmp_path):
    project = HSFProject.create_new('Shelf', str(tmp_path))
    project.save_to_disk()
    before = capture_snapshot(project, 1)
    path = project.root / 'scripts/3d.gdl'
    path.write_text(path.read_text(encoding='utf-8-sig'), encoding='utf-8')
    after = capture_snapshot(project, 1)
    assert before.source_fingerprint != after.source_fingerprint
    assert before.context_fingerprint == after.context_fingerprint
