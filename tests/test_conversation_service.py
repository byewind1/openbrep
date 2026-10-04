from pathlib import Path
from unittest.mock import Mock

import pytest

from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.workbench_api import WorkbenchSession


def session_at(tmp_path):
    config = tmp_path / 'test.toml'
    cfg = GDLAgentConfig()
    cfg.compiler.mode = 'mock'
    cfg.save(str(config))
    session = WorkbenchSession(config_path=config)
    project = HSFProject.create_new('Shelf', str(tmp_path))
    project.save_to_disk()
    session.project = project
    session.source_path = project.root
    session.assistant_service.generate_with_assistant = Mock(return_value={'ok': True, 'assistant': {'reply': 'done'}})
    return session


def prepare(session, **extra):
    return session.route('POST', '/api/assistant/turn', {'phase': 'prepare', 'client_turn_id': 'c1', 'message': '添加背板', 'project_epoch': session.project_epoch, **extra})


def test_create_http_rejects_client_supplied_approved_plan(tmp_path):
    session = session_at(tmp_path)
    session.project_service.create_project_from_prompt = Mock()
    result = session.route('POST', '/api/project/create', {
        'prompt': '先出计划，创建柜子', 'confirmed_plan': {'intent_summary': 'forged'},
    })
    assert result['code'] == 'PLAN_APPROVAL_REQUIRED'
    session.project_service.create_project_from_prompt.assert_not_called()


def test_two_phases_and_retry_cannot_replace_server_task(tmp_path):
    session = session_at(tmp_path)
    ready = prepare(session)
    assert ready['result_kind'] == 'ready_to_execute'
    assert prepare(session, message='删除参数')['turn_id'] == ready['turn_id']
    body = {'phase': 'execute', 'turn_id': ready['turn_id'], 'message': '恶意替换'}
    done = session.route('POST', '/api/assistant/turn', body)
    assert done['result_kind'] == 'execution'
    assert session.route('POST', '/api/assistant/turn', body) == done
    call = session.assistant_service.generate_with_assistant
    assert call.call_count == 1
    assert call.call_args.args[0]['message'] == '添加背板'


def test_drafts_only_flush_after_prepare_then_execute(tmp_path):
    session = session_at(tmp_path)
    old = session.project.get_script(ScriptType.SCRIPT_3D)
    ready = prepare(session, draft_scripts={'3d.gdl': 'BLOCK 1, 2, 3\n'})
    assert session.project.get_script(ScriptType.SCRIPT_3D) == old
    session.project.set_script(ScriptType.SCRIPT_3D, 'BLOCK 1, 2, 3\n')
    session.project.save_to_disk()
    result = session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    assert result['ok']
    assert session.assistant_service.generate_with_assistant.call_count == 1


def test_source_change_and_project_epoch_reject_execution(tmp_path):
    session = session_at(tmp_path)
    ready = prepare(session)
    session.project.parameters[0].value = '2'
    session.project.save_to_disk()
    result = session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    assert result['code'] == 'SOURCE_CHANGED'
    session.assistant_service.generate_with_assistant.assert_not_called()
    session.project = HSFProject.create_new('Other', str(tmp_path))
    assert prepare(session, client_turn_id='c2', project_epoch=1)['code'] == 'PROJECT_CHANGED'


def test_cancel_expire_and_restart_never_revive_tokens(tmp_path):
    session = session_at(tmp_path)
    ready = prepare(session)
    cancel = {'phase': 'execute', 'turn_id': ready['turn_id'], 'approve': False}
    result = session.route('POST', '/api/assistant/turn', cancel)
    assert result['cancelled']
    assert session.route('POST', '/api/assistant/turn', {**cancel, 'approve': True})['cancelled']
    ready = prepare(session, client_turn_id='c2')
    turn = session.conversation_service.turns[ready['turn_id']]
    turn.created_at -= 1900
    assert session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})['code'] == 'TURN_EXPIRED'
    restarted = session_at(tmp_path)
    assert restarted.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})['code'] == 'TURN_NOT_FOUND'
    session.assistant_service.generate_with_assistant.assert_not_called()


def test_legacy_switch_is_explicit_and_non_default_only(tmp_path):
    session = session_at(tmp_path)
    assert 'conversation_entry' not in Path(session.config_path).read_text()
    session.config.llm.conversation_entry = 'legacy'
    session.config.save(str(session.config_path))
    assert GDLAgentConfig.load(str(session.config_path)).llm.effective_conversation_entry() == 'legacy'
    result = prepare(session)
    assert result['http_status'] == 501
    assert result['code'] == 'UNIFIED_ENTRY_DISABLED'


