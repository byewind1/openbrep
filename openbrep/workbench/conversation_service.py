"""Session-local conversation orchestration; source mutation stays in existing services."""
from __future__ import annotations

import copy
import queue
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
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
        self.proposals: OrderedDict[str, dict] = OrderedDict()
        from openbrep.runtime.inspection import InspectionCache
        self.inspection_cache = InspectionCache()
        self.advisor = self._answer
        self.semantic_decision = self._semantic_decision

    def _llm(self):
        from openbrep.config import is_codex_qualified_model
        from openbrep.workbench.settings_service import effective_session_reasoning_effort
        config = copy.copy(self.session.config.llm)
        config.model = self.session.llm_model
        config.reasoning_effort = effective_session_reasoning_effort(self.session)
        config.credential_scope = self.session.session_id
        llm = self.session.settings_service.llm_adapter_factory(config)
        if is_codex_qualified_model(config.model):
            llm.codex_provider = self.session.settings_service._codex_provider()
        return llm

    def _semantic_decision(self, payload):
        import json
        from openbrep.chat_history import trim_history_messages
        from openbrep.llm import codex_chat_generate_kwargs
        data = {**payload, 'history': trim_history_messages(payload.get('history'))}
        llm = self._llm()
        response = llm.generate([
            {'role': 'system', 'content': '判断本轮执行方式。只输出JSON：mode(consult/plan/execute)、task_intent(CREATE/MODIFY/DEBUG/REPAIR/CHAT)、constraints(字符串数组)。明确禁止优先；普通需求执行，寻求思路咨询，先别改给方案仅计划；当前修改任务的纠偏可执行；只有唯一有效pending计划或中断任务时“好的/继续”才授权；引用、代码、假设示例不授权。无法确认执行含义时consult，不能因为已打开项目就执行。'},
            {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}
        ], max_tokens=800, stream=False, **codex_chat_generate_kwargs(llm))
        return response.content

    def _answer(self, turn, *, should_cancel=None):
        from pathlib import Path
        from openbrep.knowledge_selector import select_gdl_knowledge
        from openbrep.project_context import resolve_project_context, load_project_knowledge, load_project_skills
        from openbrep.runtime.advisor import advise
        from openbrep.runtime.inspection import inspect_snapshot
        from openbrep.workbench.project_session_service import validate_image_payload
        message = turn.body['message']
        project = turn.snapshot.project_copy()
        needs_inspection = turn.policy.mode == 'plan' or bool(project and any(word in message.lower() for word in ('这个', '脚本', '参数', '报错', '比例', '检查', '优化', '柜子', 'project', 'script', 'error', 'parameter', 'check')))
        geometry = turn.policy.mode == 'plan' or any(word in message.lower() for word in ('几何', '比例', '结构', '预览', '平面', 'geometry', 'preview', 'structure'))
        previews = ('preview_2d', 'preview_3d') if geometry and project else ()
        report = self.inspection_cache.inspect(turn.snapshot, requested=needs_inspection, previews=previews, should_cancel=should_cancel)
        payload = validate_image_payload(turn.body)
        if not payload['ok']:
            turn.state = 'failed'
            return self._failure(turn, 'INVALID_IMAGE', payload['error'])
        images = list(payload.get('images') or [])
        if payload.get('image_b64') and not images:
            images = [{'b64': payload['image_b64'], 'mime': payload['image_mime']}]
        context = resolve_project_context(project)
        knowledge = select_gdl_knowledge(instruction=message, intent='all', knowledge_dir=Path(__file__).resolve().parents[2] / 'knowledge',
                                         project_knowledge=load_project_knowledge(context))
        try:
            answer = advise(turn.snapshot, message, llm=self._llm(), report=report, mode=turn.policy.mode,
                            history=turn.body.get('history'), working_intent=self.intent_summary(),
                            knowledge=knowledge.generation_context + load_project_skills(context, message), images=images, should_cancel=should_cancel)
        except Exception:
            turn.state = 'failed'
            return self._failure(turn, 'PLAN_GENERATION_FAILED' if turn.policy.mode == 'plan' else 'ADVICE_FAILED', '计划生成失败，项目未修改。' if turn.policy.mode == 'plan' else '无法完成本轮顾问回答；项目未修改。')
        proposals = []
        for proposal in answer.proposals:
            bound = {**proposal, 'proposal_id': uuid.uuid4().hex, 'source_version': turn.snapshot.source_version, 'state': 'proposed'}
            proposals.append(bound)
            self.proposals[bound['proposal_id']] = copy.deepcopy(bound)
            while len(self.proposals) > 20:
                self.proposals.popitem(last=False)
        details = {**answer.to_dict(), 'proposals': proposals, 'inspection': report.to_dict(), 'knowledge_sources': knowledge.source_ids}
        if turn.policy.mode == 'plan':
            old = self.turns.get(self.pending_turn_id or '')
            if old and old.state == 'pending':
                old.state = 'stale'
                old.result = self._failure(old, 'PLAN_SUPERSEDED')
                old.result['pending_plan'] = old.plan
            intent = turn.policy.task_intent
            if intent == 'CHAT' or (project is None and intent == 'MODIFY'):
                intent = 'MODIFY' if project else 'CREATE'
            turn.policy = replace(turn.policy, task_intent=intent)
            turn.plan = {**answer.plan, 'plan_id': uuid.uuid4().hex, 'plan_version': 1, 'task_intent': intent,
                         'constraints': list(dict.fromkeys([*turn.policy.constraints, *answer.plan['constraints']])),
                         'project_epoch': turn.snapshot.project_epoch, 'source_version': turn.snapshot.source_version,
                         'working_intent_version': turn.working_intent_version}
            turn.state = 'pending'
            self.pending_turn_id = turn.turn_id
            return self._response(turn, 'awaiting_confirmation', awaiting_confirmation=True, pending_plan=turn.plan,
                                  advisor=details, assistant={'kind': 'advisor', 'reply': answer.reply})
        turn.state = 'completed'
        return self._response(turn, 'advice', advisor=details, assistant={'kind': 'advisor', 'reply': answer.reply})

    def clear(self):
        for turn in self.turns.values():
            if turn.state in {'prepared', 'pending'}:
                turn.state = 'cancelled'
                turn.result = self._response(turn, 'cancelled', cancelled=True)
        self.pending_turn_id = None
        self.selected_proposal = None
        self.proposals.clear()
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
        allowed = {'client_turn_id', 'message', 'history', 'images', 'image_b64', 'image_mime', 'requested_mode', 'project_epoch', 'draft_scripts', 'proposal_id', 'continue_from', 'proposal_action', 'assistant_settings', 'output_dir', 'project_name'}
        turn = PreparedTurn(uuid.uuid4().hex, client_id, copy.deepcopy({k: v for k, v in body.items() if k in allowed}), policy, snapshot, self.clock())
        self.turns[turn.turn_id] = turn
        self.client_ids[client_id] = turn.turn_id
        while len(self.turns) > MAX_RECENT_TURNS:
            old_id, old = self.turns.popitem(last=False)
            self.client_ids.pop(old.client_turn_id, None)
        import re
        pending = self.turns.get(self.pending_turn_id or '')
        reference = re.search(r'按.*(?:方案|计划)|执行.*(?:方案|计划)|^(?:好的|继续|好|ok|continue)$', message, re.I)
        if re.search(r'取消.*计划|cancel.*plan', message, re.I) and pending and pending.state == 'pending':
            pending.state = 'cancelled'
            pending.result = self._response(pending, 'cancelled', cancelled=True)
            self.pending_turn_id = None
            turn.state = 'cancelled'
            turn.result = self._response(turn, 'cancelled', cancelled=True)
            return copy.deepcopy(turn.result)
        proposal_id = body.get('proposal_id')
        if not proposal_id and reference and self.proposals:
            number = re.search(r'方案([一二三四五1-5])', message)
            available = [p for p in self.proposals.values() if p['state'] not in {'discarded', 'superseded'}]
            if number:
                index = '一二三四五'.find(number.group(1)) if not number.group(1).isdigit() else int(number.group(1)) - 1
                if 0 <= index < len(available):
                    proposal_id = available[index]['proposal_id']
            elif self.selected_proposal:
                proposal_id = self.selected_proposal['proposal_id']
            elif len(available) == 1:
                proposal_id = available[0]['proposal_id']
        if proposal_id:
            proposal = self.proposals.get(proposal_id)
            if not proposal:
                turn.state = 'failed'
                turn.result = self._failure(turn, 'PROPOSAL_NOT_FOUND')
                return copy.deepcopy(turn.result)
            if body.get('proposal_action') == 'select':
                proposal['state'] = 'selected'
                self.selected_proposal = copy.deepcopy(proposal)
                turn.state = 'completed'
                turn.result = self._response(turn, 'advice', assistant={'kind': 'advisor', 'reply': '已选择该方案；尚未执行。'})
                return copy.deepcopy(turn.result)
            if policy.mode == 'execute' and (body.get('proposal_action') == 'execute' or reference):
                if proposal['source_version']['context_fingerprint'] != snapshot.context_fingerprint:
                    turn.state = 'failed'
                    turn.result = self._failure(turn, 'PROPOSAL_STALE')
                    return copy.deepcopy(turn.result)
                policy = TurnPolicy('execute', proposal['target_intent'], tuple([*proposal['constraints'], *policy.constraints]))
                turn.policy = policy
                turn.body['message'] = proposal['goal'] + '\n用户本轮要求：' + message + '\n范围：' + '\n'.join(proposal['scope']) + '\n约束：' + '\n'.join(policy.constraints)
                proposal['state'] = 'selected'
                self.selected_proposal = copy.deepcopy(proposal)
        elif reference and pending and pending.state == 'pending' and policy.mode == 'execute':
            if pending.snapshot.context_fingerprint != snapshot.context_fingerprint:
                turn.state = 'failed'
                turn.result = self._failure(turn, 'PLAN_STALE')
                return copy.deepcopy(turn.result)
            turn.plan = copy.deepcopy(pending.plan)
            turn.plan['constraints'] = list(dict.fromkeys([*turn.plan['constraints'], *policy.constraints]))
            turn.policy = replace(policy, task_intent=pending.plan['task_intent'])
            policy = turn.policy
            pending.state = 'cancelled'
            pending.result = self._failure(pending, 'PLAN_REPLACED_BY_TURN')
            self.pending_turn_id = None
        elif reference and policy.mode == 'execute':
            turn.state = 'failed'
            turn.result = self._failure(turn, 'REFERENCE_UNAVAILABLE')
            return copy.deepcopy(turn.result)
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
                'proposals': list(self.proposals.values()), 'selected_proposal_id': self.selected_proposal.get('proposal_id') if self.selected_proposal else None}

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
        if turn.state == 'extraction_pending' and body.get('approve') is not False:
            if body.get('approve_extraction') is not True:
                return copy.deepcopy(turn.result)
            extractions = body.get('confirmed_extractions')
            if not isinstance(extractions, list) or not extractions or not all(isinstance(item, dict) for item in extractions):
                return self._failure(turn, 'INVALID_EXTRACTIONS')
            turn.body['confirmed_extractions'] = copy.deepcopy(extractions)
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
            if not turn.plan or body.get('plan_id') != turn.plan['plan_id'] or (type(body.get('plan_version')) is not int or body.get('plan_version') != turn.plan['plan_version']):
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
            if kind == 'plan':
                kind, data = 'status', {'stage': 'plan', 'message': '正在准备执行步骤…'}
            if on_event:
                on_event(kind, {**data, 'turn_id': turn.turn_id, 'project_epoch': turn.snapshot.project_epoch})
        request = {**turn.body, 'intent': turn.policy.task_intent, 'stream': False, 'confirm_plan': False,
                   'execution_policy': {**turn.policy.to_dict(), 'mode': 'execute'},
                   '_turn_should_cancel': should_cancel, '_turn_on_event': emit}
        if turn.policy.task_intent == 'CREATE' and (turn.body.get('images') or turn.body.get('image_b64')):
            request['confirm_extraction'] = True
        if turn.plan:
            request['confirmed_plan'] = copy.deepcopy(turn.plan)
        try:
            if turn.policy.task_intent == 'CREATE':
                response = self.session.create_project_from_prompt(request)
            else:
                response = self.session.assistant_service.generate_with_assistant(request)
            if self.session.project is not None and turn.snapshot._project is not None and self.session.project.root == turn.snapshot._project.root:
                self.epoch = self.session.project_epoch
            kind = 'execution' if response.get('ok') else 'failed'
            turn.state = 'extraction_pending' if response.get('awaiting_extraction_confirmation') else ('completed' if response.get('ok') else 'failed')
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
