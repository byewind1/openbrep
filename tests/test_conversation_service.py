from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_new_turn_adopts_same_project_disk_source_after_partial_write(tmp_path):
    """A fresh turn must start from the HSF files left by a prior partial run.

    The prior run may have committed files while the session still holds an
    older HSFProject instance. Preparing a new token should reconcile that
    same project from disk; it must not invalidate the new token immediately.
    """
    session = session_at(tmp_path)
    project_root = session.project.root
    old_project = session.project

    from openbrep.hsf_project import HSFProject

    disk_project = HSFProject.load_from_disk(str(project_root))
    disk_project.set_script(ScriptType.SCRIPT_3D, 'BLOCK 1, 2, 3\n')
    disk_project.save_to_disk()
    # Model a prior partial pipeline write that reached disk without updating
    # the WorkbenchSession's in-memory project object.
    assert session.project is old_project

    ready = prepare(session, client_turn_id='after-partial')
    assert ready['result_kind'] == 'ready_to_execute'
    assert session.project.get_script(ScriptType.SCRIPT_3D) == 'BLOCK 1, 2, 3\n'

    result = session.route('POST', '/api/assistant/turn', {
        'phase': 'execute', 'turn_id': ready['turn_id'],
    })
    assert result['result_kind'] == 'execution'
    session.assistant_service.generate_with_assistant.assert_called_once()


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


def test_revising_a_pending_plan_replans_and_invalidates_the_old_approval_version(tmp_path):
    from openbrep.llm import MockLLM

    session = session_at(tmp_path)
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer(), plan_answer()])
    original = prepare(session, requested_mode="plan")
    old_plan = original["pending_plan"]

    revised = session.route("POST", "/api/assistant/turn", {
        "phase": "revise",
        "turn_id": original["turn_id"],
        "plan_id": old_plan["plan_id"],
        "plan_version": old_plan["plan_version"],
        "revision_instruction": "保留现有层数，只新增背板。",
    })

    assert revised["result_kind"] == "awaiting_confirmation"
    assert revised["pending_plan"]["plan_version"] == old_plan["plan_version"] + 1
    assert revised["pending_plan"]["plan_id"] != old_plan["plan_id"]
    turn = session.conversation_service.turns[original["turn_id"]]
    assert "只新增背板" in turn.body["message"]
    assert session.assistant_service.generate_with_assistant.call_count == 0
    stale_approval = session.route("POST", "/api/assistant/turn", {
        "phase": "execute", "turn_id": original["turn_id"], "approve": True,
        "plan_id": old_plan["plan_id"], "plan_version": old_plan["plan_version"],
    })
    assert stale_approval["code"] == "PLAN_VERSION_MISMATCH"
    assert stale_approval["pending_plan"] == revised["pending_plan"]


def test_revising_a_pending_plan_after_source_change_is_rejected(tmp_path):
    from openbrep.llm import MockLLM

    session = session_at(tmp_path)
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer()])
    original = prepare(session, requested_mode="plan")
    plan = original["pending_plan"]
    session.project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\nADDZ 2\n")
    session.project.save_to_disk()

    stale = session.route("POST", "/api/assistant/turn", {
        "phase": "revise", "turn_id": original["turn_id"], "plan_id": plan["plan_id"],
        "plan_version": plan["plan_version"], "revision_instruction": "只改材质。",
    })

    assert stale["code"] == "PLAN_STALE"
    assert session.assistant_service.generate_with_assistant.call_count == 0


def test_pending_plan_is_restored_in_snapshot_without_execution(tmp_path):
    from openbrep.llm import MockLLM

    session = session_at(tmp_path)
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer()])
    pending = prepare(session, requested_mode="plan")

    reopened_snapshot = session.route("GET", "/api/snapshot")

    assert reopened_snapshot["pending_plan"]["turn_id"] == pending["turn_id"]
    assert reopened_snapshot["pending_plan"]["plan_id"] == pending["pending_plan"]["plan_id"]
    assert session.assistant_service.generate_with_assistant.call_count == 0


def test_confirmation_preference_is_independent_from_requested_mode_and_read_only_until_approved(tmp_path):
    import json

    from openbrep.llm import MockLLM

    session = session_at(tmp_path)
    session.conversation_service.semantic_decision = lambda _payload: json.dumps({
        "mode": "execute", "task_intent": "MODIFY", "constraints": [],
    })
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer()])
    project_root = session.project.root
    before_scripts = {path.name: path.read_text(encoding="utf-8") for path in (project_root / "scripts").glob("*.gdl")}
    before_revisions = list((project_root / ".openbrep" / "revisions").glob("*"))

    pending = prepare(session, message="把柜体背面加上背板", requested_mode="auto", confirm_before_execute=True)

    assert pending["result_kind"] == "awaiting_confirmation"
    assert pending["pending_plan"]["task_intent"] == "MODIFY"
    assert session.conversation_service.turns[pending["turn_id"]].body["requested_mode"] == "auto"
    assert {path.name: path.read_text(encoding="utf-8") for path in (project_root / "scripts").glob("*.gdl")} == before_scripts
    assert list((project_root / ".openbrep" / "revisions").glob("*")) == before_revisions
    session.assistant_service.generate_with_assistant.assert_not_called()