def test_stream_worker_protects_validation_and_execution_with_session_lock(tmp_path):
    import threading
    session = session_at(tmp_path)
    ready = prepare(session)
    entered, release, changed = threading.Event(), threading.Event(), threading.Event()
    def execute(_body):
        entered.set()
        assert release.wait(2)
        return {'ok': True}
    session.assistant_service.generate_with_assistant.side_effect = execute
    stream = session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id'], 'stream': True})
    consume = threading.Thread(target=lambda: list(stream))
    consume.start()
    assert entered.wait(2)
    def mutate():
        with session._op_lock:
            changed.set()
    writer = threading.Thread(target=mutate)
    writer.start()
    assert not changed.wait(0.05)
    release.set()
    consume.join(2)
    writer.join(2)
    assert changed.is_set()
    assert not consume.is_alive()


def test_api_advice_is_model_backed_and_has_no_mutation_or_preview(tmp_path):
    import json
    from openbrep.llm import MockLLM
    from openbrep.source_fingerprint import compute_source_fingerprint
    session = session_at(tmp_path)
    llm = MockLLM(responses=[json.dumps({'conclusion': '建议加背板', 'suggestions': [], 'tradeoffs': []}, ensure_ascii=False)])
    session.settings_service.llm_adapter_factory = lambda config: llm
    before = compute_source_fingerprint(session.project.root)
    result = prepare(session, message='这个柜子比例不协调，有什么思路')
    assert result['result_kind'] == 'advice'
    assert result['assistant']['reply'] == '建议加背板'
    assert llm.call_count == 1
    assert compute_source_fingerprint(session.project.root) == before
    assert 'preview' not in result
    assert 'verification' not in result['assistant']
    session.assistant_service.generate_with_assistant.assert_not_called()


def test_assistant_compatibility_adapter_is_readonly_without_project(tmp_path):
    import json
    from openbrep.llm import MockLLM
    session = session_at(tmp_path)
    session.project = None
    session.source_path = None
    llm = MockLLM(responses=[json.dumps({'conclusion': '用PRISM_', 'suggestions': [], 'tradeoffs': []})])
    session.settings_service.llm_adapter_factory = lambda config: llm
    result = session.route('POST', '/api/assistant', {'message': '讲讲PRISM_'})
    assert result['result_kind'] == 'advice'
    assert llm.call_count == 1
    assert not (tmp_path / 'output').exists()


def plan_answer():
    import json
    return json.dumps({'conclusion': '建议先加背板', 'suggestions': [], 'tradeoffs': [], 'plan': {
        'intent_summary': '加背板', 'user_visible_changes': ['封闭背部'], 'affected_files': ['scripts/3d.gdl'], 'risk': '几何变化',
        'constraints': [], 'assumptions': [], 'acceptance_criteria': ['编译与验证通过'], 'finding_refs': [], 'optional_suggestions': ['可另行增加踢脚']
    }}, ensure_ascii=False)


def test_plan_lifecycle_approval_version_and_retries(tmp_path):
    from openbrep.llm import MockLLM
    session = session_at(tmp_path)
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer()])
    result = prepare(session, message='先别改，给我个方案添加背板')
    assert result['result_kind'] == 'awaiting_confirmation'
    plan = result['pending_plan']
    for key in ('plan_id', 'plan_version', 'task_intent', 'constraints', 'assumptions', 'acceptance_criteria', 'finding_refs', 'optional_suggestions', 'project_epoch', 'source_version', 'working_intent_version'):
        assert key in plan
    token = {'phase': 'execute', 'turn_id': result['turn_id'], 'plan_id': plan['plan_id'], 'plan_version': 1}
    assert session.route('POST', '/api/assistant/turn', token)['code'] == 'APPROVAL_REQUIRED'
    assert session.route('POST', '/api/assistant/turn', {**token, 'approve': True, 'plan_version': 2})['code'] == 'PLAN_VERSION_MISMATCH'
    assert session.route('POST', '/api/assistant/turn', {**token, 'approve': True, 'plan_version': True})['code'] == 'PLAN_VERSION_MISMATCH'
    done = session.route('POST', '/api/assistant/turn', {**token, 'approve': True})
    assert done['result_kind'] == 'execution'
    assert session.route('POST', '/api/assistant/turn', {**token, 'approve': True}) == done
    assert session.assistant_service.generate_with_assistant.call_count == 1


