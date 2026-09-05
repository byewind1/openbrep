"""G3 学习 A/B 实验架测试：snapshot round-trip / 写隔离 / mock 确定性 /
record→replay / preflight / infra_excluded。

测试对应派单六项要求：
1. snapshot capture/materialize/verify round-trip（present 快照、空快照、
   legacy 路径、manifest hash 篡改检测、多源冲突）；
2. 写入隔离：treatment 副本上的（模拟）学习写入 → snapshot verify 仍通过；
   e2e 里 treatment 臂成功后 pipeline 真实追加 decisions.md 到**副本**，
   跑后 snapshot 重算仍 ok（AC-3）；
3. 离线确定性：--llm mock 双跑逐字节一致。豁免键（有文档）：run_id（随机
   后缀）、ts、generated_at、所有以 elapsed 开头的键（墙钟，含派生汇总）。
   测试先递归剔除豁免键，再对 json.dumps(sort_keys=True) 做字节级比较；
4. record→replay 闭环：迷你 suite（复用仓内 M01 fixture 只读源 + 临时语料）
   中双录→双回放零 miss（excluded == []）；
5. reuse_count preflight 拒绝（临时 skills_dir 注入带 frontmatter 的 .md）；
6. replay miss → infra_excluded 单列（reason=replay_miss + miss_keys）。

Hermetic 纪律：语料、workdir、results、snapshot、suite 全部在 pytest tmp
（tmp_path_factory 会话级共享目录，保证同一批绝对路径下回放命中）；仓内
fixture（benchmark/fixtures/modify/M01）只读，e2e 前后做整树 sha 比对证明零改动。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

import benchmark.learning_ab as lab
import benchmark.learning_snapshot as lsnap
from benchmark.llm_replay import RecordingLLM
from openbrep.llm import MockLLM

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SRC = PROJECT_ROOT / "benchmark/fixtures/modify/M01"
SOURCE_TASK_YAML = PROJECT_ROOT / "benchmark/tasks/modify/M01_add_shelf_layer.yaml"

LEARNED_SKILL_TEXT = (
    "# learned skill（冻结快照内容）\n"
    "避免把 height 语义参数塞进 A/B 宽度参数，层板厚度用 shelf_thk。\n"
)
DECISIONS_TEXT = (
    "## 2026-08-01T10:00:00 · MODIFY / r0001\n"
    "\n- Instruction: 快照测试决策记录\n- Changed files: scripts/3d.gdl\n"
)
LESSON_LINE = json.dumps(
    {
        "fingerprint": "fp-snapshot-test",
        "category": "param_semantics",
        "summary": "参数语义错误示例教训",
        "guidance": "先查参数角色再赋值",
        "example": "把高度塞进 A",
        "count": 2,
        "first_seen": "2026-08-01T10:00:00",
        "last_seen": "2026-08-01T10:00:00",
        "source": "workspace",
        "project_name": "mini",
        "raw_excerpt": "参数语义错误",
        "ignored": False,
    },
    ensure_ascii=False,
)

# mock 确定性豁免键：run_id（随机后缀）、ts/generated_at（时间戳）、
# elapsed*（墙钟秒及派生汇总）。剔除后其余字段必须逐字节一致。
_EXEMPT_EXACT = {"run_id", "ts", "generated_at"}


def _strip_dynamic(obj):
    """递归剔除确定性豁免键（见模块 docstring），返回归一化副本。"""
    if isinstance(obj, dict):
        return {
            key: _strip_dynamic(value)
            for key, value in obj.items()
            if key not in _EXEMPT_EXACT and not str(key).startswith("elapsed")
        }
    if isinstance(obj, list):
        return [_strip_dynamic(item) for item in obj]
    return obj


def _bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _tree_sha(root: Path) -> str:
    """目录整树内容 sha（相对路径排序；用于 fixture 零改动断言）。"""
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        rel = path.relative_to(root)
        digest.update(str(rel).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


# ── 迷你实验环境（会话级共享：同一批绝对路径下回放才命中）──────────────

def _mini_task_yaml(fixture_rel: str) -> dict:
    data = yaml.safe_load(SOURCE_TASK_YAML.read_text(encoding="utf-8"))
    data["id"] = "MINI01"
    data["fixture"] = fixture_rel
    return data


@pytest.fixture(scope="module")
def mini_env(tmp_path_factory):
    """一次建好迷你 suite + 双录语料 + 共享 workdir/results，供全模块复用。"""
    base = tmp_path_factory.mktemp("learning_ab")
    suite_dir = base / "mini_suite"
    suite_dir.mkdir()
    (suite_dir / "MINI01.yaml").write_text(
        yaml.safe_dump(_mini_task_yaml("benchmark/fixtures/modify/M01"),
                       allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    env = {
        "base": base,
        "suite_dir": suite_dir,
        "work_dir": base / "work",
        "results_dir": base / "results",
        "corpus_dir": base / "corpora",
    }
    env["corpus_dir"].mkdir()
    env["ctl_corpus"] = env["corpus_dir"] / "control.jsonl"
    env["trt_corpus"] = env["corpus_dir"] / "treatment.jsonl"
    env["ctl_corpus"].touch()  # 预检占位；RecordingLLM 打开即 w 截断

    record_runner = lab.LearningABRunner(
        suite_dir=str(suite_dir),
        llm_mode="mock",
        budget=2,
        control_corpus=str(env["ctl_corpus"]),
        work_dir=env["work_dir"],
        results_dir=env["results_dir"],
    )

    def _record_maker(spec):
        corpus = env["trt_corpus"] if spec.label == "treatment" else env["ctl_corpus"]
        return RecordingLLM(MockLLM(), str(corpus))

    record_runner._make_arm_llm = _record_maker
    record_runner.run_suite()
    return env


def _runner(env, **overrides) -> lab.LearningABRunner:
    params = dict(
        suite_dir=str(env["suite_dir"]),
        llm_mode="mock",
        budget=2,
        control_corpus=str(env["ctl_corpus"]),
        work_dir=env["work_dir"],
        results_dir=env["results_dir"],
        skills_dir=str(env["base"] / "empty_skills"),
    )
    params.update(overrides)
    Path(params["skills_dir"]).mkdir(parents=True, exist_ok=True)
    return lab.LearningABRunner(**params)


# ── 1. snapshot round-trip ─────────────────────────────────

def _write_learning_state(root: Path):
    memory = root / ".openbrep/memory"
    memory.joinpath("learnings").mkdir(parents=True, exist_ok=True)
    memory.joinpath("skills").mkdir(parents=True, exist_ok=True)
    (memory / "learnings/error_lessons.jsonl").write_text(LESSON_LINE + "\n", encoding="utf-8")
    (memory / "skills/learned_skill.md").write_text(LEARNED_SKILL_TEXT, encoding="utf-8")
    (memory / "decisions.md").write_text(DECISIONS_TEXT, encoding="utf-8")


def test_snapshot_roundtrip_present(tmp_path):
    src = tmp_path / "src"
    _write_learning_state(src)
    snap = tmp_path / "snap"
    lsnap.capture(src, snap)

    manifest = lsnap.load_manifest(snap)
    entries = {e["rel_path"]: e for e in manifest["entries"]}
    assert set(entries) == set(lsnap.MANAGED_REL_PATHS)
    present = (
        ".openbrep/memory/learnings/error_lessons.jsonl",
        ".openbrep/memory/skills/learned_skill.md",
        ".openbrep/memory/decisions.md",
    )
    absent = (
        ".openbrep/learnings/error_lessons.jsonl",
        ".openbrep/learnings/learned_skill.md",
    )
    for rel in present:
        assert entries[rel]["absent"] is False
        assert entries[rel]["sha256"] and entries[rel]["size"] > 0
    for rel in absent:
        assert entries[rel]["absent"] is True
    assert lsnap.verify(snap)

    target = tmp_path / "target"
    target.mkdir()
    result = lsnap.materialize(snap, target)
    assert sorted(result["written"]) == sorted(present)
    assert result["removed"] == []
    for rel in present:
        assert (target / rel).read_bytes() == (src / rel).read_bytes()
    for rel in absent:
        assert not (target / rel).exists()
    assert lsnap.verify(snap)  # materialize 不改快照


def test_snapshot_empty_all_absent_and_absent_enforcement(tmp_path):
    empty_src = tmp_path / "empty_src"
    empty_src.mkdir()
    snap = tmp_path / "snap"
    lsnap.capture(empty_src, snap)
    manifest = lsnap.load_manifest(snap)
    assert all(e["absent"] for e in manifest["entries"])
    assert lsnap.verify(snap)

    target = tmp_path / "target"
    _write_learning_state(target)  # 目标上已有 memory/ 三个受管文件
    (target / "keep.txt").write_text("unrelated", encoding="utf-8")
    result = lsnap.materialize(snap, target)
    assert result["written"] == []
    memory_rel = (
        ".openbrep/memory/learnings/error_lessons.jsonl",
        ".openbrep/memory/skills/learned_skill.md",
        ".openbrep/memory/decisions.md",
    )
    assert sorted(result["removed"]) == sorted(memory_rel)
    for rel in memory_rel:
        assert not (target / rel).exists()  # absent 条目驱逐出目标
    assert (target / "keep.txt").is_file()  # 无关文件不动


def test_snapshot_legacy_learnings_layout(tmp_path):
    legacy_src = tmp_path / "legacy_src"
    legacy = legacy_src / ".openbrep/learnings"
    legacy.mkdir(parents=True)
    (legacy / "error_lessons.jsonl").write_text(LESSON_LINE + "\n", encoding="utf-8")
    (legacy / "learned_skill.md").write_text(LEARNED_SKILL_TEXT, encoding="utf-8")
    snap = tmp_path / "snap"
    lsnap.capture(legacy_src, snap)

    manifest = lsnap.load_manifest(snap)
    by_rel = {e["rel_path"]: e for e in manifest["entries"]}
    assert by_rel[".openbrep/learnings/error_lessons.jsonl"]["absent"] is False
    assert by_rel[".openbrep/memory/learnings/error_lessons.jsonl"]["absent"] is True
    assert lsnap.verify(snap)

    target = tmp_path / "target"
    target.mkdir()
    result = lsnap.materialize(snap, target)
    assert ".openbrep/learnings/error_lessons.jsonl" in result["written"]
    assert (target / ".openbrep/learnings/error_lessons.jsonl").is_file()
    assert not (target / ".openbrep/memory/learnings/error_lessons.jsonl").exists()


def test_snapshot_tamper_detection(tmp_path):
    src = tmp_path / "src"
    _write_learning_state(src)
    snap = tmp_path / "snap"
    lsnap.capture(src, snap)

    learned = snap / ".openbrep/memory/skills/learned_skill.md"
    learned.write_text("tampered content\n", encoding="utf-8")  # sha 破坏
    truncate = snap / ".openbrep/memory/decisions.md"
    truncate.write_bytes(truncate.read_bytes()[:10])  # 字节数破坏
    ok, issues = lsnap.verify_report(snap)
    assert ok is False
    text = "；".join(issues)
    assert "sha256 不一致" in text
    assert "字节数不一致" in text
    assert lsnap.verify(snap) is False


def test_snapshot_conflicting_sources_rejected(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_learning_state(a)
    _write_learning_state(b)
    (b / ".openbrep/memory/skills/learned_skill.md").write_text(
        "不同内容\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="快照来源冲突"):
        lsnap.capture([a, b], tmp_path / "snap")


# ── 2. 写隔离 ──────────────────────────────────────────────

def test_write_isolation_snapshot_untouched_by_target_writes(tmp_path):
    src = tmp_path / "src"
    _write_learning_state(src)
    snap = tmp_path / "snap"
    lsnap.capture(src, snap)

    target = tmp_path / "target"
    target.mkdir()
    lsnap.materialize(snap, target)

    # 模拟 treatment 臂在**副本**上的学习写入（feedback 追加 / quality 落盘）
    lessons = target / ".openbrep/memory/learnings/error_lessons.jsonl"
    lessons.write_text(LESSON_LINE + "\n" + LESSON_LINE + "\n", encoding="utf-8")
    (target / ".openbrep/memory/decisions.md").write_text(
        DECISIONS_TEXT + "追加的决策\n", encoding="utf-8"
    )
    quality = target / ".openbrep/quality/runs/r_tmp_1.json"
    quality.parent.mkdir(parents=True)
    quality.write_text('{"run_id": "r_tmp_1"}', encoding="utf-8")

    assert lsnap.verify(snap) is True  # 副本怎么写都碰不到快照目录


def test_snapshot_e2e_isolation_and_learning_injection(mini_env):
    """treatment 臂挂快照跑真实 pipeline：成功后向**副本**追加决策记忆，
    快照跑后重算仍 ok（AC-3）；control 无任何学习层，treatment 有。"""
    base = mini_env["base"]
    src = base / "snap_src"
    _write_learning_state(src)
    snap = base / "snapshot"
    lsnap.capture(src, snap)

    before = _tree_sha(FIXTURE_SRC)  # fixture 原件零改动断言
    runner = _runner(mini_env, snapshot_dir=snap)
    experiment = runner.run_suite()

    assert experiment["excluded"] == []
    assert len(experiment["paired"]) == 1
    by_arm = {row["arm"]: row for row in experiment["rows"]}
    assert by_arm["control"]["infra_excluded"] is False
    assert by_arm["treatment"]["infra_excluded"] is False
    assert by_arm["control"]["learning_seen"] is False
    assert by_arm["treatment"]["learning_seen"] is True
    # 快照文件确实挂进了 treatment 副本
    mounted = (
        mini_env["work_dir"] / "MINI01__treatment/.openbrep/memory/skills/learned_skill.md"
    )
    assert mounted.read_text(encoding="utf-8") == LEARNED_SKILL_TEXT
    # 快照只出现在 treatment 副本，不污染 control 副本
    ctl_learned = mini_env["work_dir"] / "MINI01__control/.openbrep/memory/skills/learned_skill.md"
    assert not ctl_learned.exists()

    post = runner.postflight()
    assert post["snapshot_ok"] is True  # 跑后重算（写隔离硬门禁）
    assert _tree_sha(FIXTURE_SRC) == before  # fixture 原件零改动
    # AC-4：报告落盘（gitignored 输出由 CLI 负责；这里验证双文件生成）
    paths = runner.write_results(experiment)
    assert Path(paths["results_json"]).is_file()
    assert Path(paths["summary_md"]).is_file()
    text = Path(paths["summary_md"]).read_text(encoding="utf-8")
    # excluded 为空时 infra_excluded 小节不渲染（只在有内容时出现），
    # 这里断言恒在的骨架与 learning_seen 证据
    assert "## 三轴汇总" in text
    assert "## 逐题 paired delta" in text
    assert "T learning_seen" in text
    assert "| control" in text and "| treatment" in text


# ── 3. 离线确定性（mock 双跑逐字节一致，豁免键见模块 docstring）───────

def test_mock_double_run_byte_identical(mini_env):
    first = _runner(mini_env).run_suite()
    second = _runner(mini_env).run_suite()

    assert _bytes(_strip_dynamic(first)) == _bytes(_strip_dynamic(second))
    # 双跑都是完整配对：零 infra
    assert first["excluded"] == [] and second["excluded"] == []
    assert first["summary"]["paired_tasks"] == 1
    # 豁免键确实存在（证明 strip 不空转）
    raw = json.dumps(first, ensure_ascii=False)
    assert "generated_at" in raw and "run_id" in raw and "elapsed_sec" in raw
    # 两臂 prompt 真实不同（mock 模式也要证明差异存在）
    by_arm = {row["arm"]: row for row in first["rows"]}
    assert by_arm["control"]["learning_seen"] is False
    assert by_arm["treatment"]["learning_seen"] is True
    # 双臂语料 key 集不同（录制阶段已证明差异来自学习层注入）
    ctl_keys = {json.loads(line)["key"] for line in
                mini_env["ctl_corpus"].read_text(encoding="utf-8").splitlines() if line.strip()}
    trt_keys = {json.loads(line)["key"] for line in
                mini_env["trt_corpus"].read_text(encoding="utf-8").splitlines() if line.strip()}
    assert ctl_keys and trt_keys
    assert ctl_keys != trt_keys


# ── 4. record→replay 闭环（零 miss）────────────────────────

def test_record_replay_closed_loop_zero_miss(mini_env):
    runner = _runner(mini_env, treatment_replay=str(mini_env["trt_corpus"]))
    experiment = runner.run_suite()

    assert experiment["excluded"] == []
    assert len(experiment["paired"]) == 1
    for row in experiment["rows"]:
        assert row["infra_excluded"] is False
        assert row["arm"] in ("control", "treatment")


# ── 5. reuse_count preflight 拒绝 ──────────────────────────

def test_reuse_count_preflight_rejects_and_blocks_start(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "hot.md").write_text(
        "---\nname: hot-skill\nreuse_count: 7\n---\n# content\n", encoding="utf-8"
    )
    (skills / "sub").mkdir()
    (skills / "sub/cold.md").write_text("# plain\n", encoding="utf-8")
    (skills / "notes.txt").write_text("reuse_count inside txt\n", encoding="utf-8")

    listed = lab.scan_skills_for_reuse_count(skills)
    assert listed == ["hot.md"]  # 只认 .md，且命中前 2048 字节

    env = _mini_env_skeleton(tmp_path)
    runner = _runner(env, skills_dir=str(skills))
    problems = runner.preflight()
    assert any("reuse_count" in p and "hot.md" in p for p in problems)
    with pytest.raises(lab.HarnessError):
        runner.run_suite()  # preflight 失败 → 拒绝启动


def _mini_env_skeleton(tmp_path) -> dict:
    """无会话语料的最小 env 骨架（只供 preflight/拒绝路径用）。"""
    env = {
        "base": tmp_path,
        "suite_dir": tmp_path / "no_suite",
        "work_dir": tmp_path / "work",
        "results_dir": tmp_path / "results",
        "ctl_corpus": tmp_path / "corpora/control.jsonl",
    }
    env["ctl_corpus"].parent.mkdir()
    env["ctl_corpus"].touch()
    return env


def test_reuse_count_absent_passes_preflight(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "ok.md").write_text("# plain skill\n", encoding="utf-8")
    env = _mini_env_skeleton(tmp_path)
    runner = _runner(env, skills_dir=str(skills))
    assert runner.preflight() == []


# ── 6. replay miss → infra_excluded ────────────────────────

def test_control_request_identity_mirrors_runner_defaults():
    """control 镜像 runner.py 录制语义（budget 0 + plain gsm），否则黄金语料
    agent-loop 首轮 key 全 miss（budget 文本会拼进首轮 system prompt）。"""
    assert lab._arm_request_names("M01", "control", 6) == ("M01", 0)
    assert lab._arm_request_names("M01", "treatment", 6) == ("M01__treatment", 6)


def test_replay_miss_marks_infra_excluded(mini_env):
    # treatment 回放 control 语料：prompt 多学习层 → 必然 miss
    runner = _runner(mini_env, treatment_replay=str(mini_env["ctl_corpus"]))
    experiment = runner.run_suite()

    assert experiment["paired"] == []
    assert len(experiment["excluded"]) == 1
    entry = experiment["excluded"][0]
    assert entry["task_id"] == "MINI01"
    arms = {a["arm"]: a for a in entry["excluded_arms"]}
    assert set(arms) == {"treatment"}
    assert arms["treatment"]["reason"] == "replay_miss"
    assert arms["treatment"]["miss_keys"]  # sha 前缀被提取

    rows = {row["arm"]: row for row in experiment["rows"]}
    assert rows["control"]["infra_excluded"] is False
    assert rows["treatment"]["infra_excluded"] is True
    assert rows["treatment"]["success"] is False
    assert "replay 语料未命中" in rows["treatment"]["error_summary"]

    # AC-4：报告单列 —— md 渲染 infra_excluded 小节且带 miss 详情
    paths = runner.write_results(experiment)
    text = Path(paths["summary_md"]).read_text(encoding="utf-8")
    assert "## infra_excluded" in text
    assert "replay_miss" in text and "MINI01" in text