def test_saved_approval_preference_applies_when_request_omits_single_turn_override(tmp_path):
    import json

    from openbrep.llm import MockLLM

    session = session_at(tmp_path)
    session.config.llm.confirm_before_execute = True
    session.conversation_service.semantic_decision = lambda _payload: json.dumps({
        "mode": "execute", "task_intent": "MODIFY", "constraints": [],
    })
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer()])

    pending = prepare(session, message="把柜体背面加上背板")

    assert pending["result_kind"] == "awaiting_confirmation"
    assert "confirm_before_execute" not in session.conversation_service.turns[pending["turn_id"]].body
    session.assistant_service.generate_with_assistant.assert_not_called()


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


def test_create_prepare_exposes_typed_plan_and_execution_reuses_server_copy(tmp_path):
    import json

    session = session_at(tmp_path)
    session.project = None
    session.source_path = None
    session.conversation_service.semantic_decision = lambda _payload: json.dumps(
        {'mode': 'plan', 'task_intent': 'CREATE', 'constraints': []}, ensure_ascii=False)
    artifact = {
        'status': 'ready',
        'candidate_spec': {'spec_id': 'spec-1', 'params': [{'gdl_name': 'A'}]},
        'execution_plan': {'plan_id': 'plan-1', 'plan_hash': 'frozen-hash', 'steps': [], 'requirements': []},
        'observations': [{'observation_id': 'obs-1'}],
        'issues': [],
    }
    prepared = {
        'object_plan': {'object_type': 'parametric_cabinet', 'geometry': ['柜体与双门'], 'geometry_parts': ['carcass'], 'assumptions': ['背板厚度未指定']},
        'planning_artifact': artifact,
        'enriched_instruction': 'frozen instruction',
        'vision_extractions': [{'schema_name': 'cabinet'}],
    }
    fake_result = SimpleNamespace(success=True, metadata={'planning_artifact': artifact, 'prepared_typed_plan': prepared},
                                  object_plan=prepared['object_plan'], plain_text='typed plan summary')
    service_call = Mock(return_value=(fake_result, {'ok': True, 'events': []}))
    session.project_service.session_service.prepare_typed_create_plan = service_call
    session.create_project_from_prompt = Mock(return_value={'ok': True, 'assistant': {'reply': 'created'}})

    ready = prepare(session, message='先给我一个柜体建模计划', requested_mode='plan')

    assert ready['result_kind'] == 'awaiting_confirmation', ready
    assert ready['pending_plan']['typed_plan'] == artifact
    assert ready['pending_plan']['object_plan'] == prepared['object_plan']
    service_call.assert_called_once()
    assert not (tmp_path / 'output').exists()
    plan = ready['pending_plan']
    done = session.route('POST', '/api/assistant/turn', {
        'phase': 'execute', 'turn_id': ready['turn_id'], 'plan_id': plan['plan_id'],
        'plan_version': 1, 'approve': True,
    })

    assert done['result_kind'] == 'execution'
    call_body = session.create_project_from_prompt.call_args.args[0]
    token = call_body['_prepared_typed_plan_token']
    assert session.project_service.session_service._consume_prepared_typed_plan({'_prepared_typed_plan_token': token}) == (prepared, True)
    assert '_prepared_typed_plan' not in call_body
    assert call_body.get('confirm_extraction') is not True


def test_create_plan_with_unresolved_input_cannot_be_approved_or_create_source(tmp_path):
    import json

    session = session_at(tmp_path)
    session.project = None
    session.source_path = None
    session.conversation_service.semantic_decision = lambda _payload: json.dumps(
        {'mode': 'plan', 'task_intent': 'CREATE', 'constraints': []}, ensure_ascii=False)
    artifact = {'status': 'needs_input', 'candidate_spec': None, 'execution_plan': None, 'issues': [{'code': 'MISSING_EXPLICIT_UNIT'}]}
    prepared = {'object_plan': {'object_type': 'cabinet'}, 'planning_artifact': artifact,
                'enriched_instruction': 'clarify unit', 'vision_extractions': []}
    fake_result = SimpleNamespace(success=True, metadata={'planning_artifact': artifact, 'prepared_typed_plan': prepared},
                                  object_plan=prepared['object_plan'], plain_text='needs clarification')
    session.project_service.session_service.prepare_typed_create_plan = Mock(
        return_value=(fake_result, {'ok': True, 'events': []}))
    session.create_project_from_prompt = Mock(return_value={'ok': True})

    ready = prepare(session, message='生成宽度1200的柜体，先给我计划', requested_mode='plan')
    plan = ready['pending_plan']
    result = session.route('POST', '/api/assistant/turn', {
        'phase': 'execute', 'turn_id': ready['turn_id'], 'plan_id': plan['plan_id'],
        'plan_version': plan['plan_version'], 'approve': True,
    })

    assert result['code'] == 'PLAN_NEEDS_INPUT'
    session.create_project_from_prompt.assert_not_called()