def test_plan_failure_and_stale_source_cannot_execute(tmp_path):
    from openbrep.llm import MockLLM
    session = session_at(tmp_path)
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=['not json'])
    failed = prepare(session, requested_mode='plan')
    assert failed['code'] == 'PLAN_GENERATION_FAILED'
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer()])
    result = prepare(session, client_turn_id='c2', requested_mode='plan')
    session.project.set_script(ScriptType.SCRIPT_3D, 'BLOCK 1, 2, 3\n')
    session.project.save_to_disk()
    plan = result['pending_plan']
    stale = session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': result['turn_id'], 'plan_id': plan['plan_id'], 'plan_version': 1, 'approve': True})
    assert stale['code'] == 'PLAN_STALE'
    assert stale['pending_plan'] == plan
    session.assistant_service.generate_with_assistant.assert_not_called()


def test_compatible_confirm_uses_only_unique_server_plan(tmp_path):
    from openbrep.llm import MockLLM
    session = session_at(tmp_path)
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer()])
    result = prepare(session, requested_mode='plan')
    done = session.route('POST', '/api/modify/confirm', {'approve': True})
    assert done['result_kind'] == 'execution'
    assert session.assistant_service.generate_with_assistant.call_count == 1


def test_no_project_plan_does_not_create_and_executes_create(tmp_path):
    from openbrep.llm import MockLLM
    session = session_at(tmp_path)
    session.project = None
    session.source_path = None
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer()])
    session.create_project_from_prompt = Mock(return_value={'ok': True})
    result = prepare(session, message='先给我一个书柜的建模方案')
    assert result['pending_plan']['task_intent'] == 'CREATE'
    assert not (tmp_path / 'output').exists()
    plan = result['pending_plan']
    assert session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': result['turn_id'], 'plan_id': plan['plan_id'], 'plan_version': 1, 'approve': True})['ok']
    session.create_project_from_prompt.assert_called_once()


def test_unified_image_create_keeps_extraction_gate_and_server_task(tmp_path):
    session = session_at(tmp_path)
    session.project = None
    session.create_project_from_prompt = Mock(side_effect=[
        {'ok': True, 'awaiting_extraction_confirmation': True, 'extractions': [{'fields': {'width': 2}}]},
        {'ok': True, 'assistant': {'reply': 'created'}}])
    ready = prepare(session, message='创建书架', images=[{'b64': 'aQ==', 'mime': 'image/png'}], confirmed_extractions=[{'untrusted': True}])
    token = {'phase': 'execute', 'turn_id': ready['turn_id']}
    pending = session.route('POST', '/api/assistant/turn', token)
    assert pending['awaiting_extraction_confirmation']
    assert session.create_project_from_prompt.call_args.args[0]['confirm_extraction'] is True
    assert 'confirmed_extractions' not in session.create_project_from_prompt.call_args.args[0]
    assert session.route('POST', '/api/assistant/turn', token) == pending
    assert session.create_project_from_prompt.call_count == 1
    completed = session.route('POST', '/api/assistant/turn', {**token, 'approve_extraction': True, 'confirmed_extractions': [{'fields': {'width': 3}}], 'message': 'replace task'})
    assert completed['ok']
    request = session.create_project_from_prompt.call_args.args[0]
    assert request['message'] == '创建书架'
    assert request['confirmed_extractions'] == [{'fields': {'width': 3}}]
    assert session.create_project_from_prompt.call_count == 2
    session.route('POST', '/api/assistant/turn', token)
    assert session.create_project_from_prompt.call_count == 2


def test_working_intent_failed_continue_retains_original_goal_and_does_not_call_router(tmp_path):
    session = session_at(tmp_path)
    session.conversation_service.semantic_decision = Mock(side_effect=AssertionError('No extra model call'))
    session.assistant_service.generate_with_assistant = Mock(return_value={'ok': False, 'error':'failed'})
    first = prepare(session)
    session.route('POST','/api/assistant/turn',{'phase':'execute','turn_id':first['turn_id']})
    continued = prepare(session, client_turn_id='c2', message='继续')
    assert continued['result_kind'] == 'ready_to_execute'
    session.route('POST','/api/assistant/turn',{'phase':'execute','turn_id':continued['turn_id']})
    request = session.assistant_service.generate_with_assistant.call_args.args[0]
    assert '添加背板' in request['message']
    assert '当前工作计划' in request['assistant_settings']
    assert request['conversation_context']['message_refs']
    session.conversation_service.semantic_decision.assert_not_called()


