import json

from openbrep.contracts.object_spec import observation_from_modeling_plan
from openbrep.llm import LLMResponse
from openbrep.runtime.pipeline import ImageRef
from openbrep.vision.domain_skill_schema import select_domain_vision_schema
from openbrep.vision.harness import run


def test_domain_skill_dispatch_is_case_insensitive_and_status_is_explicit():
    cabinet = select_domain_vision_schema("请按图生成一个柜体", intent="IMAGE")
    assert cabinet.schema is not None
    assert cabinet.skill_id == "cabinet"
    assert cabinet.status == "unverified"
    assert "parts.doors.count" in cabinet.schema.fields
    assert "materials.body" in cabinet.schema.fields
    assert "FOR" not in cabinet.schema.extract_prompt

    unsupported = select_domain_vision_schema("请按图做一座桥", intent="CREATE")
    assert unsupported.schema is None
    assert unsupported.status == "unsupported"

    ambiguous = select_domain_vision_schema("柜体漏窗", intent="CREATE")
    assert ambiguous.schema is None
    assert ambiguous.status == "ambiguous"


def test_domain_schema_extracts_typed_fields_evidence_and_unknowns_without_commands():
    output = {
        "fields": {
            "overall.width": 1.2,
            "overall.height": 2.1,
            "parts.doors.count": 2,
            "parts.shelves.count": None,
            "materials.body": "浅色木纹",
        },
        "confidence": {
            "overall.width": "high",
            "overall.height": "high",
            "parts.doors.count": "high",
            "parts.shelves.count": "low",
            "materials.body": "low",
        },
        "evidence": {
            "parts.doors.count": "正立面可见两扇门板",
            "materials.body": "表面可见木纹，但木种不可确认",
        },
        "raw_description": "双门柜体，内部层板被遮挡",
    }

    class FakeVisionLLM:
        calls = 0
        prompt = ""

        def generate_with_image(self, text_prompt, image_b64, image_mime="image/png", **kwargs):
            self.calls += 1
            self.prompt = text_prompt
            return LLMResponse(
                content=json.dumps(output, ensure_ascii=False),
                model="mock-vision", usage={}, finish_reason="stop",
            )

    llm = FakeVisionLLM()
    plan = run(
        [ImageRef(token="图1", b64="YQ==", mime="image/png")],
        "IMAGE",
        "请参考图片创建一个柜体",
        llm,
        critic_pass=True,
    )[0]

    assert llm.calls == 1  # Domain Skill declares no critic executor/prompt.
    assert plan is not None
    assert plan.domain_skill_id == "cabinet"
    assert plan.domain_skill_status == "unverified"
    assert plan.domain_skill_version == "0.1.0"
    assert plan.fields["overall.width"] == 1.2
    assert plan.evidence["parts.doors.count"] == "正立面可见两扇门板"
    assert "gdl_strategy" not in plan.fields
    assert "只记录原图能支持" in llm.prompt
    assert "遮挡" in llm.prompt

    observation = observation_from_modeling_plan(plan)
    by_path = {item.field_path: item for item in observation.items}
    assert by_path["overall.width"].unit == "m"
    assert by_path["parts.doors.count"].value == 2
    assert by_path["parts.doors.count"].note == "正立面可见两扇门板"
    assert by_path["parts.shelves.count"].status == "unknown"
    assert by_path["parts.shelves.count"].note == "提取值为 null"
    assert by_path["materials.body"].status == "observed"
