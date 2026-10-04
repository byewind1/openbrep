#!/usr/bin/env python3
"""Evaluate production turn decisions. Offline/mock results are never acceptance."""
from __future__ import annotations
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openbrep.runtime.turn_policy import decide_turn


def evaluate_routes(rows, semantic_decision=None, *, mode='offline'):
    calls = 0
    def counted(payload):
        nonlocal calls
        calls += 1
        return semantic_decision(payload)
    confusion = Counter()
    failures = []
    decisions = []
    correct = execute_correct = forbidden_execute = 0
    started = time.monotonic()
    for row in rows:
        context = row['context']
        working = {k:v for k,v in context.items() if k != 'has_project'}
        if context.get('interrupted_task') and not working.get('active_task'):
            working['active_task'] = {**context['interrupted_task'], 'task_intent':'MODIFY'}
        try:
            policy = decide_turn(row['input'], requested_mode=context.get('requested_mode','auto'),
                history=context.get('history',[]), working_intent=working,
                project_state={'has_project':context['has_project']},
                semantic_decision=counted if semantic_decision else None)
            predicted = 'failed' if policy.error else policy.mode
            error = policy.error
        except Exception as exc:
            predicted, error = 'failed', type(exc).__name__
        expected = row['expected_mode']
        confusion[(expected,predicted)] += 1
        matched = predicted == expected
        correct += matched
        execute_correct += matched and expected == 'execute'
        forbidden_execute += bool(row.get('forbid_write') and predicted == 'execute')
        entry = {'id':row['id'],'expected':expected,'predicted':predicted,'error':error}
        decisions.append(entry)
        if not matched: failures.append(entry)
    total = len(rows)
    execute_total = sum(r['expected_mode']=='execute' for r in rows)
    accuracy = correct / total if total else 0
    recall = execute_correct / execute_total if execute_total else 0
    meets = accuracy >= .95 and recall >= .95 and forbidden_execute == 0
    return {'status':'已验收' if mode=='real' and meets else '未验收', 'mode':mode,
        'total':total,'accuracy':accuracy,'execute_recall':recall,'forbidden_execute':forbidden_execute,
        'decision_calls':calls,'real_llm_calls':calls if mode=='real' else 0,
        'confusion_matrix':{f'{a}->{b}':n for (a,b),n in sorted(confusion.items())},
        'failures':failures,'decisions':decisions,'duration_ms':round((time.monotonic()-started)*1000,2),
        'note':'失败和未评分计入分母；固定集合达标不代表线上零误判。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',default='tests/fixtures/assistant_route_eval.json')
    parser.add_argument('--mode',choices=['offline','mock','real'],default='offline')
    parser.add_argument('--config')
    parser.add_argument('--allow-paid',action='store_true',help='Explicit authorization for real calls')
    parser.add_argument('--output',default='/private/tmp/assistant-route-eval.json')
    args = parser.parse_args()
    callback = None
    model = provider = None
    if args.mode == 'mock':
        callback = lambda payload: {'mode':'consult','task_intent':'CHAT','constraints':[]}
    if args.mode == 'real':
        if not args.allow_paid or not args.config:
            parser.error('Real evaluation requires --allow-paid and an explicit --config.')
        from openbrep.workbench_api import WorkbenchSession
        session = WorkbenchSession(config_path=args.config)
        callback = session.conversation_service._semantic_decision
        model = session.llm_model
        from openbrep.config import model_to_provider
        provider = model_to_provider(model)
    rows = json.loads(Path(args.cases).read_text())
    report = evaluate_routes(rows, callback, mode=args.mode)
    report.update(model=model,provider=provider)
    destination = Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in {'decisions','failures'}},ensure_ascii=False))
    return 1 if args.mode=='real' and report['status']!='已验收' else 0

if __name__ == '__main__':
    raise SystemExit(main())
