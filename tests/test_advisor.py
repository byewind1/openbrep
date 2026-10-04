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


def test_advisor_extracts_relevant_subroutine_when_script_exceeds_budget(tmp_path):
    """P1-B（R5）：3d.gdl 超预算时抽取关键词命中的子程序闭包（不整块丢弃），
    omitted 标注 excerpt_only；模型收到的输入含目标子程序完整文本。"""
    project = HSFProject.create_new('Lattice', str(tmp_path))
    from openbrep.hsf_project import ScriptType

    big = "\n".join([f"! filler line {i} with some text to grow the script" for i in range(1200)])
    script = (
        big
        + '\nGOSUB "PatternHuiwen"\n'
        + '"PatternHuiwen":\nBLOCK 0.1, 0.1, 0.1\nGOSUB "HuiCell"\nRETURN\n'
        + '"HuiCell":\nBLOCK 0.05, 0.05, 0.05\nRETURN\n'
        + '"Unrelated":\nBLOCK 9, 9, 9\nRETURN\n'
    )
    project.scripts[ScriptType.SCRIPT_3D] = script
    snapshot = capture_snapshot(project, 1)
    report = inspect_snapshot(snapshot, requested=False)
    llm = MockLLM(responses=[answer()])
    result = advise(snapshot, '把PatternHuiwen改成连续方折', llm=llm, report=report)
    sent = json.loads(llm.call_history[0][1]['content'])
    three_d = sent['sections'].get('scripts/3d.gdl', '')
    assert 'PatternHuiwen' in three_d
    assert 'HuiCell' in three_d          # 调用闭包完整
    assert 'Unrelated' not in three_d    # 无关子程序不塞入
    assert any('excerpt_only' in item for item in sent['omitted_sections'])


def test_advisor_history_keeps_reference_mutation_entries(tmp_path):
    """P1-B（R5）：history 超预算截断时，含图/参考指代的条目优先保留——
    "按第1张" 的指代依据不能丢。"""
    project = HSFProject.create_new('Lattice', str(tmp_path))
    from openbrep.hsf_project import ScriptType

    big = "\n".join([f"! filler {i} some longer text here" for i in range(1500)])
    project.scripts[ScriptType.SCRIPT_3D] = big
    snapshot = capture_snapshot(project, 1)
    report = inspect_snapshot(snapshot, requested=False)
    history = [{'role': 'user', 'content': '旧的无关键条 ' + 'x' * 800} for _ in range(8)]
    history.append({'role': 'assistant', 'content': '按第1张参考图处理，连续方折、等宽。'})
    llm = MockLLM(responses=[answer()])
    advise(snapshot, '继续按参考图修改', llm=llm, report=report, history=history)
    sent = json.loads(llm.call_history[0][1]['content'])
    sent_history = json.dumps(sent['sections'].get('history', []), ensure_ascii=False)
    assert '按第1张参考图处理' in sent_history


def test_advisor_extract_relevant_sections_direct():
    from openbrep.runtime.advisor import _extract_relevant_sections

    script = 'A = 1\nGOSUB "PatternHuiwen"\nEND\n"PatternHuiwen":\nBLOCK 1, 1, 1\nRETURN\n'
    extracted = _extract_relevant_sections(script, ['PatternHuiwen'])
    assert '"PatternHuiwen":' in extracted
    assert 'BLOCK 1, 1, 1' in extracted
    assert _extract_relevant_sections(script, ['Nothing']) == ''
    assert _extract_relevant_sections('', ['x']) == ''
