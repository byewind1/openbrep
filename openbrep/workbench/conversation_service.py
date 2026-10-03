"""Session-local conversation orchestration; source mutation stays in existing services."""
from __future__ import annotations

import copy
import queue
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from openbrep.hsf_project import HSFProject
from openbrep.runtime.turn_policy import TurnPolicy, decide_turn
from openbrep.source_snapshot import SourceSnapshot, capture_snapshot

TURN_TTL_SECONDS = 1800
MAX_RECENT_TURNS = 128


@dataclass
class PreparedTurn:
    turn_id: str
    client_turn_id: str
    body: dict
    policy: TurnPolicy
    snapshot: SourceSnapshot
    created_at: float
    state: str = 'prepared'
    result: dict | None = None
    plan: dict | None = None
    working_intent_version: int = 0


class WorkbenchConversationService:
    def __init__(self, session: Any, *, clock=time.monotonic):
        self.session = session
        self.clock = clock
        self.epoch = session.project_epoch
        self.turns: OrderedDict[str, PreparedTurn] = OrderedDict()
        self.client_ids: dict[str, str] = {}
        self.active_turn_id: str | None = None
        self.pending_turn_id: str | None = None
        self.selected_proposal: dict | None = None
        self.advisor = None
        self.semantic_decision = None

    def clear(self):
        for turn in self.turns.values():
            if turn.state in {'prepared', 'pending'}:
                turn.state = 'cancelled'
                turn.result = self._response(turn, 'cancelled', cancelled=True)
        self.pending_turn_id = None
        self.selected_proposal = None
        self.epoch = self.session.project_epoch

    def _sync_epoch(self):
        if self.epoch != self.session.project_epoch:
            self.clear()

    def _dependency_version(self):
        return getattr(self.session, 'dependency_context_version', None)

    def _response(self, turn: PreparedTurn | None, kind: str, **payload):
        return {'ok': kind not in {'failed'}, 'result_kind': kind,
                'turn_id': turn.turn_id if turn else None,
                'project_epoch': turn.snapshot.project_epoch if turn else self.session.project_epoch,
                **payload}

    def _failure(self, turn, code, error=None):
        return self._response(turn, 'failed', code=code, error=error or code)

    def route(self, body: dict):
        # The worker owns the same session lock during the real work, not merely
        # while constructing a generator. Client disconnect cancels future work.
        if body.get('stream'):
            events = queue.Queue()
            cancel = threading.Event()
            request = {**body, 'stream': False}
            def emit(kind, data):
                events.put({'type': kind, 'data': data})
            def worker():
                try:
                    with self.session._op_lock:
                        result = self.handle(request, should_cancel=cancel.is_set, on_event=emit)
                    emit('done', result)
                except Exception:
                    emit('done', self._failure(None, 'TURN_FAILED', '本轮处理失败。'))
                finally:
                    events.put(None)
            def stream():
                threading.Thread(target=worker, daemon=True).start()
                try:
                    while True:
                        event = events.get()
                        if event is None:
                            break
                        yield event
                finally:
                    cancel.set()
            return stream()
        return self.handle(body)

    def handle(self, body: dict, *, should_cancel=None, on_event=None):
        self._sync_epoch()
        if self.session.config.llm.effective_conversation_entry() == 'legacy':
            return {**self._failure(None, 'UNIFIED_ENTRY_DISABLED'), 'http_status': 501}
        phase = body.get('phase', 'prepare')
        if phase == 'prepare':
            return self.prepare(body, should_cancel=should_cancel, on_event=on_event)
        if phase == 'execute':
            return self.execute(body, should_cancel=should_cancel, on_event=on_event)
        return self._failure(None, 'INVALID_PHASE')

    def prepare(self, body: dict, *, should_cancel=None, on_event=None):
        client_id = body.get('client_turn_id')
        if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 128:
            return self._failure(None, 'INVALID_CLIENT_TURN_ID')
        previous = self.turns.get(self.client_ids.get(client_id, ''))
        if previous:
            return copy.deepcopy(previous.result or self._response(previous, 'ready_to_execute', state=previous.state))
        if body.get('project_epoch') != self.session.project_epoch:
            return self._failure(None, 'PROJECT_CHANGED')
        message = str(body.get('message') or '').strip()
        if not message:
            return self._failure(None, 'EMPTY_MESSAGE')
        if self.active_turn_id:
            return self._failure(None, 'TURN_BUSY')
        if should_cancel and should_cancel():
            return self._response(None, 'cancelled', cancelled=True)
        try:
            snapshot = capture_snapshot(self.session.project, self.session.project_epoch, body.get('draft_scripts'), dependency_context_version=self._dependency_version())
            policy = decide_turn(message, requested_mode=body.get('requested_mode', 'auto'), history=body.get('history'),
                                 working_intent=self.intent_summary(), project_state={'has_project': self.session.project is not None},
                                 semantic_decision=self.semantic_decision)
        except (ValueError, TypeError) as exc:
            return self._failure(None, 'INVALID_TURN', str(exc))
        turn = PreparedTurn(uuid.uuid4().hex, client_id, copy.deepcopy({k: v for k, v in body.items() if k not in {'stream', 'phase'}}), policy, snapshot, self.clock())
        self.turns[turn.turn_id] = turn
        self.client_ids[client_id] = turn.turn_id
        while len(self.turns) > MAX_RECENT_TURNS:
            old_id, old = self.turns.popitem(last=False)
            self.client_ids.pop(old.client_turn_id, None)
        if policy.error:
            turn.state = 'failed'
            turn.result = self._failure(turn, policy.error)
        elif policy.mode == 'execute':
            turn.result = self._response(turn, 'ready_to_execute', mode='execute', task_intent=policy.task_intent, source_version=snapshot.source_version)
        else:
            turn.result = self._prepare_advice(turn, should_cancel=should_cancel)
        if should_cancel and should_cancel():
            turn.state = 'cancelled'
            turn.result = self._response(turn, 'cancelled', cancelled=True)
        return copy.deepcopy(turn.result)

    def intent_summary(self) -> dict:
        pending = self.turns.get(self.pending_turn_id or '')
        return {'pending_plan': pending.plan if pending and pending.state == 'pending' else None,
                'proposals': [self.selected_proposal] if self.selected_proposal else []}

    def _prepare_advice(self, turn: PreparedTurn, *, should_cancel=None):
        if self.advisor is None:
            turn.state = 'failed'
            return self._failure(turn, 'ADVISOR_UNAVAILABLE')
        return self.advisor(turn, should_cancel=should_cancel)

    def execute(self, body: dict, *, should_cancel=None, on_event=None):
        turn = self.turns.get(str(body.get('turn_id') or ''))
        if turn is None:
            return self._failure(None, 'TURN_NOT_FOUND')
        if turn.state in {'completed', 'failed', 'cancelled', 'stale'}:
            return copy.deepcopy(turn.result)
        if turn.state == 'executing':
            return self._response(turn, 'executing', state='executing')
        if body.get('approve') is False:
            turn.state = 'cancelled'
            turn.result = self._response(turn, 'cancelled', cancelled=True)
            if self.pending_turn_id == turn.turn_id:
                self.pending_turn_id = None
            return copy.deepcopy(turn.result)
        if self.clock() - turn.created_at > TURN_TTL_SECONDS:
            turn.state = 'cancelled'
            turn.result = self._failure(turn, 'TURN_EXPIRED')
            return copy.deepcopy(turn.result)
        if turn.policy.mode == 'consult':
            return self._failure(turn, 'READ_ONLY_TURN')
        if turn.policy.mode == 'plan':
            if body.get('approve') is not True:
                return self._failure(turn, 'APPROVAL_REQUIRED')
            if not turn.plan or body.get('plan_id') != turn.plan['plan_id'] or body.get('plan_version') != turn.plan['plan_version']:
                return self._failure(turn, 'PLAN_VERSION_MISMATCH')
        if not turn.snapshot.matches(self.session.project, self.session.project_epoch, self._dependency_version()):
            turn.state = 'stale'
            turn.result = self._failure(turn, 'PLAN_STALE' if turn.policy.mode == 'plan' else 'SOURCE_CHANGED')
            if turn.plan:
                turn.result['pending_plan'] = copy.deepcopy(turn.plan)
            return copy.deepcopy(turn.result)
        if self.active_turn_id:
            return self._failure(turn, 'TURN_BUSY')
        if should_cancel and should_cancel():
            turn.state = 'cancelled'
            turn.result = self._response(turn, 'cancelled', cancelled=True)
            return copy.deepcopy(turn.result)
        self.active_turn_id = turn.turn_id
        turn.state = 'executing'
        def emit(kind, data):
            if on_event:
                on_event(kind, {**data, 'turn_id': turn.turn_id, 'project_epoch': turn.snapshot.project_epoch})
        request = {**turn.body, 'intent': turn.policy.task_intent, 'stream': False, 'confirm_plan': False,
                   'execution_policy': {**turn.policy.to_dict(), 'mode': 'execute'},
                   '_turn_should_cancel': should_cancel, '_turn_on_event': emit}
        if turn.plan:
            request['confirmed_plan'] = copy.deepcopy(turn.plan)
        try:
            if turn.policy.task_intent == 'CREATE':
                response = self.session.create_project_from_prompt(request)
            else:
                response = self.session.assistant_service.generate_with_assistant(request)
            kind = 'execution' if response.get('ok') else 'failed'
            turn.state = 'completed' if response.get('ok') else 'failed'
            turn.result = self._response(turn, kind, **{k: v for k, v in response.items() if k not in {'turn_id', 'project_epoch', 'result_kind'}})
            if should_cancel and should_cancel():
                # Keep real delivery evidence if changes already happened.
                turn.state = 'cancelled'
                turn.result['cancelled'] = True
                turn.result['result_kind'] = 'cancelled'
        except Exception:
            turn.state = 'failed'
            turn.result = self._failure(turn, 'EXECUTION_FAILED')
        finally:
            self.active_turn_id = None
            if self.pending_turn_id == turn.turn_id:
                self.pending_turn_id = None
        return copy.deepcopy(turn.result)
