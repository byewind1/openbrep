import json
from unittest.mock import Mock

import pytest

from openbrep.codex.turn import CodexTurnResult
from openbrep.llm import LLMAdapter
from openbrep.runtime.advisor import advise
from openbrep.runtime.inspection import inspect_snapshot
from openbrep.source_snapshot import capture_snapshot
from tests.test_conversation_service import session_at, prepare, plan_answer


@pytest.mark.parametrize('entry', ['managed', 'local'])
@pytest.mark.parametrize('mode', ['consult', 'plan'])
def test_codex_advisor_shares_schema_and_readonly_channel(tmp_path, entry, mode):
    session = session_at(tmp_path)
    session.config.llm.model = session.llm_model = 'openai-codex/gpt-5.6-luna'
    session.config.llm.codex_entry = entry
    session.config.llm.reasoning_effort = 'high'
    content = plan_answer() if mode == 'plan' else json.dumps({'conclusion': '建议加背板', 'suggestions': [], 'tradeoffs': []}, ensure_ascii=False)
    provider = Mock()
    provider.chat.return_value = CodexTurnResult(content=content, model=session.llm_model, finish_reason='stop')
    session.settings_service.codex_provider = provider
    session.settings_service.llm_adapter_factory = lambda config: LLMAdapter(config)
    result = prepare(session, message='有什么思路', requested_mode=mode)
    # Explicit text is stricter than the button; use a neutral plan instruction.
    if mode == 'plan':
        result = prepare(session, client_turn_id='c2', message='先出计划，添加背板', requested_mode=mode)
    assert result['ok']
    assert result['result_kind'] == ('awaiting_confirmation' if mode == 'plan' else 'advice')
    assert provider.chat.call_args.kwargs['reasoning_effort'] == 'high'
    assert 'tools' not in provider.chat.call_args.kwargs
    session.assistant_service.generate_with_assistant.assert_not_called()


def test_codex_advisor_images_use_ephemeral_image_transport(tmp_path):
    session = session_at(tmp_path)
    session.config.llm.model = session.llm_model = 'openai-codex/gpt-5.6-luna'
    llm = LLMAdapter(session.config.llm)
    provider = Mock()
    provider.chat.return_value = CodexTurnResult(content=json.dumps({'conclusion': '结构建议', 'suggestions': [], 'tradeoffs': []}), model=session.llm_model, finish_reason='stop')
    llm.codex_provider = provider
    snapshot = capture_snapshot(None, 0)
    report = inspect_snapshot(snapshot)
    image = {'mime': 'image/png', 'b64': 'test-image'}
    advise(snapshot, '看看结构，先别生成', llm=llm, report=report, images=[image])
    assert provider.chat.call_args.kwargs['images'] == [image]
