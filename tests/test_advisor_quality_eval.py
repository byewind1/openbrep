import json
from pathlib import Path
from scripts.advisor_quality_eval import fixed_source, assess_scores, DIMENSIONS
from openbrep.source_snapshot import capture_snapshot


def test_frozen_quality_cases_are_public_and_sources_are_deterministic(tmp_path):
    data=json.loads(Path('tests/fixtures/advisor_quality_cases.json').read_text())
    assert len(data['questions'])>=30 and len(data['multi_rounds'])>=12
    assert len({r['id'] for r in data['questions']+data['multi_rounds']})>=42
    for spec in data['sources'].values():
        first=fixed_source(spec,tmp_path/'one');second=fixed_source(spec,tmp_path/'two')
        assert capture_snapshot(first,1).context_fingerprint==capture_snapshot(second,1).context_fingerprint
        if first:assert not first.root.exists()
    acceptance=json.loads(Path('tests/fixtures/advisor_quality_acceptance.json').read_text())
    assert acceptance['ordinary_model']['status']==acceptance['codex_model']['status']=='未验收'


def test_missing_scores_are_in_denominator_and_critical_errors_block_acceptance():
    scored={'scores':{'legacy':{k:0 for k in DIMENSIONS},'advisor':{k:2 for k in DIMENSIONS}},'critical_errors':[]}
    result=assess_scores([scored,{'scores':{},'critical_errors':[]}])
    assert result['total']==2 and result['scored']==1
    assert result['mean_score']==5 and result['improvement_ratio']==.5 and result['status']=='未验收'
    result=assess_scores([{**scored,'critical_errors':['编造检查事实']}])
    assert result['status']=='未验收'
