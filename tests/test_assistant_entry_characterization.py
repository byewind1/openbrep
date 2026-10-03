"""S0 entry baseline. Update these characterizations at cards 06/07."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openbrep.compiler import MockHSFCompiler
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import MockLLM
from openbrep.runtime.pipeline import TaskPipeline, TaskRequest
from openbrep.workbench.assistant_service import WorkbenchAssistantService


def test_route_fixture_schema():
    rows = json.loads((Path(__file__).parent / 'fixtures/assistant_route_eval.json').read_text())
    assert len(rows) >= 45
    assert len({r['id'] for r in rows}) == len(rows)
    for row in rows:
        assert isinstance(row['input'], str) and row['input'].strip()
        assert isinstance(row['context'], dict)
        assert isinstance(row['context']['has_project'], bool)
        assert row['expected_mode'] in {'consult', 'plan', 'execute'}
        assert row['expected_outcome'] in {'advice', 'awaiting_confirmation', 'ready_to_execute', 'cancelled', 'failed'}


def test_characterization_invalid_plan_now_fails_closed(tmp_path):
    # Characterization change (cards 03/06): maintainer approved fail-closed
    # in both unified and legacy. Planning happens before mutation dispatch.
    project = HSFProject.create_new('Shelf', work_dir=str(tmp_path))
    project.scripts[ScriptType.SCRIPT_3D] = 'BLOCK A, B, ZZYZX\nEND\n'
    project.save_to_disk()
    llm = MockLLM(responses=['not JSON'])
    pipeline = TaskPipeline(config=GDLAgentConfig(), trace_dir=str(tmp_path / 'traces'))
    pipeline._make_llm = lambda request: llm
    result = pipeline.execute(TaskRequest(user_input='给书架加一层层板', intent='MODIFY', project=project, confirm_plan=True))
    assert result.error == 'PLAN_GENERATION_FAILED'
    assert project.get_script(ScriptType.SCRIPT_3D) == 'BLOCK A, B, ZZYZX\nEND\n'
    session = SimpleNamespace(source_path=project.root, project=project)
    service = WorkbenchAssistantService(session)
    with patch.object(service, '_build_generate_pipeline', return_value=(MagicMock(execute=lambda _: result), None)), \
         patch.object(project, 'save_to_disk') as save:
        response = service._generate_with_confirmation({'message': '给书架加一层层板'})
    assert response['code'] == 'PLAN_GENERATION_FAILED'
    assert not response['ok']
    save.assert_not_called()


def test_characterization_stream_no_longer_implies_visible_planning(tmp_path):
    project = HSFProject.create_new('Shelf', work_dir=str(tmp_path))
    session = SimpleNamespace(
        source_path=project.root, project=project, project_epoch=1,
        llm_model='mock', assistant_settings='',
    )
    service = WorkbenchAssistantService(session)
    # No config on the fake pipeline: exercise just request construction.
    with patch.object(service, '_new_pipeline', return_value=object()):
        _, request = service._build_generate_pipeline(
            {'message': '给书架加一层层板'},
            {'image_b64': None, 'image_mime': 'image/png'},
            on_event=None, should_cancel=lambda: False,
        )
    assert request.agent_loop_plan is False  # characterization change (card06)
