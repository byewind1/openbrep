"""Session-local conversation orchestration; source mutation stays in existing services."""
from __future__ import annotations

import copy
import queue
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from openbrep.runtime.turn_policy import TurnPolicy, decide_turn
from openbrep.source_snapshot import SourceSnapshot, capture_snapshot
from openbrep.workbench.working_intent import (
    gui_instruction,
    initial_intent,
    intent_context,
    reduce_intent,
)
from openbrep.workbench.working_intent_store import load_working_intent, save_working_intent

TURN_TTL_SECONDS = 1800
MAX_RECENT_TURNS = 128


def _task_events(session):
    """任务事件服务（卡04）；会话缺服务（替身/旧测试）时安全降级为 None。"""
    return getattr(session, "task_event_service", None)


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
    prepared_typed_plan: dict | None = None
    prepared_typed_plan_token: str | None = None
    working_intent_version: int = 0
    plan_revision: int = 0
    reference_asset_ids: list[str] | None = None
    repair_context: dict | None = None


class WorkbenchConversationService:
    def __init__(self, session: Any, *, clock=time.monotonic):
        self.session = session
        self.clock = clock
        self.epoch = session.project_epoch
        self.last_context_fingerprint = None
        self.project_identity = str(session.project.root) if session.project else None
        self.working_intent_persistence = "memory_only"
        self.working_intent_issue: str | None = None
        self.working_intent = self._load_project_intent()
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

    def pending_plan_snapshot(self) -> dict | None:
        """Read-only pending plan projection for UI reload; never executes a turn."""
        turn = self.turns.get(self.pending_turn_id or '')
        if turn is None or turn.state != 'pending' or not turn.plan:
            return None
        if self.clock() - turn.created_at > TURN_TTL_SECONDS:
            return None
        if turn.working_intent_version != self.working_intent['version'] or not turn.snapshot.matches(
            self.session.project, self.session.project_epoch, self._dependency_version()
        ):
            return None
        return {**copy.deepcopy(turn.plan), 'turn_id': turn.turn_id}

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

    def _load_project_intent(self) -> dict:
        project = self.session.project
        if project is None:
            self.working_intent_persistence = "memory_only"
            return initial_intent(self.session.session_id, self.session.project_epoch)
        state, issue = load_working_intent(
            project.root,
            session_id=self.session.session_id,
            project_epoch=self.session.project_epoch,
        )
        self.working_intent_persistence = "load_failed" if issue else "project"
        self.working_intent_issue = issue
        return state

    def _reduce_working_intent(self, event: dict) -> None:
        self.working_intent = reduce_intent(self.working_intent, event)
        project = self.session.project
        if project is None:
            self.working_intent_persistence = "memory_only"
            return
        try:
            save_working_intent(project.root, self.working_intent)
            self.working_intent_persistence = "project"
            self.working_intent_issue = None
        except Exception as exc:  # persistence failure must remain visible
            self.working_intent_persistence = "save_failed"
            self.working_intent_issue = f"working_intent_save_failed:{type(exc).__name__}"

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
        from openbrep.project_context import (
            load_project_knowledge,
            load_project_skills,
            resolve_project_context,
        )
        from openbrep.runtime.advisor import advise
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
        if answer.assumptions:
            self._reduce_working_intent({'kind': 'assumptions', 'message_id': turn.turn_id, 'values': list(answer.assumptions)})
        proposals = []
        for proposal in answer.proposals:
            bound = {**proposal, 'proposal_id': uuid.uuid4().hex, 'source_version': turn.snapshot.source_version, 'state': 'proposed'}
            proposals.append(bound)
            self.proposals[bound['proposal_id']] = copy.deepcopy(bound)
            while len(self.proposals) > 20:
                self.proposals.popitem(last=False)
        if proposals:
            self._reduce_working_intent({'kind': 'proposals', 'proposals': list(self.proposals.values())})
        turn.working_intent_version = self.working_intent['version']
        details = {
            **answer.to_dict(),
            'proposals': proposals,
            'inspection': report.to_dict(),
            'knowledge_sources': knowledge.source_ids,
            'knowledge_omissions': knowledge.omitted_source_ids,
        }
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
            turn.plan = {**answer.plan, 'plan_id': uuid.uuid4().hex, 'plan_version': turn.plan_revision + 1, 'task_intent': intent,
                         'constraints': list(dict.fromkeys([*turn.policy.constraints, *answer.plan['constraints']])),
                         'project_epoch': turn.snapshot.project_epoch, 'source_version': turn.snapshot.source_version,
                         'working_intent_version': turn.working_intent_version}
            turn.state = 'pending'
            self.pending_turn_id = turn.turn_id
            return self._response(turn, 'awaiting_confirmation', awaiting_confirmation=True, pending_plan=turn.plan,
                                  advisor=details, assistant={'kind': 'advisor', 'reply': answer.reply})
        turn.state = 'completed'
        return self._response(turn, 'advice', advisor=details, assistant={'kind': 'advisor', 'reply': answer.reply})

    def clear(self, *, persist: bool = True):
        for turn in self.turns.values():
            if turn.state in {'prepared', 'pending'}:
                self._discard_prepared_typed_plan(turn)
                turn.state = 'cancelled'
                turn.result = self._response(turn, 'cancelled', cancelled=True)
                self._record_task_terminal(turn, kind='cancelled', state='cancelled',
                                           message='项目会话已切换或历史已清除，任务终止。')
        self.pending_turn_id = None
        self.selected_proposal = None
        self.proposals.clear()
        self.epoch = self.session.project_epoch
        self.last_context_fingerprint = None
        self.project_identity = str(self.session.project.root) if self.session.project else None
        self.working_intent = initial_intent(self.session.session_id, self.session.project_epoch)
        self.working_intent_issue = None
        if persist and self.session.project is not None:
            try:
                save_working_intent(self.session.project.root, self.working_intent)
                self.working_intent_persistence = "project"
            except Exception as exc:
                self.working_intent_persistence = "save_failed"
                self.working_intent_issue = f"working_intent_save_failed:{type(exc).__name__}"

    def _sync_epoch(self):
        if self.epoch != self.session.project_epoch:
            identity = str(self.session.project.root) if self.session.project else None
            if identity is not None and identity == self.project_identity:
                self._reduce_working_intent({'kind': 'source_changed', 'project_epoch': self.session.project_epoch})
                for turn in self.turns.values():
                    if turn.state in {'pending', 'prepared'}:
                        self._discard_prepared_typed_plan(turn)
                        turn.state = 'stale'
                        turn.result = self._failure(turn, 'PLAN_STALE' if turn.plan else 'SOURCE_CHANGED')
                self.pending_turn_id = None
                self.epoch = self.session.project_epoch
            else:
                self.clear(persist=False)
                self.working_intent = self._load_project_intent()

    def _dependency_version(self):
        return getattr(self.session, 'dependency_context_version', None)

    def _response(self, turn: PreparedTurn | None, kind: str, **payload):
        # identity 契约（卡02）：project_epoch = turn 开始时的会话代次（事件
        # 过滤/守卫用它）；current_project_epoch + session_id = 响应生成时的
        # 服务端当前身份，前端用于发现代次漂移，不把旧任务结果盲接入新项目。
        return {'ok': kind not in {'failed'}, 'result_kind': kind,
                'turn_id': turn.turn_id if turn else None,
                'project_epoch': turn.snapshot.project_epoch if turn else self.session.project_epoch,
                'session_id': self.session.session_id,
                'current_project_epoch': self.session.project_epoch,
                'working_intent': self.intent_summary(),
                **({'repair_context': copy.deepcopy(turn.repair_context)} if turn and turn.repair_context else {}),
                **payload}

    def _checkpoint_visual_repair(self, turn: PreparedTurn) -> dict | None:
        """Save the exact source/spec state immediately before an approved repair."""
        context = turn.repair_context
        if not isinstance(context, dict) or context.get('state') != 'awaiting_approval':
            return None
        if self.session.project is None:
            return {'code': 'PROJECT_UNAVAILABLE', 'error': '修复执行前项目已关闭。'}
        from openbrep.project_write_lock import project_write_lock
        from openbrep.revisions import create_revision
        from openbrep.source_fingerprint import compute_source_fingerprint

        root = self.session.project.root
        if context.get('project_root') and Path(root).resolve() != Path(str(context['project_root'])).resolve():
            return {'code': 'PROJECT_CHANGED', 'error': '项目已切换，修复计划已失效。'}
        expected = str(context.get('before_source_fingerprint') or '')
        with project_write_lock(root):
            current = compute_source_fingerprint(root)
            if not expected or current != expected:
                return {'code': 'REPAIR_SOURCE_STALE', 'error': '准备修复后源码已变化，请重新对照后再试。'}
            try:
                revision = create_revision(
                    root,
                    message='visual repair before snapshot',
                    gsm_name=self.session.project.name,
                    metadata={'repair': {
                        'review_id': context.get('review_id'),
                        'finding_id': context.get('finding_id'),
                        'run_id': context.get('run_id'),
                    }},
                    trigger='visual_repair',
                    intent='REPAIR',
                    user_instruction=str(turn.body.get('message') or ''),
                )
            except Exception as exc:
                return {'code': 'REPAIR_CHECKPOINT_FAILED', 'error': f'无法保存修复前版本：{exc}'}
        context['before_revision_id'] = revision.revision_id
        context['state'] = 'executing'
        return None

    def _finish_visual_repair(self, turn: PreparedTurn, response: dict) -> None:
        """Restore a failed repair that regressed a required check; otherwise require recheck."""
        context = turn.repair_context
        if not isinstance(context, dict) or context.get('state') != 'executing':
            return
        from openbrep.source_fingerprint import compute_source_fingerprint

        root = self.session.project.root if self.session.project is not None else None
        if root is not None and context.get('project_root') and Path(root).resolve() != Path(str(context['project_root'])).resolve():
            context['state'] = 'restore_conflict'
            context['reason'] = 'project_changed_after_repair'
            return
        after = compute_source_fingerprint(root) if root is not None else None
        assistant = response.get('assistant') if isinstance(response.get('assistant'), dict) else {}
        verification = assistant.get('verification') if isinstance(assistant.get('verification'), dict) else {}
        required_failed = verification.get('requirements_passed') is False
        execution_failed = response.get('ok') is not True
        context['repair_run_id'] = assistant.get('run_id')
        context['after_source_fingerprint'] = after
        if required_failed or (execution_failed and after != context.get('before_source_fingerprint')):
            if after == context.get('before_source_fingerprint'):
                context['state'] = 'repair_failed' if execution_failed else 'requirements_failed_without_change'
                context['reason'] = 'required_check_failed' if required_failed else 'execution_failed'
                return
            from openbrep.project_write_lock import project_write_lock
            from openbrep.revisions import restore_revision

            before_revision_id = str(context.get('before_revision_id') or '')
            if not before_revision_id or root is None:
                context['state'] = 'restore_unavailable'
                context['reason'] = 'missing_before_revision'
                return
            try:
                with project_write_lock(root):
                    current = compute_source_fingerprint(root)
                    if current != after:
                        context['state'] = 'restore_conflict'
                        context['reason'] = 'source_changed_after_repair'
                        return
                    restored = restore_revision(
                        root,
                        before_revision_id,
                        message='restore rejected visual repair',
                    )
                    context['restore_revision_id'] = restored.revision_id
                    context['after_source_fingerprint'] = compute_source_fingerprint(root)
                    context['state'] = 'restored_after_requirement_failure' if required_failed else 'restored_after_execution_failure'
                    context['reason'] = 'required_check_failed' if required_failed else 'execution_failed'
                    refresh = getattr(self.session, 'refresh_same_project', None)
                    if callable(refresh):
                        from openbrep.hsf_project import HSFProject
                        refresh(HSFProject.load_from_disk(str(root)))
            except Exception as exc:
                context['state'] = 'restore_failed'
                context['reason'] = f'{type(exc).__name__}: {exc}'
            return
        context['state'] = 'recheck_required' if response.get('ok') is True else 'repair_failed'

    def _failure(self, turn, code, error=None):
        return self._response(turn, 'failed', code=code, error=error or code)

    def _record_task_terminal(self, turn, *, kind, state=None, message=None, error_code=None):
        """卡04：终止事件（幂等落盘）；缺服务时静默跳过。"""
        events = _task_events(self.session)
        if events is None or turn is None:
            return
        events.finish_turn(
            turn.turn_id, kind=kind, state=state, message=message, error_code=error_code,
        )

    def _record_task_stage(self, turn, *, stage, message=None, kind='preparing'):
        events = _task_events(self.session)
        if events is None or turn is None:
            return
        events.record_stage(turn.turn_id, kind=kind, stage=stage, message=message)

    def list_turn_events(self) -> dict:
        """RF04：任务索引（重开项目后前端据此展示未完成任务）。"""
        events = _task_events(self.session)
        if events is None:
            return {'ok': True, 'turns': []}
        return {'ok': True, 'turns': events.list_turns()}

    def read_turn_events(self, turn_id: str) -> dict:
        """卡04：任务事件只读查询（GET 天然 lock-free，不拿 _op_lock）。"""
        events = _task_events(self.session)
        if events is None:
            return {'ok': False, 'error': 'Task event service unavailable', 'events': [], 'turn_id': turn_id}
        return events.read_turn(turn_id)

    def _record_advice_outcome(self, turn, result: dict) -> None:
        """prepare 的咨询/计划/失败结果的终止或阶段事件（卡04）。"""
        if turn is None or not isinstance(result, dict):
            return
        if result.get('result_kind') == 'awaiting_confirmation':
            self._record_task_stage(turn, stage='plan_gate', message='修改计划已生成，待用户确认。')
            return
        if result.get('result_kind') == 'advice':
            reply = ((result.get('assistant') or {}).get('reply') or '')
            self._record_task_terminal(turn, kind='completed', state='advice',
                                       message=str(reply)[:200] or '咨询完成。')
            return
        if result.get('result_kind') == 'failed':
            self._record_task_terminal(turn, kind='failed', error_code=result.get('code'),
                                       message=str(result.get('error') or '本轮失败。'))

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
        if phase == 'revise':
            return self.revise_plan(body, should_cancel=should_cancel, on_event=on_event)
        if phase == 'status':
            return self.turn_status(body)
        if phase == 'execute':
            return self.execute(body, should_cancel=should_cancel, on_event=on_event)
        return self._failure(None, 'INVALID_PHASE')

    def turn_status(self, body: dict):
        """Read-only status lookup used to restore an approval card after UI reload."""
        turn = self.turns.get(str(body.get('turn_id') or ''))
        if turn is None:
            return self._failure(None, 'TURN_NOT_FOUND')
        if turn.state == 'pending' and turn.plan:
            valid = (
                self.clock() - turn.created_at <= TURN_TTL_SECONDS
                and turn.working_intent_version == self.working_intent['version']
                and turn.snapshot.matches(self.session.project, self.session.project_epoch, self._dependency_version())
            )
            if valid:
                return self._response(turn, 'awaiting_confirmation', state='pending', pending_plan=copy.deepcopy(turn.plan))
        return self._response(turn, 'status', state=turn.state)

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
        if on_event:
            # 卡05：prepare 流式反馈——语义路由等待
            on_event('status', {'stage': 'route', 'message': '正在判断本轮执行方式…'})
        if should_cancel and should_cancel():
            return self._response(None, 'cancelled', cancelled=True)
        if re.search(r'撤回|取消.*限制|withdraw', message, re.I):
            for constraint in list(self.working_intent['constraints']):
                if constraint['status'] == 'active' and constraint['value'] in message:
                    self._reduce_working_intent({'kind': 'withdraw', 'constraint_id': constraint['id']})
        previous_intent = self.intent_summary()
        try:
            snapshot = capture_snapshot(self.session.project, self.session.project_epoch, body.get('draft_scripts'), dependency_context_version=self._dependency_version())
            policy = decide_turn(message, requested_mode=body.get('requested_mode', 'auto'), history=body.get('history'),
                                 working_intent=self.intent_summary(), project_state={'has_project': self.session.project is not None},
                                 semantic_decision=self.semantic_decision)
        except (ValueError, TypeError) as exc:
            return self._failure(None, 'INVALID_TURN', str(exc))
        confirm_before_execute = body.get(
            'confirm_before_execute',
            bool(self.session.config.llm.confirm_before_execute),
        )
        if type(confirm_before_execute) is not bool:
            return self._failure(None, 'INVALID_TURN', 'confirm_before_execute 必须是布尔值。')
        if confirm_before_execute and policy.mode == 'execute':
            # Approval is a separate execution policy: keep routing and intent,
            # but prepare a plan and stop before the mutating execution path.
            policy = replace(policy, mode='plan')
        if self.last_context_fingerprint is not None and self.last_context_fingerprint != snapshot.context_fingerprint:
            self._reduce_working_intent({'kind': 'source_changed', 'project_epoch': self.session.project_epoch})
        self.last_context_fingerprint = snapshot.context_fingerprint
        allowed = {'client_turn_id', 'message', 'history', 'images', 'image_b64', 'image_mime', 'requested_mode', 'confirm_before_execute', 'project_epoch', 'draft_scripts', 'proposal_id', 'continue_from', 'proposal_action', 'assistant_settings', 'output_dir', 'project_name', 'effect_contract'}
        turn = PreparedTurn(uuid.uuid4().hex, client_id, copy.deepcopy({k: v for k, v in body.items() if k in allowed}), policy, snapshot, self.clock())
        self._reduce_working_intent({'kind': 'turn', 'message_id': turn.turn_id, 'message': message,
            'constraints': [c for c in policy.constraints if c in message], 'execute': policy.mode == 'execute' and not policy.error, 'task_intent': policy.task_intent})
        turn.working_intent_version = self.working_intent['version']
        self.turns[turn.turn_id] = turn
        self.client_ids[client_id] = turn.turn_id
        # 卡04：任务开始（项目归属在此固定）——accepted 事件先落盘
        events = _task_events(self.session)
        if events is not None:
            events.begin_turn(turn.turn_id, project_epoch=turn.snapshot.project_epoch,
                              message=message, requested_mode=policy.mode)
        if on_event:
            on_event('status', {'stage': policy.mode,
                                'message': '本轮将执行修改。' if policy.mode == 'execute' else '正在准备回答…',
                                'turn_id': turn.turn_id})
        while len(self.turns) > MAX_RECENT_TURNS:
            old_id, old = self.turns.popitem(last=False)
            self.client_ids.pop(old.client_turn_id, None)
        pending = self.turns.get(self.pending_turn_id or '')
        reference = re.search(r'按.*(?:方案|计划)|执行.*(?:方案|计划)|^(?:好的|继续|好|ok|continue)$', message, re.I)
        if re.search(r'取消.*计划|cancel.*plan', message, re.I) and pending and pending.state == 'pending':
            pending.state = 'cancelled'
            pending.result = self._response(pending, 'cancelled', cancelled=True)
            self.pending_turn_id = None
            turn.state = 'cancelled'
            turn.result = self._response(turn, 'cancelled', cancelled=True)
            self._record_task_terminal(turn, kind='cancelled', state='cancelled',
                                       message='计划已取消。')
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
                self._record_task_terminal(turn, kind='failed', error_code='PROPOSAL_NOT_FOUND')
                return copy.deepcopy(turn.result)
            if body.get('proposal_action') == 'select':
                proposal['state'] = 'selected'
                self.selected_proposal = copy.deepcopy(proposal)
                self._reduce_working_intent({'kind': 'select', 'proposal_id': proposal_id, 'message_id': turn.turn_id})
                turn.state = 'completed'
                turn.result = self._response(turn, 'advice', assistant={'kind': 'advisor', 'reply': '已选择该方案；尚未执行。'})
                self._record_task_terminal(turn, kind='completed', state='advice', message='已选择该方案；尚未执行。')
                return copy.deepcopy(turn.result)
            if policy.mode == 'execute' and (body.get('proposal_action') == 'execute' or reference):
                if proposal['source_version']['context_fingerprint'] != snapshot.context_fingerprint:
                    turn.state = 'failed'
                    turn.result = self._failure(turn, 'PROPOSAL_STALE')
                    self._record_task_terminal(turn, kind='failed', error_code='PROPOSAL_STALE')
                    return copy.deepcopy(turn.result)
                policy = TurnPolicy('execute', proposal['target_intent'], tuple([*proposal['constraints'], *policy.constraints]))
                turn.policy = policy
                turn.body['message'] = proposal['goal'] + '\n用户本轮要求：' + message + '\n范围：' + '\n'.join(proposal['scope']) + '\n约束：' + '\n'.join(policy.constraints)
                proposal['state'] = 'selected'
                self.selected_proposal = copy.deepcopy(proposal)
                self._reduce_working_intent({'kind': 'select', 'proposal_id': proposal_id, 'message_id': turn.turn_id, 'execute': True})
                turn.working_intent_version = self.working_intent['version']
        elif reference and pending and pending.state == 'pending' and policy.mode == 'execute':
            if pending.snapshot.context_fingerprint != snapshot.context_fingerprint:
                turn.state = 'failed'
                turn.result = self._failure(turn, 'PLAN_STALE')
                self._record_task_terminal(turn, kind='failed', error_code='PLAN_STALE')
                return copy.deepcopy(turn.result)
            turn.plan = copy.deepcopy(pending.plan)
            turn.plan['constraints'] = list(dict.fromkeys([*turn.plan['constraints'], *policy.constraints]))
            turn.policy = replace(policy, task_intent=pending.plan['task_intent'])
            policy = turn.policy
            pending.state = 'cancelled'
            pending.result = self._failure(pending, 'PLAN_REPLACED_BY_TURN')
            self.pending_turn_id = None
        elif reference and policy.mode == 'execute' and previous_intent.get('active_task'):
            task = previous_intent['active_task']
            goal = next((g['text'] for g in self.working_intent['goals'] if g['id'] in task['goal_refs']), '')
            turn.body['message'] = goal + '\n本轮要求：' + message
            self._reduce_working_intent({'kind': 'supersede_task', 'task_id': task['id']})
        elif reference and policy.mode == 'execute':
            turn.state = 'failed'
            turn.result = self._failure(turn, 'REFERENCE_UNAVAILABLE')
            self._record_task_terminal(turn, kind='failed', error_code='REFERENCE_UNAVAILABLE')
            return copy.deepcopy(turn.result)
        if policy.error:
            turn.state = 'failed'
            turn.result = self._failure(turn, policy.error)
            self._record_task_terminal(turn, kind='failed', error_code=policy.error)
        elif policy.mode == 'execute':
            self._reduce_working_intent({'kind': 'set_goal', 'task_id': turn.turn_id, 'goal': turn.body['message']})
            turn.working_intent_version = self.working_intent['version']
            turn.result = self._response(turn, 'ready_to_execute', mode='execute', task_intent=policy.task_intent, source_version=snapshot.source_version)
            self._record_task_stage(turn, stage='ready', message='任务已就绪，等待执行。')
        else:
            turn._prepare_on_event = on_event
            turn.result = self._prepare_advice(turn, should_cancel=should_cancel)
            self._record_advice_outcome(turn, turn.result)
            if on_event:
                result_kind = turn.result.get('result_kind')
                if result_kind == 'advice':
                    on_event('status', {'stage': 'done', 'message': '回答完成。', 'turn_id': turn.turn_id})
                elif result_kind == 'awaiting_confirmation':
                    on_event('status', {'stage': 'plan_gate', 'message': '修改计划已生成，待确认。', 'turn_id': turn.turn_id})
        if should_cancel and should_cancel():
            turn.state = 'cancelled'
            turn.result = self._response(turn, 'cancelled', cancelled=True)
            self._record_task_terminal(turn, kind='cancelled', state='cancelled')
        return copy.deepcopy(turn.result)

    def intent_summary(self) -> dict:
        pending = self.turns.get(self.pending_turn_id or '')
        return {**intent_context(self.working_intent), 'pending_plan': pending.plan if pending and pending.state == 'pending' else None,
                'proposals': list(self.proposals.values()), 'selected_proposal_id': self.selected_proposal.get('proposal_id') if self.selected_proposal else None,
                'persistence': self.working_intent_persistence, 'persistence_issue': self.working_intent_issue}

    def _prepare_advice(self, turn: PreparedTurn, *, should_cancel=None):
        if self.advisor is None:
            turn.state = 'failed'
            return self._failure(turn, 'ADVISOR_UNAVAILABLE')
        # 卡04：prepare 阶段流式可观测（咨询/计划生成中）
        self._record_task_stage(turn, stage='advice' if turn.policy.mode != 'plan' else 'plan',
                                message='正在生成顾问回答…' if turn.policy.mode != 'plan' else '正在生成修改计划…')
        on_event = getattr(turn, '_prepare_on_event', None)
        if callable(on_event):
            on_event('status', {'stage': 'advice' if turn.policy.mode != 'plan' else 'plan',
                                'message': '正在生成顾问回答…' if turn.policy.mode != 'plan' else '正在生成修改计划…'})
        if turn.policy.mode == 'plan' and (
            turn.policy.task_intent in {'CREATE', 'IMAGE'}
            or (self.session.project is None and turn.policy.task_intent == 'MODIFY')
        ):
            if turn.policy.task_intent == 'MODIFY':
                turn.policy = replace(turn.policy, task_intent='CREATE')
            return self._prepare_typed_create_plan(turn, should_cancel=should_cancel)
        return self.advisor(turn, should_cancel=should_cancel)

    def revise_plan(self, body: dict, *, should_cancel=None, on_event=None):
        """Re-plan a user-edited intent and bump its approval version."""
        turn = self.turns.get(str(body.get('turn_id') or ''))
        if turn is None or turn.state != 'pending' or not turn.plan:
            return self._failure(turn, 'PLAN_NOT_PENDING')
        if body.get('plan_id') != turn.plan.get('plan_id') or type(body.get('plan_version')) is not int or body.get('plan_version') != turn.plan.get('plan_version'):
            return {**self._failure(turn, 'PLAN_VERSION_MISMATCH'), 'pending_plan': copy.deepcopy(turn.plan)}
        instruction = body.get('revision_instruction')
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 2000:
            return self._failure(turn, 'INVALID_PLAN_REVISION', '请填写不超过 2000 字的计划修改要求。')
        if turn.working_intent_version != self.working_intent['version'] or not turn.snapshot.matches(
            self.session.project, self.session.project_epoch, self._dependency_version()
        ):
            turn.state = 'stale'
            turn.result = self._failure(turn, 'PLAN_STALE')
            self.pending_turn_id = None
            return copy.deepcopy(turn.result)
        if should_cancel and should_cancel():
            return self._response(turn, 'cancelled', cancelled=True)

        previous_version = int(turn.plan.get('plan_version') or 1)
        self._discard_prepared_typed_plan(turn)
        turn.plan_revision = previous_version
        turn.plan = None
        turn.result = None
        turn.body['message'] = (
            str(turn.body.get('message') or '').rstrip()
            + '\n用户要求修改本次计划：'
            + instruction.strip()
        )
        turn.policy = replace(turn.policy, mode='plan')
        turn.state = 'prepared'
        self.pending_turn_id = None
        if on_event:
            setattr(turn, '_prepare_on_event', on_event)
        if turn.policy.task_intent in {'CREATE', 'IMAGE'} or (
            self.session.project is None and turn.policy.task_intent == 'MODIFY'
        ):
            if turn.policy.task_intent == 'MODIFY':
                turn.policy = replace(turn.policy, task_intent='CREATE')
            return self._prepare_typed_create_plan(turn, should_cancel=should_cancel)
        return self.advisor(turn, should_cancel=should_cancel)

    def _discard_prepared_typed_plan(self, turn: PreparedTurn) -> None:
        token = turn.prepared_typed_plan_token
        if not token:
            return
        facade = getattr(self.session, 'project_service', None)
        service = getattr(facade, 'session_service', facade)
        discard = getattr(service, 'discard_prepared_typed_plan', None)
        if callable(discard):
            discard(token)
        turn.prepared_typed_plan_token = None
        turn.prepared_typed_plan = None

    def _prepare_typed_create_plan(self, turn: PreparedTurn, *, should_cancel=None):
        """Prepare the same typed CREATE plan later consumed by execution."""
        facade = getattr(self.session, 'project_service', None)
        service = getattr(facade, 'session_service', facade)
        prepare = getattr(service, 'prepare_typed_create_plan', None)
        if not callable(prepare):
            turn.state = 'failed'
            return self._failure(turn, 'TYPED_PLANNER_UNAVAILABLE')
        body = {
            **turn.body,
            '_turn_should_cancel': should_cancel,
            '_turn_on_event': getattr(turn, '_prepare_on_event', None),
        }
        result, response = prepare(body)
        if not response.get('ok') or result is None:
            turn.state = 'failed'
            return self._failure(turn, response.get('code') or 'PLAN_GENERATION_FAILED', response.get('error'))
        metadata = result.metadata or {}
        artifact = metadata.get('planning_artifact')
        prepared = metadata.get('prepared_typed_plan')
        object_plan = result.object_plan or {}
        if not isinstance(artifact, dict) or not isinstance(prepared, dict) or not isinstance(object_plan, dict):
            turn.state = 'failed'
            return self._failure(turn, 'INVALID_TYPED_PLAN')
        if turn.policy.task_intent == 'IMAGE':
            turn.policy = replace(turn.policy, task_intent='CREATE')
        old = self.turns.get(self.pending_turn_id or '')
        if old and old.state == 'pending':
            self._discard_prepared_typed_plan(old)
            old.state = 'stale'
            old.result = self._failure(old, 'PLAN_SUPERSEDED')
            old.result['pending_plan'] = old.plan
        execution = artifact.get('execution_plan') or {}
        steps = (execution.get('steps') or []) if isinstance(execution, dict) else []
        requirements = (execution.get('requirements') or []) if isinstance(execution, dict) else []
        plan = {
            'intent_summary': f"生成{object_plan.get('object_type') or 'GDL 构件'}",
            'user_visible_changes': list(object_plan.get('geometry') or [])[:12],
            'change_delta': list(object_plan.get('geometry_parts') or [])[:12],
            'preserved_constraints': list(turn.policy.constraints),
            'affected_files': list(dict.fromkeys(
                str(step.get('target')) for step in steps
                if isinstance(step, dict) and step.get('kind') == 'modify_script' and step.get('target')
            )),
            'risk': '；'.join(object_plan.get('risks') or []) or ('计划需要补充信息' if artifact.get('status') == 'needs_input' else '未识别到额外风险'),
            'constraints': list(turn.policy.constraints),
            'assumptions': list(object_plan.get('assumptions') or []),
            'acceptance_criteria': [str(item.get('text')) for item in requirements if isinstance(item, dict) and item.get('text')],
            'typed_plan': copy.deepcopy(artifact),
            'object_plan': copy.deepcopy(object_plan),
            'plan_id': uuid.uuid4().hex,
            'plan_version': turn.plan_revision + 1,
            'task_intent': turn.policy.task_intent,
            'project_epoch': turn.snapshot.project_epoch,
            'source_version': turn.snapshot.source_version,
            'working_intent_version': self.working_intent['version'],
        }
        turn.plan = plan
        turn.prepared_typed_plan = copy.deepcopy(prepared)
        register = getattr(service, 'register_prepared_typed_plan', None)
        if callable(register):
            turn.prepared_typed_plan_token = register(prepared)
        else:
            turn.state = 'failed'
            return self._failure(turn, 'TYPED_PLAN_UNAVAILABLE')
        turn.working_intent_version = self.working_intent['version']
        turn.state = 'pending'
        self.pending_turn_id = turn.turn_id
        return self._response(turn, 'awaiting_confirmation', awaiting_confirmation=True,
                              pending_plan=plan, planning_artifact=copy.deepcopy(artifact),
                              events=response.get('events') or [],
                              assistant={'kind': 'advisor', 'reply': result.plain_text})

    def execute(self, body: dict, *, should_cancel=None, on_event=None):
        turn = self.turns.get(str(body.get('turn_id') or ''))
        if turn is None:
            return self._failure(None, 'TURN_NOT_FOUND')
        events = _task_events(self.session)

        def _with_recording(result: dict) -> dict:
            # RF03：记录状态随响应上报（degraded = 执行记录保存失败，任务仍继续）
            if events is not None:
                result['events_recording'] = events.recording_status(turn.turn_id)
            return result
        if turn.state in {'completed', 'failed', 'cancelled', 'stale'}:
            return _with_recording(copy.deepcopy(turn.result))
        if turn.state == 'extraction_pending' and body.get('approve') is not False:
            if body.get('approve_extraction') is not True:
                return _with_recording(copy.deepcopy(turn.result))
            extractions = body.get('confirmed_extractions')
            if not isinstance(extractions, list) or not extractions or not all(isinstance(item, dict) for item in extractions):
                return self._failure(turn, 'INVALID_EXTRACTIONS')
            turn.body['confirmed_extractions'] = copy.deepcopy(extractions)
        if turn.state == 'executing':
            return self._response(turn, 'executing', state='executing')
        if body.get('approve') is False:
            self._discard_prepared_typed_plan(turn)
            turn.state = 'cancelled'
            turn.result = self._response(turn, 'cancelled', cancelled=True)
            self._record_task_terminal(turn, kind='cancelled', state='cancelled', message='用户取消了本次任务。')
            if self.pending_turn_id == turn.turn_id:
                self.pending_turn_id = None
            return _with_recording(copy.deepcopy(turn.result))
        if self.clock() - turn.created_at > TURN_TTL_SECONDS:
            self._discard_prepared_typed_plan(turn)
            turn.state = 'cancelled'
            turn.result = self._failure(turn, 'TURN_EXPIRED')
            self._record_task_terminal(turn, kind='cancelled', state='cancelled', error_code='TURN_EXPIRED')
            return _with_recording(copy.deepcopy(turn.result))
        if turn.policy.mode == 'consult':
            return self._failure(turn, 'READ_ONLY_TURN')
        if turn.policy.mode == 'plan':
            if turn.working_intent_version != self.working_intent['version']:
                self._discard_prepared_typed_plan(turn)
                turn.state = 'stale'
                turn.result = {**self._failure(turn, 'PLAN_STALE'), 'pending_plan': copy.deepcopy(turn.plan)}
                if self.pending_turn_id == turn.turn_id:
                    self.pending_turn_id = None
                return copy.deepcopy(turn.result)
            if body.get('approve') is not True:
                return self._failure(turn, 'APPROVAL_REQUIRED')
            if not turn.plan or body.get('plan_id') != turn.plan['plan_id'] or (type(body.get('plan_version')) is not int or body.get('plan_version') != turn.plan['plan_version']):
                return {**self._failure(turn, 'PLAN_VERSION_MISMATCH'), 'pending_plan': copy.deepcopy(turn.plan)}
            typed = turn.plan.get('typed_plan')
            if isinstance(typed, dict) and typed.get('status') == 'needs_input':
                return self._failure(turn, 'PLAN_NEEDS_INPUT', '计划依据需要澄清；当前计划不能执行。请取消后补充要求重新规划。')
        if not turn.snapshot.matches(self.session.project, self.session.project_epoch, self._dependency_version()):
            self._discard_prepared_typed_plan(turn)
            turn.state = 'stale'
            turn.result = self._failure(turn, 'PLAN_STALE' if turn.policy.mode == 'plan' else 'SOURCE_CHANGED')
            self._record_task_stage(turn, kind='source_changed', stage=None,
                                    message='源码已变化，旧任务令牌失效。')
            if turn.plan:
                turn.result['pending_plan'] = copy.deepcopy(turn.plan)
            return copy.deepcopy(turn.result)
        if self.active_turn_id:
            return self._failure(turn, 'TURN_BUSY')
        if should_cancel and should_cancel():
            turn.state = 'cancelled'
            turn.result = self._response(turn, 'cancelled', cancelled=True)
            return copy.deepcopy(turn.result)
        from openbrep.runtime.micro_modify import detect_micro_modify
        micro = detect_micro_modify(turn.body['message'], self.session.project) if self.session.project else None
        if micro:
            param = self.session.project.get_parameter(micro.param_name)
            tokens = [micro.param_name, param.description] + {'A': ['宽度', 'width'], 'B': ['深度', 'depth'], 'ZZYZX': ['高度', 'height']}.get(micro.param_name, [])
            protected = [c['value'] for c in self.working_intent['constraints'] if c['status'] == 'active']
            if any(re.search(r'不改|不要改|保持|do not|don.t|unchanged', c, re.I) and any(token and token.lower() in c.lower() for token in tokens) for c in protected):
                return self._failure(turn, 'CONSTRAINT_CONFLICT', '参数修改与仍有效的用户约束冲突；请明确撤回该约束。')
        repair_checkpoint_error = self._checkpoint_visual_repair(turn)
        if repair_checkpoint_error:
            turn.state = 'failed'
            turn.result = self._response(turn, 'failed', **repair_checkpoint_error)
            self._record_task_terminal(
                turn,
                kind='failed',
                error_code=str(repair_checkpoint_error.get('code') or 'REPAIR_CHECKPOINT_FAILED'),
            )
            return _with_recording(copy.deepcopy(turn.result))
        if not any(t['id'] == turn.turn_id for t in self.working_intent['tasks']):
            self._reduce_working_intent({'kind': 'start_task', 'task_id': turn.turn_id, 'goal': turn.body['message'], 'task_intent': turn.policy.task_intent})
        self.active_turn_id = turn.turn_id
        turn.state = 'executing'
        def emit(kind, data):
            if kind == 'plan':
                kind, data = 'status', {'stage': 'plan', 'message': '正在准备执行步骤…'}
            # RF02/RF03：事件先规范落盘，再以同一 event_id/seq/timestamp 广播
            # （canonical kind：preparing/tool_started/tool_finished/verification/
            #  public_commentary——前端 live 与复盘恢复消费同一事件形状）。
            if events is not None:
                for canonical in events.handle_pipeline_event(
                    turn.turn_id, kind, data if isinstance(data, dict) else {}
                ):
                    if on_event:
                        on_event(canonical['kind'], {
                            **canonical,
                            'turn_id': turn.turn_id,
                            'project_epoch': turn.snapshot.project_epoch,
                        })
                return
            if on_event:
                on_event(kind, {**data, 'turn_id': turn.turn_id, 'project_epoch': turn.snapshot.project_epoch})
        request = {**turn.body, 'intent': turn.policy.task_intent, 'stream': False, 'confirm_plan': False,
                   'execution_policy': {**turn.policy.to_dict(), 'mode': 'execute'},
                   '_turn_should_cancel': should_cancel, '_turn_on_event': emit,
                   'conversation_context': intent_context(self.working_intent)}
        # P1-A：执行注入已采用的参考资产（允许列表语义——模型不自取任意地址）。
        # 下一轮无新附件也能拿到同一 hash 的图；轮 body 的 images（用户新附件）
        # 优先，显式契约不被覆盖。no-attachment 续接"按图改"是本闭环的目标场景。
        try:
            reference_images = self.session.reference_service.execution_images()
        except AttributeError:
            reference_images = []
        injected_reference_ids: list[str] = []
        if reference_images and not request.get('images') and not request.get('image_b64'):
            request['images'] = reference_images
            injected_reference_ids = [asset.id for asset in self.session.reference_service.selected_assets()]
        turn.reference_asset_ids = list(injected_reference_ids)
        # R2/S1/S2（评审）：效果契约的 change_kind 必须来自本轮任务意图，
        # 不来自"有没有图片/参考资产"（有图 ≠ 要求形状变化——材质/新增选项
        # 任务会被 geometry 门误拦）。确定性关键词推导（子句级否定过滤 +
        # 重构信号返回 None）；S2：续接型语句（"按你的建议修改"）继承
        # working_intent 前文消息里的最近明确目标——原始事故
        # "搜回纹图→按你的建议修改"由此重新进入效果门。显式契约（调用方
        # 传入）优先；推导不出明确意图 → 不设门，通用验证与用户复核兜底。
        if not request.get('effect_contract'):
            from openbrep.runtime.effect_contract import derive_change_kind
            # A restarted backend has no message_refs yet. Restored user
            # history supplies context, never assistant suggestions or authority.
            restored_history = tuple(
                entry['content']
                for entry in (turn.body.get('history') or [])[-8:]
                if isinstance(entry, dict) and entry.get('role') == 'user'
                and isinstance(entry.get('content'), str)
            )
            history_texts = restored_history + tuple(
                str(ref.get('text') or '')
                for ref in self.working_intent.get('message_refs', [])[-8:]
                if isinstance(ref, dict)
            )
            derived_kind = derive_change_kind(turn.body.get('message') or '', history_texts=history_texts)
            if derived_kind:
                contract: dict = {'change_kind': derived_kind}
                if injected_reference_ids:
                    contract['reference_asset_ids'] = injected_reference_ids
                request['effect_contract'] = contract
        try:
            request['assistant_settings'] = gui_instruction(str(turn.body.get('assistant_settings') or self.session.assistant_settings), request['conversation_context'])
        except ValueError:
            self.active_turn_id = None
            turn.state = 'failed'
            turn.result = self._failure(turn, 'CONTEXT_TOO_LARGE')
            return copy.deepcopy(turn.result)
        if turn.policy.task_intent == 'CREATE' and not turn.prepared_typed_plan and (turn.body.get('images') or turn.body.get('image_b64')):
            request['confirm_extraction'] = True
        if turn.plan:
            request['confirmed_plan'] = copy.deepcopy(turn.plan)
        if turn.prepared_typed_plan_token:
            request['_prepared_typed_plan_token'] = turn.prepared_typed_plan_token
        try:
            if turn.policy.task_intent == 'CREATE':
                response = self.session.create_project_from_prompt(request)
            else:
                response = self.session.assistant_service.generate_with_assistant(request)
            if self.session.project is not None and turn.snapshot._project is not None and self.session.project.root == turn.snapshot._project.root:
                self.epoch = self.session.project_epoch
                self._reduce_working_intent({'kind': 'source_changed', 'project_epoch': self.session.project_epoch})
            kind = 'execution' if response.get('ok') else 'failed'
            turn.state = 'extraction_pending' if response.get('awaiting_extraction_confirmation') else ('completed' if response.get('ok') else 'failed')
            self.last_context_fingerprint = capture_snapshot(self.session.project, self.session.project_epoch).context_fingerprint
            self._reduce_working_intent({'kind': 'result', 'task_id': turn.turn_id, 'result': response})
            turn.result = self._response(turn, kind, **{k: v for k, v in response.items() if k not in {'turn_id', 'project_epoch', 'result_kind'}})
            if turn.repair_context:
                self._finish_visual_repair(turn, response)
                turn.result['repair_context'] = copy.deepcopy(turn.repair_context)
            if should_cancel and should_cancel():
                # Keep real delivery evidence if changes already happened.
                turn.state = 'cancelled'
                turn.result['cancelled'] = True
                turn.result['result_kind'] = 'cancelled'
            # 卡04：终止事件（完整交付/部分修改/无变化/取消/失败，幂等落盘）
            if events is not None:
                events.finish_turn_from_response(turn.turn_id, turn.result)
        except Exception:
            turn.state = 'failed'
            turn.result = self._failure(turn, 'EXECUTION_FAILED')
            self._record_task_terminal(turn, kind='failed', error_code='EXECUTION_FAILED',
                                       message='执行过程中发生错误。')
        finally:
            self.active_turn_id = None
            if self.pending_turn_id == turn.turn_id:
                self.pending_turn_id = None
        return _with_recording(copy.deepcopy(turn.result))
