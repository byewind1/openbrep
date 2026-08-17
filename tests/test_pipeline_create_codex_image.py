"""tests/test_pipeline_create_codex_image — D5 Codex 图片 CREATE 与 Vision Harness。

契约（D5 派单 + 交付协议 v2 红队自查，全部实测）：
1. 授权边界：app-server localImage/图片输入只收到「当前请求已授权图片」——
   授权字节物化进 turn 临时 cwd（不透明文件名 image-<sha16>.<ext>），
   用户提供的任何路径/canary 零到达（real-subprocess 输入日志反证）。
2. 路径对抗：`../../`、symlink 越界、不存在、非图片字节、超大——全部 fail
   closed，错误输出不含路径内秘密（物化器内容校验 + cwd 包含性断言）。
3. 确认门禁：未确认/取消/过期 epoch 文件系统零变化；确认后未编辑 extraction
   的 hints 与缓存 byte-identical（sha256 对比）。
4. 生命周期：取消与 app-server crash 路径临时图片全部清理（临时目录扫描）。
5. 非 Codex provider 与 benchmark 非交互路径 prompt 逐字节不变（调用形状锁定）。
6. MODIFY/旧单图 image_b64 对 codex 保持 fail closed（D10/D11 门禁前）。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from openbrep.codex.app_server import CodexAppServerClient, StdioJsonRpcTransport
from openbrep.codex.provider import CodexProvider
from openbrep.codex.turn import CodexTurnRunner
from openbrep.compiler import CompileResult
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import ScriptType
from openbrep.llm import LLMAdapter, LLMResponse
from openbrep.runtime.pipeline import ImageRef, TaskPipeline, TaskRequest
from openbrep.semantic_verifier import SemanticVerificationResult
from openbrep.vision.modeling_plan import ModelingPlan

# ── helpers ────────────────────────────────────────────────

def _png_b64(color=(200, 30, 30), size=(2, 2)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _jpg_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (30, 30, 200)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _webp_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (200, 200, 30)).save(buf, format="WEBP")
    return base64.b64encode(buf.getvalue()).decode()


def _sha256_b64(image_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(image_b64)).hexdigest()


class _CodexTurnResult:
    def __init__(self, content, finish_reason="stop", error=None):
        self.content = content
        self.finish_reason = finish_reason
        self.error = error
        self.usage = {}


GENERIC_JSON = json.dumps(
    {
        "component_type": "书架",
        "main_form": "rectangular_shelf",
        "layers": [
            {"name": "base", "command": "BLOCK", "description": "底座", "parametric": True},
            {"name": "slot_body", "command": "PRISM_", "description": "柜体", "parametric": True},
        ],
        "symmetry": ["x", "y"],
        "key_features": ["层板", "侧板"],
        "dimension_hints": {"width": "约 0.6m", "height": "约 0.8m"},
        "parametrize": ["width", "depth", "height", "shelf_count"],
        "fix_as_ratio": ["shelf_thickness = height * 0.04"],
        "raw_description": "双层书架",
    },
    ensure_ascii=False,
)

PLANNER_JSON = json.dumps(
    {
        "object_type": "参数化构件",
        "validation_checks": ["检查 2D 脚本是否可见", "检查 3D 脚本是否以 END 结束"],
    },
    ensure_ascii=False,
)

FULL_GDL = (
    "[FILE: paramlist.xml]\n"
    "Length A = 0.60 ! Shelf width\n"
    "Length B = 0.40 ! Shelf depth\n"
    "Length ZZYZX = 0.80 ! Total height\n"
    "\n"
    "[FILE: scripts/1d.gdl]\n"
    "! Master script placeholder\n"
    "\n"
    "[FILE: scripts/2d.gdl]\n"
    "PROJECT2 3, 270, 2\n"
    "HOTSPOT2 0, 0\n"
    "HOTSPOT2 A, 0\n"
    "HOTSPOT2 0, B\n"
    "HOTSPOT2 A, B\n"
    "\n"
    "[FILE: scripts/3d.gdl]\n"
    "BLOCK A, B, ZZYZX\n"
    "END\n"
)

_EXTRACT_PREFIX = "你是建筑构件视觉结构分析器"
_PLANNER_PREFIX = "You are an expert Archicad GDL object architect"


class _CodexImageProvider:
    """按 system 前缀分派（提取/planner/生成），记录每次 chat 的 images kwargs。

    模拟 CodexProvider.chat 的对外契约：messages + model + images（未物化的
    授权 b64 列表——物化发生在真实 provider.chat 内部）。
    """

    def __init__(self, gen_texts=None):
        self.gen_texts = list(gen_texts or [])
        self.calls = []

    def chat(self, messages, model, **kwargs):
        self.calls.append({"messages": messages, "model": model, "kwargs": kwargs})
        system = ""
        if messages and isinstance(messages[0], dict):
            system = str(messages[0].get("content") or "")
        if _EXTRACT_PREFIX in system:
            return _CodexTurnResult(GENERIC_JSON)
        if system.startswith(_PLANNER_PREFIX):
            return _CodexTurnResult(PLANNER_JSON)
        if self.gen_texts:
            return _CodexTurnResult(self.gen_texts.pop(0))
        return _CodexTurnResult("", finish_reason="no_final_message", error="未返回最终回复")

    @property
    def extraction_calls(self):
        return [
            c for c in self.calls
            if _EXTRACT_PREFIX in str(c["messages"][0].get("content") or "")
        ]

    @property
    def generation_calls(self):
        return [
            c for c in self.calls
            if _EXTRACT_PREFIX not in str(c["messages"][0].get("content") or "")
            and not str(c["messages"][0].get("content") or "").startswith(_PLANNER_PREFIX)
        ]


def _sem_pass() -> SemanticVerificationResult:
    return SemanticVerificationResult(passed=True, issues=[])


def _ok_compiler() -> MagicMock:
    compiler = MagicMock()
    compiler.hsf2libpart.return_value = CompileResult(
        success=True, stdout="", stderr="", mode="lp",
        output_path="/tmp/x.gsm", exit_code=0,
    )
    return compiler


def _codex_pipeline(tmp_path, provider, *, compiler=None) -> TaskPipeline:
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    config.llm.providers = [
        {"name": "openai-codex", "api_mode": "codex_app_server", "api_key": "", "models": []}
    ]
    pipeline = TaskPipeline(
        config=config,
        trace_dir=str(tmp_path / "traces"),
        codex_provider=provider,
    )
    if compiler is not None:
        config.compiler.path = "/fake/LP_XMLConverter"
        pipeline._make_compiler = lambda: compiler
    return pipeline


def _make_request(
    tmp_path, *, images=None, confirm_extraction=False, confirmed_extractions=None, **kwargs
):
    return TaskRequest(
        user_input=kwargs.pop("user_input", "生成一个可参数化的构件"),
        intent=kwargs.pop("intent", "CREATE"),
        work_dir=str(tmp_path / "work"),
        output_dir=str(tmp_path / "out"),
        images=images or [],
        confirm_extraction=confirm_extraction,
        confirmed_extractions=confirmed_extractions,
        **kwargs,
    )


def _run(pipeline, request, tmp_path):
    with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
        return pipeline.execute(request)


def _temp_turn_dirs() -> set[str]:
    """系统临时根下现存的 openbrep-codex-turn-* 目录集合（生命周期扫描基线）。"""
    root = Path(tempfile.gettempdir()).resolve()
    if not root.is_dir():
        return set()
    return {p.name for p in root.glob("openbrep-codex-turn-*")}


# ── 1. 快速单元：物化器内容校验 + turn 参数包含性断言 ──────────────────

class TestMaterializeAuthorizedImages:
    def test_valid_png_jpeg_webp_materialized_opaque(self, tmp_path):
        from openbrep.codex.provider import _materialize_authorized_images

        cwd = Path(tmp_path) / "turn-cwd"
        cwd.mkdir()
        entries = _materialize_authorized_images(cwd, [
            {"b64": _png_b64(), "mime": "image/png"},
            {"b64": _jpg_b64(), "mime": "image/jpeg"},
            {"b64": _webp_b64(), "mime": "image/webp"},
        ])
        assert len(entries) == 3
        for entry in entries:
            p = Path(entry["path"])
            assert p.is_absolute()
            assert os.path.commonpath([str(cwd.resolve()), str(p.resolve())]) == str(cwd.resolve())
            assert p.parent == cwd.resolve()
            assert p.name.startswith("image-") and p.name[len("image-"):len("image-")+16]
            assert p.is_file() and not p.is_symlink()
            assert entry["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
            assert entry["mime"] in ("image/png", "image/jpeg", "image/webp")

    def test_garbage_bytes_all_invalid_raises_stable(self, tmp_path):
        from openbrep.codex.provider import _materialize_authorized_images

        cwd = Path(tmp_path) / "cwd"
        cwd.mkdir()
        with pytest.raises(Exception) as ei:
            _materialize_authorized_images(
                cwd, [{"b64": base64.b64encode(b"hello").decode(), "mime": "image/png"}]
            )
        assert "图片数据无法识别" in str(ei.value)
        assert list(cwd.iterdir()) == []  # 非图片字节零落盘

    def test_mixed_valid_and_garbage_keeps_only_valid(self, tmp_path):
        from openbrep.codex.provider import _materialize_authorized_images

        cwd = Path(tmp_path) / "cwd"
        cwd.mkdir()
        entries = _materialize_authorized_images(cwd, [
            {"b64": _png_b64(), "mime": "image/png"},
            {"b64": base64.b64encode(b"not an image").decode(), "mime": "image/png"},
        ])
        assert len(entries) == 1
        assert entries[0]["index"] == 1

    def test_oversize_rejected(self, tmp_path):
        from openbrep.codex.provider import _materialize_authorized_images

        cwd = Path(tmp_path) / "cwd"
        cwd.mkdir()
        # 合法魔数 + 超上限字节
        big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * (21 * 1024 * 1024)).decode()
        with pytest.raises(Exception) as ei:
            _materialize_authorized_images(cwd, [{"b64": big, "mime": "image/png"}])
        assert "图片数据无法识别" in str(ei.value)

    def test_empty_images_noop(self, tmp_path):
        from openbrep.codex.provider import _materialize_authorized_images

        cwd = Path(tmp_path) / "cwd"
        cwd.mkdir()
        assert _materialize_authorized_images(cwd, []) == []


class TestTurnStartParamsContainment:
    def test_in_cwd_path_emits_localImage(self, tmp_path):
        cwd = Path(tmp_path) / "cwd"
        cwd.mkdir()
        img = cwd / "image-abc.png"
        img.write_bytes(b"x")
        params = CodexTurnRunner.build_turn_start_params(
            thread_id="t1", model="m", cwd=str(cwd), user_text="hi",
            images=[{"path": str(img)}],
        )
        assert params["input"][0]["type"] == "text"
        assert params["input"][1] == {"type": "localImage", "path": str(img.resolve())}

    def test_escaping_path_rejected(self, tmp_path):
        cwd = Path(tmp_path) / "cwd"
        cwd.mkdir()
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"x")
        with pytest.raises(Exception) as ei:
            CodexTurnRunner.build_turn_start_params(
                thread_id="t1", model="m", cwd=str(cwd), user_text="hi",
                images=[{"path": str(outside)}],
            )
        assert "图片路径校验失败" in str(ei.value)


# ── 2. 快速单元：llm 分派（codex 图片意图 / MODIFY / 旧单图 fail closed）──

class TestLlmCodexImageDispatch:
    def _adapter(self, provider):
        config = GDLAgentConfig()
        config.llm.model = "openai-codex/gpt-5.6-luna"
        config.llm.providers = [
            {"name": "openai-codex", "api_mode": "codex_app_server", "api_key": "", "models": []}
        ]
        adapter = LLMAdapter(config.llm)
        adapter.codex_provider = provider
        return adapter

    def test_codex_image_without_intent_fails_closed(self):
        provider = _CodexImageProvider()
        adapter = self._adapter(provider)
        with pytest.raises(RuntimeError) as ei:
            adapter.generate_with_images(
                "生成书架",
                [{"b64": _png_b64(), "mime": "image/png"}],
                model="openai-codex/gpt-5.6-luna",
            )
        assert "图片通道拒绝" in str(ei.value) or "提取确认" in str(ei.value)
        assert provider.calls == []

    def test_codex_image_create_dispatches_turn_with_images(self):
        provider = _CodexImageProvider(gen_texts=[FULL_GDL])
        adapter = self._adapter(provider)
        resp = adapter.generate_with_images(
            "按参考图生成书架",
            [{"b64": _png_b64(), "mime": "image/png"}, {"b64": _jpg_b64(), "mime": "image/jpeg"}],
            system_prompt="你是 GDL 生成器",
            model="openai-codex/gpt-5.6-luna",
            codex_intent="CREATE",
        )
        assert resp.content == FULL_GDL
        assert len(provider.calls) == 1
        images_kwarg = provider.calls[0]["kwargs"].get("images")
        assert images_kwarg is not None
        assert [i["b64"] for i in images_kwarg] == [_png_b64(), _jpg_b64()]

    def test_codex_modify_with_images_fails_closed(self):
        provider = _CodexImageProvider()
        adapter = self._adapter(provider)
        with pytest.raises(RuntimeError):
            adapter.generate_with_images(
                "改书架",
                [{"b64": _png_b64(), "mime": "image/png"}],
                model="openai-codex/gpt-5.6-luna",
            )
        assert provider.calls == []

    def test_codex_legacy_single_image_field_fails_closed(self):
        """旧 image_b64 字段（无确认门）对 codex 保持 fail closed。"""
        provider = _CodexImageProvider()
        adapter = self._adapter(provider)
        with pytest.raises(RuntimeError) as ei:
            adapter.generate_with_image(
                "按图生成",
                _png_b64(),
                "image/png",
                model="openai-codex/gpt-5.6-luna",
            )
        assert "图片通道拒绝" in str(ei.value) or "提取确认" in str(ei.value)
        assert provider.calls == []


# ── 3. 快速单元：非 codex 调用形状逐字节不变 ────────────────────────────

class TestNonCodexCallShapeUnchanged:
    def test_non_codex_harness_calls_carry_no_extra_kwargs(self, tmp_path):
        """非 codex：harness 提取调用不带任何额外 kwargs（逐字节不变）。

        generic 非 codex 路径走 analyze_reference_image → llm.generate（内嵌
        data-URI），调用形状与基线一致；schema/critic 走 generate_with_image，
        llm_kwargs=None 时零额外 kwargs。
        """
        from openbrep.vision.harness import run as harness_run

        seen = {"generate": [], "gwi": []}

        class RecordingLLM:
            def generate(self, messages, **kwargs):
                seen["generate"].append(dict(kwargs))
                return LLMResponse(content=GENERIC_JSON, model="m", usage={}, finish_reason="stop")

            def generate_with_image(
                self, text_prompt, image_b64, image_mime="image/jpeg", system_prompt=None, **kwargs
            ):
                seen["gwi"].append(dict(kwargs))
                return LLMResponse(content=GENERIC_JSON, model="m", usage={}, finish_reason="stop")

        images = [
            ImageRef(token="图1", b64=_png_b64(), mime="image/png", sha256=_sha256_b64(_png_b64()))
        ]
        plans = harness_run(
            images, "CREATE", "生成一个可参数化的构件", RecordingLLM(), llm_kwargs=None
        )
        assert plans[0] is not None
        # generic 非 codex：generate 调用形状 = 基线（仅 max_tokens，零新增 kwargs）
        assert seen["generate"] == [{"max_tokens": 1200}]
        # schema/critic 若被调用：llm_kwargs=None → 零额外 kwargs（无 codex_intent 等）
        for kwargs in seen["gwi"]:
            assert "codex_intent" not in kwargs and "codex_on_event" not in kwargs

    def test_non_codex_pipeline_generation_images_no_codex_kwargs(self, tmp_path):
        """非 codex：pipeline 带图 CREATE 的生成调用不带 codex kwargs。"""
        from openbrep.llm import LLMAdapter as _LA

        captured = {}

        class RecordingAdapter(_LA):
            def generate_with_images(self, text_prompt, images, system_prompt=None, **kwargs):
                captured["kwargs"] = dict(kwargs)
                return LLMResponse(content=FULL_GDL, model="m", usage={}, finish_reason="stop")

        pipeline = TaskPipeline(
            config=GDLAgentConfig(),
            trace_dir=str(tmp_path / "traces"),
        )
        pipeline._make_llm = lambda req: RecordingAdapter(pipeline.config.llm)
        pipeline._make_compiler = _ok_compiler
        request = _make_request(
            tmp_path,
            images=[ImageRef(token="图1", b64=_png_b64(), mime="image/png")],
        )
        with (
            patch("openbrep.runtime.pipeline.plan_gdl_object") as mock_plan,
            patch("openbrep.vision.harness.analyze_reference_image") as mock_analyze,
            patch(
                "openbrep.vision.modeling_plan.visual_structure_to_gdl_hint",
                return_value="hint",
            ),
            patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()),
        ):
            mock_plan.return_value = _FakeObjectPlan()
            mock_analyze.return_value = _FakeVS()
            result = pipeline.execute(request)
        assert result.success
        # 非 codex 生成调用 kwargs 不含任何 codex 专用键（逐字节不变）
        assert "codex_intent" not in captured["kwargs"]
        assert "codex_should_cancel" not in captured["kwargs"]
        assert "codex_on_event" not in captured["kwargs"]
        assert captured["kwargs"].get("max_tokens") == 4096


@dataclass
class _FakeObjectPlan:
    object_type: str = "bookshelf"
    knowledge_sources: list = field(default_factory=list)

    def to_prompt(self):
        return "## 对象规划\n书架"

    def to_user_summary(self):
        return "书架"

    def to_dict(self):
        return {"object_type": "bookshelf"}

    @property
    def validation_checks(self):
        return []


class _FakeVS:
    component_type = "书架"
    main_form = "rectangular_shelf"
    layers = []
    symmetry = []
    key_features = []
    dimension_hints = {}
    parametrize = []
    fix_as_ratio = []
    raw_description = "双层书架"


# ── 4. 快速：codex 图片 CREATE 确认门 + 生成（stub provider）────────────

class TestCodexImageConfirmationFlow:
    def test_unconfirmed_early_exit_no_hsf_no_fs_change(self, tmp_path):
        provider = _CodexImageProvider(gen_texts=[FULL_GDL])
        pipeline = _codex_pipeline(tmp_path, provider, compiler=_ok_compiler())
        out_root = tmp_path / "out"
        out_root.mkdir()
        before = sorted(p.name for p in out_root.iterdir())
        request = _make_request(
            tmp_path,
            images=[ImageRef(token="图1", b64=_png_b64(), mime="image/png")],
            confirm_extraction=True,
        )
        result = _run(pipeline, request, tmp_path)
        # 早退：等确认，不生成、不落盘
        assert result.success is True
        assert result.metadata.get("awaiting_extraction_confirmation") is True
        assert len(result.metadata.get("vision_extractions") or []) == 1
        assert result.metadata["vision_extractions"][0]["sha256"] == _sha256_b64(_png_b64())
        # 只跑了 1 次提取 turn（未进 planner/生成）
        assert len(provider.calls) == 1
        # 文件系统零变化
        assert sorted(p.name for p in out_root.iterdir()) == before
        assert not (out_root / "untitled").exists()

    def test_confirmed_generation_gets_authorized_images(self, tmp_path):
        provider = _CodexImageProvider(gen_texts=[FULL_GDL])
        pipeline = _codex_pipeline(tmp_path, provider, compiler=_ok_compiler())
        request1 = _make_request(
            tmp_path,
            images=[
                ImageRef(token="图1", b64=_png_b64(), mime="image/png"),
                ImageRef(token="图2", b64=_jpg_b64(), mime="image/jpeg"),
            ],
            confirm_extraction=True,
        )
        early = _run(pipeline, request1, tmp_path)
        assert early.metadata.get("awaiting_extraction_confirmation")
        entries = early.metadata["vision_extractions"]
        assert len(entries) == 2

        # 未编辑确认：同 body 重发 + confirmed_extractions
        request2 = _make_request(
            tmp_path,
            images=[
                ImageRef(token="图1", b64=_png_b64(), mime="image/png"),
                ImageRef(token="图2", b64=_jpg_b64(), mime="image/jpeg"),
            ],
            confirm_extraction=True,
            confirmed_extractions=entries,
        )
        result = _run(pipeline, request2, tmp_path)
        assert result.success is True, result.verification
        assert result.project is not None
        assert "BLOCK A, B, ZZYZX" in result.project.get_script(ScriptType.SCRIPT_3D)

        # 生成 turn 收到两张授权图（b64 逐字节一致）
        gen_calls = provider.generation_calls
        assert gen_calls, "必须有生成 turn"
        images_kwarg = gen_calls[-1]["kwargs"].get("images") or []
        assert [i["b64"] for i in images_kwarg] == [_png_b64(), _jpg_b64()]
        # 提取 turn 每图一次，也都带对应授权图
        ext_calls = provider.extraction_calls
        assert len(ext_calls) == 2
        assert [i["b64"] for i in ext_calls[0]["kwargs"]["images"]] == [_png_b64()]
        assert [i["b64"] for i in ext_calls[1]["kwargs"]["images"]] == [_jpg_b64()]

    def test_edited_extraction_flows_into_hint(self, tmp_path):
        """编辑 fields 后确认：hint 反映编辑值（可编辑语义保持）。"""
        provider = _CodexImageProvider(gen_texts=[FULL_GDL])
        pipeline = _codex_pipeline(tmp_path, provider, compiler=_ok_compiler())
        request1 = _make_request(
            tmp_path,
            images=[ImageRef(token="图1", b64=_png_b64(), mime="image/png")],
            confirm_extraction=True,
        )
        early = _run(pipeline, request1, tmp_path)
        entry = early.metadata["vision_extractions"][0]
        edited = [dict(entry, fields=dict(entry["fields"], parametrize=["width", "height"]))]
        request2 = _make_request(
            tmp_path,
            images=[ImageRef(token="图1", b64=_png_b64(), mime="image/png")],
            confirm_extraction=True,
            confirmed_extractions=edited,
        )
        result = _run(pipeline, request2, tmp_path)
        assert result.success
        gen_text = provider.generation_calls[-1]["messages"]
        # 生成 turn 的 user 文本包含编辑后的参数（from_dict → to_hint 生效）
        user_text = " ".join(
            str(m.get("content") or "") for m in gen_text if m.get("role") != "system"
        )
        assert "width" in user_text


# ── 5. 快速：materializer 与 provider.chat 清理（stub client，无子进程）──

class _StubCodexClient:
    """最小 app-server client 替身：只满足 status() 查询，记录 RPC 调用。"""

    def __init__(self):
        self.calls = []

    def start(self):
        pass

    def account_read(self):
        return {"account": {"type": "chatgpt", "email": "jo@example.com", "planType": "pro"}}

    def account_rate_limits_read(self):
        return {"rateLimits": {}}

    def thread_start(self, params):
        self.calls.append(("thread/start", params))
        return {"thread": {"id": "t1"}}

    def turn_start(self, params):
        self.calls.append(("turn/start", params))
        return {"turn": {"id": "tr1"}}

    def turn_interrupt(self, params):
        self.calls.append(("turn/interrupt", params))
        return {}

    def thread_delete(self, params):
        self.calls.append(("thread/delete", params))
        return {}


class TestProviderChatLifecycle:
    def _provider(self, client):
        return CodexProvider(
            codex_home=Path(tempfile.mkdtemp(prefix="codex-home-")),
            client_factory=lambda: client,
            cli_available=True,
            browser_opener=lambda url: None,
        )

    def test_invalid_bytes_never_starts_turn_and_cleans_temp(self):
        client = _StubCodexClient()
        provider = self._provider(client)
        before = _temp_turn_dirs()
        try:
            with pytest.raises(Exception) as ei:
                provider.chat(
                    [{"role": "user", "content": "按图生成"}],
                    model="gpt-5.6-luna",
                    images=[{"b64": base64.b64encode(b"garbage").decode(), "mime": "image/png"}],
                )
            assert "图片数据无法识别" in str(ei.value)
            # 非图片字节：thread/turn 一个都没发起
            assert [c[0] for c in client.calls] == []
        finally:
            provider.close()
        assert _temp_turn_dirs() == before

    def test_valid_image_turn_forwarded_localImage_within_cwd(self):
        client = _StubCodexClient()
        provider = self._provider(client)
        try:
            # turn 层：stub client 没有通知流，run 会超时——我们只验证参数面
            import openbrep.codex.turn as turn_mod
            orig_build = turn_mod.CodexTurnRunner.build_turn_start_params
            seen = {}

            def spy(**kwargs):
                seen["params"] = orig_build(**kwargs)
                return seen["params"]

            turn_mod.CodexTurnRunner.build_turn_start_params = staticmethod(spy)
            try:
                provider.chat(
                    [{"role": "user", "content": "hi"}],
                    model="gpt-5.6-luna",
                    timeout=2.0,  # stub 无通知流 → 快速超时，只验证参数面
                    images=[{"b64": _png_b64(), "mime": "image/png"}],
                )
            except Exception:
                pass  # stub 无通知流 → turn 会超时/失败；参数面才是断言点
            finally:
                turn_mod.CodexTurnRunner.build_turn_start_params = staticmethod(orig_build)
            assert "params" in seen
            local_images = [i for i in seen["params"]["input"] if i.get("type") == "localImage"]
            assert len(local_images) == 1
            # 物化路径在临时 cwd 内、不透明文件名
            cwd = Path(seen["params"]["cwd"])
            img_path = Path(local_images[0]["path"])
            assert (
                os.path.commonpath([str(cwd.resolve()), str(img_path.resolve())])
                == str(cwd.resolve())
            )
            assert img_path.name.startswith("image-") and img_path.suffix == ".png"
        finally:
            provider.close()


# ── 6. real-subprocess：授权边界 / 临时图片生命周期 ──────────────────────

def _provider_with_fake_server(tmp_path):
    """真实子进程 fake app-server 支撑的 CodexProvider（含登录态）。"""
    import sys

    fake_server = str(Path(__file__).parent / "fake_codex_app_server.py")
    home = tmp_path / "codex-home"

    def factory():
        transport = StdioJsonRpcTransport(
            codex_binary=sys.executable,
            codex_home=home,
            extra_args=(fake_server,),
            rpc_timeout=10.0,
        )
        return CodexAppServerClient(transport=transport)

    return CodexProvider(
        codex_home=home,
        client_factory=factory,
        cli_available=True,
        browser_opener=lambda url: None,
    )


class TestRealSubprocessAuthorizationBoundary:
    def test_full_flow_app_server_sees_only_authorized_images(self, tmp_path):
        """端到端（真实 fake app-server 子进程）：提取 → 确认 → 生成。

        红队反证（fake server 输入日志独立校验）：
        - 每个 localImage.path 都在该 thread 的 cwd 内（inside_cwd=True）、
          文件存在、非 symlink、sha256 == 授权图 sha256；
        - 输入文本/路径零 canary、零 `../` 段、零用户路径；
        - 临时 cwd 目录（含图片文件）用完即删。
        """
        from openbrep.codex.provider import set_default_codex_provider

        out_root = tmp_path / "out"
        out_root.mkdir()
        input_log = tmp_path / "input-log.jsonl"
        env = {
            "FAKE_CODEX_TURN": "1",
            "FAKE_CODEX_SIGNED_IN": "1",
            "FAKE_CODEX_TURN_FINAL_TEXTS": str(tmp_path / "texts.jsonl"),
            "FAKE_CODEX_TURN_INPUT_LOG": str(input_log),
            "FAKE_CODEX_REJECT_ESCAPING_IMAGE": "1",
        }
        (tmp_path / "texts.jsonl").write_text(
            "\n".join(json.dumps(x) for x in [GENERIC_JSON, PLANNER_JSON, FULL_GDL]) + "\n",
            encoding="utf-8",
        )
        saved = {k: v for k, v in os.environ.items() if k.startswith("FAKE_CODEX_")}
        os.environ.update(env)
        provider = _provider_with_fake_server(tmp_path)
        try:
            set_default_codex_provider(provider)
            pipeline = _codex_pipeline(tmp_path, provider, compiler=_ok_compiler())

            # 第一程：提取 → 早退等确认
            request1 = _make_request(
                tmp_path,
                images=[ImageRef(token="图1", b64=_png_b64(), mime="image/png")],
                confirm_extraction=True,
            )
            early = _run(pipeline, request1, tmp_path)
            assert early.metadata.get("awaiting_extraction_confirmation")
            entries = early.metadata["vision_extractions"]
            assert len(entries) == 1
            entry = entries[0]
            authorized_sha = entry["sha256"]
            assert authorized_sha == _sha256_b64(_png_b64())

            # 未编辑确认：hint 与缓存 byte-identical
            hint1 = ModelingPlan.from_dict(entry).to_hint()
            request2 = _make_request(
                tmp_path,
                images=[ImageRef(token="图1", b64=_png_b64(), mime="image/png")],
                confirm_extraction=True,
                confirmed_extractions=entries,
            )
            result = _run(pipeline, request2, tmp_path)
            assert result.success is True, result.verification
            assert result.project is not None
            assert "BLOCK A, B, ZZYZX" in result.project.get_script(ScriptType.SCRIPT_3D)

            # ── fake server 输入日志逐条校验 ──
            lines = [ln for ln in input_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
            assert lines, "input log 必须有记录"
            entries_log = [json.loads(ln) for ln in lines]
            # 3 个 turn：提取 / planner / 生成
            assert len(entries_log) == 3
            local_images = []
            gen_text = ""
            for e in entries_log:
                for item in e["input"]:
                    if item.get("type") == "localImage":
                        local_images.append(item)
                    elif item.get("type") == "text":
                        if "参考图建模计划" in item.get("text", ""):
                            gen_text = item["text"]
            # 提取 + 生成两个 turn 带图（planner 无图）
            assert len(local_images) == 2
            for li in local_images:
                assert li["inside_cwd"] is True, f"localImage 必须落在 thread cwd 内: {li}"
                assert li["exists"] is True and li["is_file"] is True
                assert li["symlink"] is False
                assert li["sha256"] == authorized_sha, "app-server 只收授权图字节"
                # 不透明文件名 image-<16 hex>.png
                name = Path(li["path"]).name
                assert name.startswith("image-") and name[len("image-"):len("image-")+16].isalnum()
                # 路径不含用户可注入段
                assert ".." not in li["path"].split("/")
            # 输入文本零 canary / 零用户路径
            log_text = input_log.read_text(encoding="utf-8")
            for token in ("/etc/", "canary", "secret", "../"):
                assert token not in log_text, f"输入日志不得包含 {token}: {log_text[:800]}"
            # 未编辑 hints byte-identical：生成 turn 文本内含 hint1 全量，
            # 且嵌入副本与原始 hint 的 sha256 逐字节一致（缓存对比）
            assert gen_text
            hint_idx = gen_text.find(hint1)
            assert hint_idx != -1, "生成 turn 必须携带未编辑的完整 hint"
            embedded = gen_text[hint_idx : hint_idx + len(hint1)]
            assert embedded == hint1
            assert (
                hashlib.sha256(hint1.encode("utf-8")).hexdigest()
                == hashlib.sha256(embedded.encode("utf-8")).hexdigest()
            )
        finally:
            try:
                provider.close()
            finally:
                for k in list(os.environ):
                    if k.startswith("FAKE_CODEX_"):
                        os.environ.pop(k, None)
                os.environ.update(saved)
                set_default_codex_provider(None)
        # 临时 cwd（含图片文件）全部清理
        assert _temp_turn_dirs() == set(), "取消/完成路径临时目录必须清理"

    def test_path_injection_canary_never_reaches_app_server(self, tmp_path):
        """授权边界反证：伪造请求注入未授权路径——app-server 只收到授权图字节。

        - 原始 localImage 键注入（wire 形状）→ 请求门禁拒绝（missing both b64
          and path，fail closed）；
        - 已存在路径条目（绝对 canary 路径 / `../../` 相对越界）→ 后端读取为
          字节、以不透明文件名物化——canary 路径字符串零到达 app-server；
        - fake server 独立校验：每个 localImage 都在 thread cwd 内、sha256 匹配
          请求声明图的字节、无 symlink、无 `..` 段。
        """
        from openbrep.codex.provider import set_default_codex_provider
        from openbrep.workbench_api import WorkbenchSession

        # ── (a) 原始 localImage 键注入 → 请求门禁 fail closed ──
        session = WorkbenchSession(pipeline_class=TaskPipeline)
        gate_resp = session.route(
            "POST",
            "/api/project/create",
            {
                "prompt": "按图生成",
                "output_dir": str(tmp_path / "out-gate"),
                "images": [
                    {"b64": _png_b64(), "mime": "image/png"},
                    {"localImage": {"path": "/tmp/CANARY-SECRET-IMG.png"}},
                ],
            },
        )
        assert gate_resp.get("ok") is False
        assert "missing both b64 and path" in gate_resp.get("error", "")

        # ── (b) 已存在路径条目：canary 路径/`../../` 只作为字节来源，不得到达 ──
        secret_dir = tmp_path / "secret-dir"
        secret_dir.mkdir()
        secret_img = secret_dir / "CANARY-SECRET-IMG.png"
        secret_img.write_bytes(base64.b64decode(_png_b64()))
        # `../../` 相对越界：从 upload 子目录指向 secret 文件
        upload_dir = tmp_path / "upload"
        upload_dir.mkdir()
        # 构造一个「相对路径越过 upload 目录」的引用（与工作区外同一文件）
        # 形如 ../secret-dir/CANARY-SECRET-IMG.png（相对路径越过 upload 目录）
        rel_path = os.path.relpath(secret_img, upload_dir)

        input_log = tmp_path / "input-log.jsonl"
        env = {
            "FAKE_CODEX_TURN": "1",
            "FAKE_CODEX_SIGNED_IN": "1",
            "FAKE_CODEX_TURN_FINAL_TEXTS": str(tmp_path / "texts.jsonl"),
            "FAKE_CODEX_TURN_INPUT_LOG": str(input_log),
            "FAKE_CODEX_REJECT_ESCAPING_IMAGE": "1",
        }
        # 2 张图 → 2 个提取 turn（都在确认早退前）
        (tmp_path / "texts.jsonl").write_text(
            "\n".join(json.dumps(x) for x in [GENERIC_JSON, GENERIC_JSON]) + "\n",
            encoding="utf-8",
        )
        saved = {k: v for k, v in os.environ.items() if k.startswith("FAKE_CODEX_")}
        os.environ.update(env)
        provider = _provider_with_fake_server(tmp_path)
        try:
            set_default_codex_provider(provider)
            pipeline = _codex_pipeline(tmp_path, provider)
            request1 = _make_request(
                tmp_path,
                images=[
                    ImageRef(token="图1", b64=_png_b64(), mime="image/png"),  # 授权图 A
                    ImageRef(token="图2", b64="", path=str(secret_img)),  # 绝对 canary 路径
                    # ../ 相对越界（同一 canary 文件）
                    ImageRef(token="图3", b64="", path=str(Path(upload_dir) / rel_path)),
                ],
                confirm_extraction=True,
            )
            early = _run(pipeline, request1, tmp_path)
            assert early.metadata.get("awaiting_extraction_confirmation") is True
            entries = early.metadata.get("vision_extractions") or []
            non_skipped = [e for e in entries if not e.get("skipped")]
            # 三张图都声明为请求图片（后端读取为字节）→ 三个提取、三份 sha256
            assert len(non_skipped) == 3
            for e in non_skipped:
                assert e["sha256"] == _sha256_b64(_png_b64())
        finally:
            try:
                provider.close()
            finally:
                for k in list(os.environ):
                    if k.startswith("FAKE_CODEX_"):
                        os.environ.pop(k, None)
                os.environ.update(saved)
                set_default_codex_provider(None)
        # app-server 输入日志：canary 路径字符串/`../`/绝对路径零到达
        log_text = input_log.read_text(encoding="utf-8")
        for token in ("CANARY-SECRET-IMG", "secret-dir", "upload", "..", "/etc/"):
            assert token not in log_text, f"未授权路径不得到达 app-server: {log_text[:1200]}"
        lines = [ln for ln in log_text.splitlines() if ln.strip()]
        local_images = [
            item for e in (json.loads(ln) for ln in lines)
            for item in e.get("input", []) if item.get("type") == "localImage"
        ]
        assert len(local_images) == 3  # 三张授权图各一次
        for li in local_images:
            assert li["inside_cwd"] is True, li
            assert li["exists"] and li["is_file"] and not li["symlink"]
            assert li["sha256"] == _sha256_b64(_png_b64())
            assert ".." not in li["path"].split("/")

    def test_cancel_cleans_temp_images(self, tmp_path):
        """取消路径：turn 挂起时用户取消 → 临时 cwd（含图片）全部清理。"""
        from openbrep.codex.provider import set_default_codex_provider

        env = {
            "FAKE_CODEX_TURN": "1",
            "FAKE_CODEX_SIGNED_IN": "1",
            "FAKE_CODEX_TURN_HANG": "1",
        }
        saved = {k: v for k, v in os.environ.items() if k.startswith("FAKE_CODEX_")}
        os.environ.update(env)
        provider = _provider_with_fake_server(tmp_path)
        before = _temp_turn_dirs()
        try:
            set_default_codex_provider(provider)
            pipeline = _codex_pipeline(tmp_path, provider)
            request = _make_request(
                tmp_path,
                images=[ImageRef(token="图1", b64=_png_b64(), mime="image/png")],
                confirm_extraction=True,
                should_cancel=lambda: True,
            )
            result = _run(pipeline, request, tmp_path)
            # 取消 → 提取 turn 被中断 → harness 降级（未知构件 + 稳定文案）→ 早退
            assert result.success is True
            assert result.metadata.get("awaiting_extraction_confirmation") is True
            entries = result.metadata.get("vision_extractions") or []
            assert entries
            vs = entries[0]["fields"]["visual_structure"]
            assert vs["component_type"] == "未知构件"
            assert "图像分析失败" in entries[0]["raw_description"]
            # 降级文案只含稳定文本（零上游原文/canary）
            assert "canary" not in json.dumps(entries[0], ensure_ascii=False)
        finally:
            try:
                provider.close()
            finally:
                for k in list(os.environ):
                    if k.startswith("FAKE_CODEX_"):
                        os.environ.pop(k, None)
                os.environ.update(saved)
                set_default_codex_provider(None)
        # 取消后临时目录（含图片文件）零残留
        assert _temp_turn_dirs() == before

    def test_crash_cleans_temp_images(self, tmp_path):
        """crash 路径：app-server 在 turn 中途崩溃 → 临时 cwd（含图片）全部清理。"""
        from openbrep.codex.provider import set_default_codex_provider

        env = {
            "FAKE_CODEX_TURN": "1",
            "FAKE_CODEX_SIGNED_IN": "1",
            "FAKE_CODEX_CRASH_AFTER_REQUESTS": "3",  # initialize + thread/start + turn/start
        }
        saved = {k: v for k, v in os.environ.items() if k.startswith("FAKE_CODEX_")}
        os.environ.update(env)
        provider = _provider_with_fake_server(tmp_path)
        before = _temp_turn_dirs()
        try:
            set_default_codex_provider(provider)
            pipeline = _codex_pipeline(tmp_path, provider)
            request = _make_request(
                tmp_path,
                images=[ImageRef(token="图1", b64=_png_b64(), mime="image/png")],
                confirm_extraction=True,
            )
            result = _run(pipeline, request, tmp_path)
            # crash → turn 失败 → harness 降级 → 早退（零 HSF 落盘）
            assert result.metadata.get("awaiting_extraction_confirmation") is True
            entries = result.metadata.get("vision_extractions") or []
            assert entries
            vs = entries[0]["fields"]["visual_structure"]
            assert vs["component_type"] == "未知构件"
            assert "图像分析失败" in entries[0]["raw_description"]
        finally:
            try:
                provider.close()
            finally:
                for k in list(os.environ):
                    if k.startswith("FAKE_CODEX_"):
                        os.environ.pop(k, None)
                os.environ.update(saved)
                set_default_codex_provider(None)
        # crash 后临时目录（含图片文件）零残留
        assert _temp_turn_dirs() == before


# ── 7. workbench 级：epoch 过期拒绝（未确认/取消/过期零 HSF）────────────

class TestWorkbenchEpochGate:
    def test_epoch_expired_confirmation_rejected_no_hsf(self, tmp_path):
        from openbrep.codex.provider import set_default_codex_provider
        from openbrep.workbench_api import WorkbenchSession

        provider = _CodexImageProvider(gen_texts=[FULL_GDL])
        set_default_codex_provider(provider)
        out_root = tmp_path / "out"
        out_root.mkdir()
        session = WorkbenchSession(pipeline_class=TaskPipeline)
        session.llm_model = "openai-codex/gpt-5.6-luna"
        session.max_retries = 2
        body = {
            "prompt": "生成一个可参数化的构件",
            "output_dir": str(out_root),
            "images": [{"b64": _png_b64(), "mime": "image/png"}],
            "confirm_extraction": True,
        }
        try:
            early = session.route("POST", "/api/project/create", body)
            assert early.get("awaiting_extraction_confirmation") is True
            assert session.pending_extraction is not None
            confirmed = [dict(session.pending_extraction["extractions"][0])]
            # 项目切换：epoch +1 → 确认被拒（NO_PENDING_EXTRACTION）
            session.project_epoch += 1
            response = session.route(
                "POST", "/api/project/create", {**body, "confirmed_extractions": confirmed}
            )
            assert response.get("ok") is False
            assert response.get("code") == "NO_PENDING_EXTRACTION"
            assert session.pending_extraction is None
            # 文件系统零变化：无项目目录创建
            assert sorted(p.name for p in out_root.iterdir()) == []
            assert not any(p.is_dir() and p.name != "out" for p in out_root.iterdir())
        finally:
            set_default_codex_provider(None)

    def test_workbench_confirm_delivers_and_persists_extraction_cache(self, tmp_path):
        """确认后完整交付 + 提取工件按 sha256 内容寻址落盘（缓存键不变）。"""
        from openbrep.codex.provider import set_default_codex_provider
        from openbrep.workbench_api import WorkbenchSession

        provider = _CodexImageProvider(gen_texts=[FULL_GDL])
        set_default_codex_provider(provider)
        out_root = tmp_path / "out"
        out_root.mkdir()
        session = WorkbenchSession(pipeline_class=TaskPipeline)
        session.llm_model = "openai-codex/gpt-5.6-luna"
        session.max_retries = 2
        body = {
            "prompt": "生成一个可参数化的构件",
            "output_dir": str(out_root),
            "images": [{"b64": _png_b64(), "mime": "image/png"}],
            "confirm_extraction": True,
        }
        try:
            early = session.route("POST", "/api/project/create", body)
            assert early.get("awaiting_extraction_confirmation") is True
            entry = early["extractions"][0]
            hint1 = ModelingPlan.from_dict(entry).to_hint()
            confirmed = [dict(entry)]
            with patch("openbrep.semantic_verifier.verify_semantics", return_value=_sem_pass()):
                response = session.route(
                    "POST", "/api/project/create", {**body, "confirmed_extractions": confirmed}
                )
            assert response.get("ok") is True, response
            project_path = Path(response["project"]["path"])
            assert project_path.is_dir()
            # 提取工件落盘：内容寻址（sha256[:12]），未编辑字段与提示 byte-identical
            from openbrep.vision.extraction_store import list_extraction_hashes
            hashes = list_extraction_hashes(project_path)
            assert hashes, "必须落盘提取工件"
            expected_prefix = entry["sha256"][:12]
            assert expected_prefix in hashes
            artifact_path = (
                project_path / ".openbrep" / "vision" / f"extraction-{expected_prefix}.json"
            )
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            hint_cache = ModelingPlan.from_dict(artifact).to_hint()
            assert hint_cache == hint1  # 缓存 hint 与原始提取 byte-identical
            assert artifact["sha256"] == entry["sha256"]
        finally:
            set_default_codex_provider(None)