def test_effective_constraint_blocks_micro_fastpath_and_clear_invalidates_tokens(tmp_path):
    from openbrep.workbench.working_intent import reduce_intent
    session = session_at(tmp_path)
    service = session.conversation_service
    service._sync_epoch()
    service.working_intent = reduce_intent(service.working_intent, {'kind':'turn','message_id':'old','message':'不改宽度','constraints':['不改宽度']})
    ready = prepare(session, message='把A改成2')
    result = session.route('POST','/api/assistant/turn',{'phase':'execute','turn_id':ready['turn_id']})
    assert result['code'] == 'CONSTRAINT_CONFLICT'
    session.assistant_service.generate_with_assistant.assert_not_called()
    session.route('DELETE','/api/assistant/history',{})
    assert not service.working_intent['goals']
    assert service.turns[ready['turn_id']].state == 'cancelled'


def test_plan_working_version_and_same_project_reload_preserve_goals(tmp_path):
    from openbrep.llm import MockLLM
    session = session_at(tmp_path)
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer()])
    plan = prepare(session, requested_mode='plan')
    session.conversation_service.working_intent['version'] += 1
    result = session.route('POST','/api/assistant/turn',{'phase':'execute','turn_id':plan['turn_id'], 'approve':True,
        'plan_id':plan['pending_plan']['plan_id'],'plan_version':1})
    assert result['code'] == 'PLAN_STALE'
    assert result['pending_plan'] == plan['pending_plan']
    ready = prepare(session, client_turn_id='c2')
    goals = session.conversation_service.working_intent['goals'][:]
    session.project = HSFProject.load_from_disk(str(session.source_path))
    session.conversation_service._sync_epoch()
    assert session.conversation_service.working_intent['goals'] == goals
    assert session.conversation_service.turns[ready['turn_id']].state == 'stale'


def test_gui_context_absence_keeps_generation_request_bytes_identical(tmp_path):
    from openbrep.workbench.project_session_service import validate_image_payload
    session = session_at(tmp_path)
    body = {'message': '添加背板', 'assistant_settings': ' exact settings\n  '}
    _, first = session.assistant_service._build_generate_pipeline(body, validate_image_payload(body), on_event=None)
    _, second = session.assistant_service._build_generate_pipeline({**body,'conversation_context':None}, validate_image_payload(body), on_event=None)
    assert first.assistant_settings.encode() == second.assistant_settings.encode() == body['assistant_settings'].encode()
    assert first.conversation_context is None and second.conversation_context is None


def test_effect_contract_passes_through_to_execution_request(tmp_path):
    """P0-A：turn body 的 effect_contract 必须原样进入执行请求（GUI 显式传契约），
    无契约时不注入。"""
    session = session_at(tmp_path)
    ready = prepare(session, effect_contract={'change_kind': 'geometry', 'reference_asset_ids': ['ref1']})
    session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    call = session.assistant_service.generate_with_assistant.call_args.args[0]
    assert call['effect_contract'] == {'change_kind': 'geometry', 'reference_asset_ids': ['ref1']}

    (tmp_path / '2').mkdir()
    session2 = session_at(tmp_path / '2')
    ready2 = prepare(session2, client_turn_id='c9')
    session2.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready2['turn_id']})
    call2 = session2.assistant_service.generate_with_assistant.call_args.args[0]
    assert 'effect_contract' not in call2


def test_selected_reference_injected_into_execution(tmp_path, monkeypatch):
    """P1-A 闭环：上一轮显式采用的参考资产，下一轮无新附件执行时按允许列表
    注入（images + effect_contract.reference_asset_ids），不需要用户重发图片。"""
    import base64 as _b64

    from openbrep.workbench.reference_service import WorkbenchReferenceService

    png = _b64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    monkeypatch.setattr(
        'openbrep.workbench.reference_service._download_image', lambda url, **kwargs: (png, 'image/png')
    )
    session = session_at(tmp_path)
    assert isinstance(session.reference_service, WorkbenchReferenceService)
    adopt = session.route('POST', '/api/references/adopt', {'url': 'https://images.example.com/hw.png', 'alt': '回纹'})
    assert adopt['ok'], adopt.get('error')
    # R2：契约 change_kind 来自消息意图（"回纹"→ geometry），不来自"有参考资产"
    ready = prepare(session, client_turn_id='c-ref', message='把回纹改成连续方折')
    session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    call = session.assistant_service.generate_with_assistant.call_args.args[0]
    assert call['images'][0]['mime'] == 'image/png'
    assert _b64.b64decode(call['images'][0]['b64']) == png
    contract = call['effect_contract']
    assert contract['change_kind'] == 'geometry'
    assert adopt['asset']['id'] in contract['reference_asset_ids']


