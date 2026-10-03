import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject
from openbrep.llm import MockLLM
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.runtime.turn_policy import decide_turn, explicit_policy


@pytest.mark.parametrize('text,mode', [
    ('这个柜子比例不协调，有什么思路', 'consult'),
    ('比例不协调，你直接帮我优化', 'execute'),
    ('先别改，看看怎么优化', 'plan'),
    ('不要改，只解释这次报错', 'consult'),
    ('把 shelf_count 改成 5', 'execute'),
    ('先出计划，把 shelf_count 改成 5', 'plan'),
    ('不要把 shelf_count 改成 5', 'consult'),
    ('不改宽度，把高度改为 2m', 'execute'),
    ('给这个柜子加个背板？', 'execute'),
    ('例如“把 shelf_count 改成 5”，这条命令如何解析', 'consult'),
])
def test_rule_contract(text, mode):
    semantic = Mock(side_effect=AssertionError('Rule fast path must not call LLM'))
    assert decide_turn(text, semantic_decision=semantic).mode == mode
    semantic.assert_not_called()


def test_mixed_constraints_keep_positive_operation():
    policy = explicit_policy('不改宽度，把高度改为 2m')
    assert policy.mode == 'execute'
    assert policy.constraints == ('不改宽度',)


@pytest.mark.parametrize('response', [None, '{bad', {'mode': 'write'}, {'mode': 'execute', 'task_intent': 'invalid'}])
def test_invalid_semantic_result_does_not_expand_permissions(response):
    semantic = Mock(return_value=response)
    result = decide_turn('比例不协调', semantic_decision=semantic)
    assert result.mode == 'consult'
    assert result.error == 'DECISION_FAILED'
    assert semantic.call_count == 1


def test_semantic_input_carries_history_and_working_intent():
    semantic = Mock(return_value={'mode': 'execute', 'task_intent': 'MODIFY'})
    history = [{'role': 'user', 'content': '优化比例'}]
    result = decide_turn('还是太笨重了', history=history, working_intent={'active_task': '优化比例'}, semantic_decision=semantic)
    assert result.mode == 'execute'
    assert semantic.call_args.args[0]['history'] == history


def test_missing_target_never_creates_a_substitute():
    result = decide_turn('修改这个柜子', project_state={'has_project': False})
    assert result.mode == 'consult'
    assert result.error == 'MISSING_TARGET'


@pytest.mark.parametrize('branch', ['micro', 'skill', 'dsl', 'loop', 'codex'])
def test_pipeline_readonly_guards_all_dispatch_branches(tmp_path, branch):
    project = HSFProject.create_new('Shelf', work_dir=str(tmp_path))
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / 'traces'))
    for name in ('_try_micro_modify', '_try_skill_ops', '_try_param_modify', '_handle_modify_agent_loop', '_handle_gdl'):
        setattr(pipeline, name, Mock(side_effect=AssertionError('Mutation branch entered')))
    project.save_to_disk = Mock(side_effect=AssertionError('Saved read-only source'))
    pipeline._is_codex_model_selected = Mock(return_value=branch == 'codex')
    request = TaskRequest(user_input='把 shelf_count 改成 5', intent='MODIFY', project=project, execution_policy={'mode': 'consult'})
    result = pipeline.execute(request)
    assert result.metadata['mode'] == 'consult'
    assert not project.root.exists()
    project.save_to_disk.assert_not_called()


def test_optional_request_fields_have_no_default_prompt_effect(tmp_path):
    messages = []
    for fields in ({}, {'execution_policy': None, 'conversation_context': None}):
        llm = MockLLM(responses=['你好'])
        pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / 'traces'))
        pipeline._make_llm = lambda request: llm
        pipeline.execute(TaskRequest(user_input='你好', intent='CHAT', **fields))
        messages.append(json.dumps(llm.call_history, ensure_ascii=False, sort_keys=True).encode())
    assert messages[0] == messages[1]


@pytest.mark.parametrize('text', [
    '该对象编译失败：Error in 3d.gdl: IF/ENDIF mismatch。请定位并修复 3d 脚本中的错误，使编译通过，不要做其他结构性改动。',
    '删除参数表里不再使用的支腿样式参数 leg_style，其他参数保持不变。',
])
def test_execution_with_error_syntax_and_preservation_constraints(text):
    assert explicit_policy(text).mode == 'execute'

@pytest.mark.parametrize('intent', ['MODIFY', 'DEBUG', 'REPAIR', 'CREATE', 'IMAGE'])
@pytest.mark.parametrize('branch', ['micro', 'skill', 'dsl', 'loop', 'codex'])
def test_confirm_plan_precedes_all_execution_engines(tmp_path, intent, branch):
    project = HSFProject.create_new('Shelf', work_dir=str(tmp_path))
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / 'traces'))
    for name in ('_try_micro_modify', '_try_skill_ops', '_try_param_modify', '_handle_modify_agent_loop', '_handle_gdl', '_make_compiler'):
        setattr(pipeline, name, Mock(side_effect=AssertionError('Execution setup before plan approval')))
    pipeline._is_codex_model_selected = Mock(return_value=branch == 'codex')
    llm = MockLLM(responses=[json.dumps({'intent_summary': '添加背板', 'user_visible_changes': ['背部封闭'], 'affected_files': ['scripts/3d.gdl'], 'risk': '改变形状'})])
    pipeline._make_llm = lambda request: llm
    result = pipeline.execute(TaskRequest(user_input='添加背板', intent=intent, project=project, confirm_plan=True, output_dir=str(tmp_path / 'out')))
    assert result.metadata['awaiting_confirmation']
    assert not project.root.exists()
    assert not (tmp_path / 'out').exists()


def test_parameter_plan_remains_zero_llm_and_zero_write(tmp_path):
    from openbrep.hsf_project import GDLParameter
    project = HSFProject.create_new('Shelf', work_dir=str(tmp_path))
    project.parameters.append(GDLParameter('shelf_count', 'Integer', '层板数', '4'))
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / 'traces'))
    pipeline._make_llm = Mock(side_effect=AssertionError('Unnecessary model call'))
    result = pipeline.execute(TaskRequest(user_input='先出计划，把 shelf_count 改成 5', project=project))
    assert result.metadata['awaiting_confirmation']
    assert result.metadata['pending_plan']['affected_files'] == ['paramlist.xml']
    assert project.parameters[-1].value == '4'
    assert not project.root.exists()