def test_compatible_confirm_uses_only_unique_server_plan(tmp_path):
    from openbrep.llm import MockLLM
    session = session_at(tmp_path)
    session.settings_service.llm_adapter_factory = lambda config: MockLLM(responses=[plan_answer()])
    prepare(session, requested_mode='plan')
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
    result = prepare(session, message='先生成一个书柜，给我一个计划', requested_mode='plan')
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


def test_continuation_inherits_prior_goal_contract_and_blocks_no_effect(tmp_path, monkeypatch):
    """S2（三轮 review）：冻结原始事故聊天结构——轮1 搜参考图（consult），
    轮2 "按你的建议进行修改"（语义判定为 execute）→ 继承前文唯一的 geometry
    目标契约；零几何变化的结果不得把任务关闭为 completed。"""
    import json as _json

    from openbrep.runtime.pipeline import TaskResult

    session = _real_session(tmp_path, TaskResult(
        success=False, plain_text='已按参考调整（源码有变化）', scripts={'scripts/3d.gdl': 'x'},
        metadata={'acceptance': {'effect': {'required': True, 'change_kind': 'geometry',
                                             'satisfied': False, 'status': 'no_effect', 'reason': '几何签名相同'}}},
    ))
    conversation = session.conversation_service
    # 真实链路中语义判定带完整历史：续接轮（"按你的建议修改"）判 execute，
    # 纯咨询轮判 consult——离线冻结这两条判定。
    def _frozen_semantic(payload):
        message = str(payload.get('message') or '')
        if '建议' in message and '修改' in message:
            return _json.dumps({'mode': 'execute', 'task_intent': 'MODIFY', 'constraints': []})
        return _json.dumps({'mode': 'consult', 'task_intent': 'CHAT', 'constraints': []})

    conversation.semantic_decision = _frozen_semantic

    # 轮1：原始事故的第一句（consult，不执行；进入 message_refs）
    turn1 = session.route('POST', '/api/assistant/turn', {
        'phase': 'prepare', 'client_turn_id': 'c-s2-1',
        'message': '你能不能搜个回纹的图片参考一下？', 'project_epoch': session.project_epoch,
    })
    assert turn1['result_kind'] in {'advice', 'failed'}  # 咨询轮；顾问失败不影响前文引用留存
    # 轮2：原始事故的第二句（续接执行）
    turn2 = session.route('POST', '/api/assistant/turn', {
        'phase': 'prepare', 'client_turn_id': 'c-s2-2',
        'message': '按你的建议进行修改', 'project_epoch': session.project_epoch,
    })
    assert turn2['result_kind'] == 'ready_to_execute', turn2
    result = session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': turn2['turn_id']})
    pipelines = [p for p in _RecordingPipeline.captured if p.request is not None]
    assert pipelines
    request = pipelines[-1].request
    # S2 断言：续接轮继承前文唯一的 geometry 目标 → 效果门启用
    assert request.effect_contract == {'change_kind': 'geometry'}, request.effect_contract
    # 零几何变化 → 任务不得 completed
    tasks = conversation.working_intent['tasks']
    assert tasks[-1]['state'] == 'incomplete'
    assert result['ok'] is True  # 有产出照常交付，验证报告如实 FAIL


def test_continuation_recovers_goal_from_restored_user_history(tmp_path):
    """Backend 重启后仍须使用前端恢复的用户目标，而非只看当前短句。"""
    import json

    from openbrep.runtime.pipeline import TaskResult

    session = _real_session(tmp_path, TaskResult(success=True, plain_text='done'))
    session.conversation_service.semantic_decision = lambda payload: json.dumps(
        {'mode': 'execute', 'task_intent': 'MODIFY', 'constraints': []})
    ready = prepare(session, message='按你的建议进行修改', history=[
        {'role': 'user', 'content': '搜个回纹的图片参考一下'},
        {'role': 'assistant', 'content': '也可以改材质，但先按参考修回纹'},
    ])
    assert ready['result_kind'] == 'ready_to_execute', ready
    session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    request = [p.request for p in _RecordingPipeline.captured if p.request is not None][-1]
    assert request.effect_contract == {'change_kind': 'geometry'}