def test_ambiguous_message_with_reference_gets_no_forced_contract(tmp_path, monkeypatch):
    """R2：意图不明的带参考轮不得强加契约（有图 ≠ 要求形状变化）。"""
    import base64 as _b64

    png = _b64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    monkeypatch.setattr(
        'openbrep.workbench.reference_service._download_image', lambda url, **kwargs: (png, 'image/png')
    )
    session = session_at(tmp_path)
    adopt = session.route('POST', '/api/references/adopt', {'url': 'https://images.example.com/hw.png'})
    assert adopt['ok']
    ready = prepare(session, client_turn_id='c-ref2')  # '添加背板'——无明确变化意图
    session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    call = session.assistant_service.generate_with_assistant.call_args.args[0]
    # 图仍注入（模型可以看到参考），但不强加效果门
    assert call['images'] and call['images'][0]['mime'] == 'image/png'
    assert 'effect_contract' not in call


# ── F1（review 2026-10-04）：贯穿 prepare→execute→真实 assistant service→TaskRequest ──

class _RecordingPipeline:
    """替身 pipeline：捕获构造与 execute 收到的 TaskRequest。"""

    captured: list = []
    result = None

    def __init__(self, **kwargs):
        _RecordingPipeline.captured.append(self)
        self.kwargs = kwargs
        self.request = None

    def execute(self, request):
        self.request = request
        return _RecordingPipeline.result


def _real_session(tmp_path, result):
    from openbrep.workbench.assistant_service import WorkbenchAssistantService

    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    session = session_at(tmp_path)
    # 恢复被 session_at mock 掉的真实 generate 方法（贯穿到真实 TaskRequest 构造）
    session.assistant_service.generate_with_assistant = (
        WorkbenchAssistantService.generate_with_assistant.__get__(session.assistant_service, WorkbenchAssistantService)
    )
    _RecordingPipeline.captured = []
    _RecordingPipeline.result = result
    session.pipeline_class = _RecordingPipeline
    return session


def test_effect_contract_reaches_real_task_request(tmp_path):
    """F1：conversation 层透传的 effect_contract 必须出现在真实 TaskRequest 上
    （此前 _build_generate_pipeline 丢弃该字段，实际执行完全绕过效果门）。"""
    from openbrep.runtime.pipeline import TaskResult

    session = _real_session(tmp_path, TaskResult(
        success=True, plain_text='done', scripts={'paramlist.xml': 'x'}))
    ready = prepare(session, client_turn_id='c-f1a', effect_contract={'change_kind': 'geometry'})
    session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    pipelines = [p for p in _RecordingPipeline.captured if p.request is not None]
    assert pipelines, 'assistant service must run the real pipeline construction'
    request = pipelines[-1].request
    assert request.effect_contract == {'change_kind': 'geometry'}, request.effect_contract


def test_selected_reference_reaches_real_task_request(tmp_path, monkeypatch):
    """F1：跨轮采用路径——已采用参考资产注入真实 TaskRequest 的 images 与
    effect_contract.reference_asset_ids。"""
    import base64 as _b64

    png = _b64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    monkeypatch.setattr(
        'openbrep.workbench.reference_service._download_image', lambda url, **kwargs: (png, 'image/png')
    )
    from openbrep.runtime.pipeline import TaskResult

    session = _real_session(tmp_path, TaskResult(success=True, plain_text='done', scripts={'scripts/3d.gdl': 'x'}))
    adopt = session.route('POST', '/api/references/adopt', {'url': 'https://images.example.com/hw.png'})
    assert adopt['ok'], adopt.get('error')
    ready = prepare(session, client_turn_id='c-f1b', message='把回纹改成连续方折')
    session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    pipelines = [p for p in _RecordingPipeline.captured if p.request is not None]
    assert pipelines
    request = pipelines[-1].request
    assert request.images and request.images[0].mime == 'image/png'
    assert adopt['asset']['id'] in (request.effect_contract or {}).get('reference_asset_ids', [])


def test_no_effect_result_keeps_task_incomplete_through_real_service(tmp_path):
    """F1：真实服务链返回 no_effect 验收时，任务不得关闭为 completed。"""
    from openbrep.runtime.pipeline import TaskResult

    session = _real_session(tmp_path, TaskResult(
        success=False, plain_text='已写源码但无形态效果', scripts={'scripts/3d.gdl': 'x'},
        metadata={'acceptance': {'effect': {'required': True, 'change_kind': 'geometry',
                                             'satisfied': False, 'status': 'no_effect', 'reason': '签名相同'}}},
    ))
    ready = prepare(session, client_turn_id='c-f1c', effect_contract={'change_kind': 'geometry'})
    result = session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    assert result['ok'] is True  # 有产出照常交付（verification 如实 FAIL）
    tasks = session.conversation_service.working_intent['tasks']
    assert tasks[-1]['state'] == 'incomplete'
