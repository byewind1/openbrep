import json
from unittest.mock import Mock

import pytest

from openbrep.hsf_project import HSFProject
from openbrep.llm import MockLLM
from openbrep.runtime.advisor import advise, parse_advisor_output
from openbrep.runtime.inspection import InspectionReport, inspect_snapshot
from openbrep.source_snapshot import capture_snapshot


def answer(**extra):
    return json.dumps({'conclusion': '推荐背板', 'basis': [], 'suggestions': ['用PRISM_'], 'tradeoffs': ['重量增加'], **extra}, ensure_ascii=False)


def test_advisor_single_call_readonly_and_fake_evidence_is_assumption(tmp_path):
    project = HSFProject.create_new('Shelf', str(tmp_path))
    snapshot = capture_snapshot(project, 1)
    report = inspect_snapshot(snapshot, requested=False)
    llm = MockLLM(responses=[answer(basis=[{'text': '厚度不足', 'finding_refs': ['invented']}])])
    result = advise(snapshot, '有什么思路', llm=llm, report=report)
    assert result.facts == ()
    assert result.assumptions == ('厚度不足',)
    assert llm.call_count == 1
    assert not project.root.exists()
    assert 'tools' not in str(llm.call_history)


def test_model_cannot_forge_inspection_report():
    report = InspectionReport('i1', {}, 0, ())
    with pytest.raises(ValueError):
        parse_advisor_output(answer(findings=[{'message': '已检查'}]), report)


def test_valid_finding_reference_retains_location():
    finding = {'id': 'f1', 'severity': 'error', 'message': 'IF mismatch', 'evidence_location': {'file': 'scripts/3d.gdl'}, 'source': 'static_checker'}
    report = InspectionReport('i1', {}, 0, ({'status': 'completed', 'findings': [finding]},))
    result = parse_advisor_output(answer(basis=[{'text': 'IF不配对', 'finding_refs': ['f1']}]), report)
    assert result.facts[0]['evidence'][0]['file'] == 'scripts/3d.gdl'


def test_no_project_advisor_does_not_create_output(tmp_path):
    snapshot = capture_snapshot(None, 0)
    report = inspect_snapshot(snapshot)
    result = advise(snapshot, 'PRISM_怎么选', llm=MockLLM(responses=[answer()]), report=report)
    assert result.reply
    assert list(tmp_path.iterdir()) == []


def test_stale_report_never_enters_model(tmp_path):
    snapshot = capture_snapshot(None, 0)
    report = InspectionReport('stale', {'context_fingerprint': 'old'}, 0, ())
    llm = Mock()
    with pytest.raises(ValueError, match='Stale'):
        advise(snapshot, '解释', llm=llm, report=report)
    llm.generate.assert_not_called()
