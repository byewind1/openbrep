import json
from pathlib import Path
from scripts.assistant_route_eval import evaluate_routes


def test_frozen_route_set_has_required_category_counts():
    rows=json.loads(Path('tests/fixtures/assistant_route_eval.json').read_text())
    assert len(rows)>=120
    assert len({r['id'] for r in rows})==len(rows)
    assert sum(r['forbid_write'] for r in rows)>=40
    assert sum(r['expected_mode']=='execute' for r in rows)>=30
    assert sum(r.get('depends_on_history',False) for r in rows)>=20


def test_evaluation_failure_is_in_denominator_and_counts_real_attempts():
    rows=[{'id':'explicit','input':'把A改成2','context':{'has_project':True},'expected_mode':'execute'},
          {'id':'unclear','input':'还是很笨重','context':{'has_project':True},'expected_mode':'execute'}]
    def broken(payload): raise RuntimeError('offline failure')
    result=evaluate_routes(rows,broken,mode='real')
    assert result['total']==2 and result['accuracy']==.5 and result['execute_recall']==.5
    assert result['real_llm_calls']==1
    assert result['confusion_matrix']['execute->failed']==1
    assert result['status']=='未验收'


def test_mock_success_is_not_real_acceptance():
    row={'id':'read','input':'只解释参数','context':{'has_project':True},'expected_mode':'consult','forbid_write':True}
    result=evaluate_routes([row],lambda p:{'mode':'execute'},mode='mock')
    assert result['forbidden_execute']==0
    assert result['status']=='未验收' and result['real_llm_calls']==0
