"""Cancellable deterministic checks on immutable HSF snapshots, never compilation."""
from __future__ import annotations

import copy
import json
import multiprocessing
import time
import uuid
from dataclasses import dataclass

from openbrep.hsf_project import ScriptType
from openbrep.source_snapshot import SourceSnapshot
from openbrep.static_checker import StaticChecker
from openbrep.values_declarations import parse_values_declarations

LOCAL_BUDGET_SECONDS = 2.0
INSPECTION_CONTEXT_TOKENS = 2000
CHECKER_VERSION = 'assistant-inspection-v1'


@dataclass(frozen=True)
class InspectionReport:
    inspection_id: str
    source_version: dict
    duration_ms: float
    checks: tuple[dict, ...]

    def to_dict(self):
        return {'inspection_id': self.inspection_id, 'source_version': copy.deepcopy(self.source_version),
                'duration_ms': self.duration_ms, 'checks': copy.deepcopy(list(self.checks))}


def _check(kind, status, findings=(), coverage=None, reason=None):
    return {'id': kind, 'kind': kind, 'status': status, 'findings': list(findings),
            'coverage': coverage or {}, 'unavailable_reason': reason, 'truncated_count': 0}


def _worker(project, sender):
    try:
        result = StaticChecker().check(project)
        findings = []
        for severity, issues in [('error', result.errors), ('warning', result.warnings)]:
            for issue in issues:
                findings.append({'id': f'static:{len(findings)}', 'severity': severity, 'message': issue.detail,
                                 'evidence_location': {'file': issue.file}, 'source': 'static_checker', 'check_type': issue.check_type})
        sender.send(_check('static', 'completed', findings, {'scripts': [st.value for st in project.scripts]}))
        findings = [{'id': f'parameter:{p.name}', 'severity': 'info', 'message': f'{p.name} ({p.type_tag}) = {p.value}',
                     'evidence_location': {'file': 'paramlist.xml', 'parameter': p.name}, 'source': 'parameter_declaration'} for p in project.parameters]
        for name, values in parse_values_declarations(project.get_script(ScriptType.PARAM) or '').items():
            findings.append({'id': f'values:{name}', 'severity': 'info', 'message': f'VALUES {name}: {json.dumps(values, ensure_ascii=False)}',
                             'evidence_location': {'file': 'scripts/vl.gdl', 'parameter': name}, 'source': 'values_static_declaration'})
        sender.send(_check('parameters', 'completed', findings, {'static_declarations_only': True, 'dynamic_expressions_not_evaluated': True}))
        sender.send(None)
    except Exception as exc:
        sender.send(_check('inspection', 'unavailable', reason=type(exc).__name__))
        sender.send(None)
    finally:
        sender.close()


def inspect_snapshot(snapshot: SourceSnapshot, *, requested=True, budget_seconds=LOCAL_BUDGET_SECONDS, should_cancel=None) -> InspectionReport:
    start = time.monotonic()
    checks = []
    project = snapshot.project_copy()
    reason = None
    if not requested or project is None:
        checks = [_check(kind, 'not_requested', reason='No project' if project is None else None) for kind in ('static', 'parameters')]
    elif budget_seconds <= 0 or (should_cancel and should_cancel()):
        reason = 'cancelled' if should_cancel and should_cancel() else 'budget_exceeded'
    else:
        ctx = multiprocessing.get_context('spawn')
        receiver, sender = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_worker, args=(project, sender), daemon=True)
        try:
            process.start()
            sender.close()
            deadline = start + budget_seconds
            while True:
                if should_cancel and should_cancel():
                    reason = 'cancelled'
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = 'budget_exceeded'
                    break
                if receiver.poll(min(remaining, 0.02)):
                    try:
                        item = receiver.recv()
                    except EOFError:
                        reason = 'worker_terminated'
                        break
                    if item is None:
                        break
                    checks.append(item)
                elif not process.is_alive():
                    reason = 'worker_terminated'
                    break
        except Exception as exc:
            reason = type(exc).__name__
        finally:
            if process.is_alive():
                process.terminate()
            if process.pid is not None:
                process.join(timeout=0.2)
                if process.is_alive():
                    process.kill()
                    process.join()
            receiver.close()
    present = {c['kind'] for c in checks}
    for kind in ('static', 'parameters'):
        if kind not in present:
            checks.append(_check(kind, 'unavailable', reason=reason or 'not_completed'))
    checks.extend(_check(kind, 'not_requested') for kind in ('preview_2d', 'preview_3d', 'recent_verification'))
    return InspectionReport(uuid.uuid4().hex, snapshot.source_version, round((time.monotonic() - start) * 1000, 2), tuple(checks))


def render_inspection(report: InspectionReport, *, token_budget=INSPECTION_CONTEXT_TOKENS) -> dict:
    """Whole findings in severity order; UTF-8 bytes are a conservative token bound."""
    entries = []
    for check in report.checks:
        entries.append({'kind': check['kind'], 'status': check['status'], 'coverage': check['coverage'], 'unavailable_reason': check['unavailable_reason']})
    included = []
    omitted = 0
    findings = sorted((f for c in report.checks for f in c['findings']), key=lambda f: {'error': 0, 'warning': 1, 'info': 2}.get(f['severity'], 3))
    for finding in findings:
        candidate = {'checks': entries, 'findings': included + [finding], 'truncated_count': omitted}
        if len(json.dumps(candidate, ensure_ascii=False).encode()) <= token_budget:
            included.append(finding)
        else:
            omitted += 1
    return {'checks': entries, 'findings': included, 'truncated_count': omitted}
