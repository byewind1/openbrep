"""Cancellable deterministic checks on immutable HSF snapshots, never compilation."""
from __future__ import annotations

import copy
import json
import multiprocessing
import re
import time
import uuid
from dataclasses import dataclass

from openbrep.hsf_project import ScriptType
from openbrep.source_snapshot import SourceSnapshot
from openbrep.static_checker import StaticChecker
from openbrep.values_declarations import parse_values_declarations

LOCAL_BUDGET_SECONDS = 2.0
INSPECTION_CONTEXT_TOKENS = 2000
CHECKER_VERSION = 'assistant-inspection-v2'


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


def _worker(project, sender, previews=()):
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
        for kind in previews:
            from openbrep.gdl_previewer import preview_2d_script, preview_3d_script
            params = {p.name: p.value for p in project.parameters}
            script = project.get_script(ScriptType.SCRIPT_2D if kind == 'preview_2d' else ScriptType.SCRIPT_3D) or ''
            setup = project.get_script(ScriptType.MASTER) or ''
            kwargs = {'parameters': params, 'setup_script': setup, 'wall_clock_limit': 0.5}
            if kind == 'preview_2d':
                result = preview_2d_script(script, script_3d=project.get_script(ScriptType.SCRIPT_3D), **kwargs)
                count = len(result.lines) + len(result.polygons) + len(result.circles) + len(result.arcs) + len(result.texts)
            else:
                result = preview_3d_script(script, **kwargs)
                count = len(result.meshes) + len(result.wires)
            warnings = list(result.warnings)
            string_branch = bool(re.search(r'\bIF\b[^\n]*"', setup + '\n' + script, re.I))
            incomplete = bool(warnings or string_branch)
            coverage = {'local_approximation': True, 'current_parameter_values_only': True,
                        'all_string_branches_evaluated': not string_branch, 'dependencies_resolved': not bool(re.search(r'\bCALL\b', setup + '\n' + script, re.I)),
                        'primitive_count': count}
            findings = [{'id': f'{kind}:warning:{i}', 'severity': 'warning', 'message': text,
                         'evidence_location': {'file': 'scripts/' + ('2d.gdl' if kind == 'preview_2d' else '3d.gdl')}, 'source': 'local_previewer'} for i, text in enumerate(warnings)]
            if count == 0 and script.strip() and not incomplete:
                findings.append({'id': f'{kind}:empty', 'severity': 'warning', 'message': '当前参数值下的受支持脚本未产生本地预览几何。',
                                 'evidence_location': {'file': 'scripts/' + ('2d.gdl' if kind == 'preview_2d' else '3d.gdl')}, 'source': 'local_previewer'})
            sender.send(_check(kind, 'partial' if incomplete else 'completed', findings, coverage))
        sender.send(None)
    except Exception as exc:
        sender.send(_check('inspection', 'unavailable', reason=type(exc).__name__))
        sender.send(None)
    finally:
        sender.close()


def inspect_snapshot(snapshot: SourceSnapshot, *, requested=True, budget_seconds=LOCAL_BUDGET_SECONDS, should_cancel=None, previews=(), recent_verification=None) -> InspectionReport:
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
        process = ctx.Process(target=_worker, args=(project, sender, tuple(previews)), daemon=True)
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
    for kind in ('static', 'parameters', *previews):
        if kind not in present:
            checks.append(_check(kind, 'unavailable', reason=reason or 'not_completed'))
    checks.extend(_check(kind, 'not_requested') for kind in ('preview_2d', 'preview_3d') if kind not in previews)
    verified = recent_verification and recent_verification.get('source_fingerprint') == snapshot.source_fingerprint and snapshot.source_fingerprint is not None and not snapshot.drafts
    checks.append(_check('recent_verification', 'completed' if verified else 'not_requested', coverage={'source_matched': bool(verified)},
                         findings=recent_verification.get('findings', []) if verified else []))
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


class InspectionCache:
    """Only reusable with an explicit dependency version; failures never persist."""
    def __init__(self, limit=32):
        from collections import OrderedDict
        self.entries = OrderedDict()
        self.limit = limit

    def inspect(self, snapshot, *, requested=True, previews=(), should_cancel=None, **kwargs):
        key = (snapshot.context_fingerprint, CHECKER_VERSION, snapshot.dependency_context_version, tuple(previews), requested,
               json.dumps(kwargs.get('recent_verification'), sort_keys=True, default=str), snapshot.source_fingerprint if kwargs.get('recent_verification') else None)
        if should_cancel and should_cancel():
            return inspect_snapshot(snapshot, requested=requested, previews=previews, should_cancel=should_cancel, **kwargs)
        if snapshot.dependency_context_version is not None and key in self.entries:
            cached = self.entries[key]
            self.entries.move_to_end(key)
            return InspectionReport(uuid.uuid4().hex, snapshot.source_version, 0, copy.deepcopy(cached.checks))
        report = inspect_snapshot(snapshot, requested=requested, previews=previews, should_cancel=should_cancel, **kwargs)
        if snapshot.dependency_context_version is not None and all(c['status'] != 'unavailable' for c in report.checks):
            self.entries[key] = report
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
        return report
