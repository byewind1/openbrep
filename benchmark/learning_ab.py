"""学习效果 A/B 实验架（G3）：同一批 MODIFY fixture 任务跑两臂，回答
「include_learned_skills=True + 冻结学习快照」相对 benchmark 现口径有没有用。

两臂：
- control   ：include_learned_skills=False（与 benchmark/runner.py 现口径
  逐项一致），回放**现有 modify 黄金语料**
  （benchmark/fixtures/llm_corpus/modify.jsonl，默认 --control-corpus）；
- treatment ：include_learned_skills=True + 挂载学习快照（--snapshot），
  录制（--record）或回放（--replay）**独立的实验语料**。快照由
  benchmark/learning_snapshot.py 产生（AC-1），默认零快照 = 空快照语义
  （快照五项全 absent，仍比 control 多 developer 基线教训层）。

设计要点（AC-2/AC-3）：
- 每臂每任务独立 fixture 副本（copytree 到 --workdir/<task>__<arm>），
  treatment 副本在复制后 materialize 快照；副本即用即弃；
- 编译器恒为 MockHSFCompiler（录制纪律：compile 反馈嵌在 agent loop
  prompt 里，必须确定性；与 modify 语料录制口径一致）；
- 两臂 quality_ledger_enabled=True：每 run 结束后从工作副本收割
  QualityRecord（<副本>/.openbrep/quality/runs/<run_id>.json，run_id 取自
  TaskResult.metadata）进结果行，显式拷贝，副本销毁不丢；
- replay miss（ReplayLLM KeyError，文案 "replay 语料未命中"）→ 该臂该任务
  记 infra_excluded，不进配对统计，报告单列（M20 类环境缺 fixture 记忆时
  如实呈现，不假装通过也不算模型失败）；
- 写隔离：快照跑前 verify、跑后重算；skills/ 下出现带 reuse_count
  frontmatter 的文件时 preflight 拒绝启动（skills_loader 会写回该类文件）；
  fixture 原件零触碰（只读拷贝）；experiment 只写 --workdir/--results-dir
  （gitignored）。

输出（--results-dir，gitignored）：
    learning_ab_<suite>.json          全量实验记录
    learning_ab_<suite>_summary.md    人类可读报告（配对 delta + 三轴）

确定性（离线 mock 双跑逐字节一致，豁免清单见下，测试按此归一化）：
- mock 臂（MockLLM 确定性响应）与 replay 臂（黄金语料）都无随机性；
- 豁免键（递归剔除/清零后再比）：run_id（随机后缀）、ts、generated_at、
  elapsed_sec（墙钟）。其余字段必须逐字节一致。

用法（仓库根目录）：
    # 离线自检（mock：双臂确定性，仅验证流程/隔离/报告）
    python benchmark/learning_ab.py --llm mock --suite benchmark/tasks/modify/
    # 录制 treatment 真实语料（消耗 API 额度）
    python benchmark/learning_ab.py --llm real --suite benchmark/tasks/modify/ \
        --record benchmark/results/corpora/learning_treatment.jsonl \
        --snapshot benchmark/results/snapshots/demo --config config.toml
    # 全离线配对回放（treatment 回放独立语料 + control 回放 modify 语料）
    python benchmark/learning_ab.py --llm mock --suite benchmark/tasks/modify/ \
        --replay benchmark/results/corpora/learning_treatment.jsonl \
        --snapshot benchmark/results/snapshots/demo
"""

from __future__ import annotations

import argparse
import datetime
import json
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark.assertions import assert_success_criteria  # noqa: E402
from benchmark.learning_snapshot import (  # noqa: E402
    load_manifest,
    materialize,
    verify,
)
from benchmark.runner import _apply_naming_alignment, _git_commit  # noqa: E402
from benchmark.schema import load_benchmark_task  # noqa: E402
from openbrep.compiler import MockHSFCompiler  # noqa: E402
from openbrep.config import GDLAgentConfig  # noqa: E402
from openbrep.gdl_contract_checker import GDLContractChecker  # noqa: E402
from openbrep.hsf_project import HSFProject  # noqa: E402
from openbrep.llm import MockLLM  # noqa: E402
from openbrep.static_checker import StaticChecker  # noqa: E402

ARMS = ("control", "treatment")
DEFAULT_CONTROL_CORPUS = "benchmark/fixtures/llm_corpus/modify.jsonl"
DEFAULT_REPLAY_CONFIG = "benchmark/fixtures/replay_config_modify.toml"
DEFAULT_WORKDIR = "benchmark/workdir/learning_ab"
DEFAULT_RESULTS_DIR = "benchmark/results"

# ReplayLLM miss 文案（llm_replay._miss）——识别 infra_excluded 的可靠标记，
# 禁止用"任务失败"这类模糊信号归类。
_REPLAY_MISS_MARKERS = ("replay 语料未命中", "请用 --llm-record 重新录制")
_MISS_KEY_RE = re.compile(r"sha256:([0-9a-f]{12,40})")

# learning_seen 探测标记：include_learned_skills=True 时 build_skill_prompt
# 渲染出的层标题（learning.py build_error_learning_skill）+ 快照 learned_skill
# 内容首行片段。control 臂任何情况下都不含这些文本。
_LEARNING_LAYER_MARKERS = (
    "## Skill: workspace_gdl_error_avoidance",
    "## Skill: workspace_distilled_lessons",
    "## Skill: developer_gdl_error_baseline",
)


class HarnessError(RuntimeError):
    """preflight 失败（含全部问题清单）。"""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def scan_skills_for_reuse_count(skills_dir: str | Path) -> list[str]:
    """preflight：返回 skills/ 下带 reuse_count 字段的 .md 文件相对名。

    skills_loader._count_reuse 会把 reuse_count/last_used 写回带该 frontmatter
    的 skill 文件——实验期间一旦写回，skills/ 被污染（AC-3 门禁），
    必须在启动前拒绝（防未来漂移）。
    """
    root = Path(skills_dir)
    hits: list[str] = []
    if not root.is_dir():
        return hits
    for path in sorted(root.rglob("*.md")):
        try:
            head = path.read_text(encoding="utf-8")[:2048]
        except Exception:
            continue
        if "reuse_count" in head:
            hits.append(str(path.relative_to(root)))
    return hits


def dirty_entries(repo_root: str | Path, rel_paths: list[str]) -> list[str]:
    """`git status --short -- <paths>` 输出逐行（空 = 干净）。"""
    try:
        proc = subprocess.run(
            ["git", "status", "--short", "--", *rel_paths],
            cwd=str(Path(repo_root).resolve()),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return ["<git status 执行失败>"]
    if proc.returncode != 0:
        return [f"<git status 非零退出: {proc.returncode}>"]
    return [line for line in proc.stdout.splitlines() if line.strip()]


class ProbeLLM:
    """LLM 透明代理：计数 + 记录每次调用的 messages 文本（供 miss 侦测与
    learning_seen 取证），其余属性委托给内层。不改任何调用语义。"""

    def __init__(self, base):
        self._base = base
        self.call_count = 0
        self.message_texts: list[str] = []

    def _observe(self, messages) -> None:
        self.call_count += 1
        if len(self.message_texts) < 200:
            texts = []
            for message in messages or []:
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if isinstance(content, str) and content:
                    texts.append(content)
            if texts:
                self.message_texts.append("\n".join(texts))

    def generate(self, messages, **kwargs):
        self._observe(messages)
        return self._base.generate(messages, **kwargs)

    def generate_with_tools(self, messages, tools, **kwargs):
        self._observe(messages)
        return self._base.generate_with_tools(messages, tools, **kwargs)

    def generate_with_image(self, *args, **kwargs):
        self.call_count += 1
        return self._base.generate_with_image(*args, **kwargs)

    def generate_with_images(self, *args, **kwargs):
        self.call_count += 1
        return self._base.generate_with_images(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._base, name)


class ArmSpec:
    """单臂运行时参数（control 与 treatment 的差异全在这里）。"""

    def __init__(
        self,
        label: str,
        *,
        include_learned_skills: bool,
        snapshot_dir: str | Path | None,
    ):
        self.label = label
        self.include_learned_skills = include_learned_skills
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None


def _fixture_src(task) -> Path:
    fixture = Path(task.fixture)
    if fixture.is_absolute():
        return fixture
    return PROJECT_ROOT / fixture


class LearningABRunner:
    def __init__(
        self,
        *,
        suite_dir: str,
        llm_mode: str = "mock",
        budget: int = 6,
        config_path: str | None = None,
        control_corpus: str | None = None,
        treatment_record: str | None = None,
        treatment_replay: str | None = None,
        snapshot_dir: str | Path | None = None,
        work_dir: str | Path | None = None,
        results_dir: str | Path | None = None,
        skills_dir: str | Path | None = None,
    ):
        if llm_mode not in {"mock", "real"}:
            raise ValueError("llm_mode must be one of: mock, real")
        if treatment_record and treatment_replay:
            raise ValueError("--record 与 --replay 互斥（treatment 语料一次只走一种）")
        self.llm_mode = llm_mode
        self.budget = budget
        self.suite_dir = suite_dir
        self.config_path = config_path or str(PROJECT_ROOT / DEFAULT_REPLAY_CONFIG)
        if control_corpus is None:
            self.control_corpus = str(PROJECT_ROOT / DEFAULT_CONTROL_CORPUS)
        else:
            path = Path(control_corpus)
            self.control_corpus = str(path if path.is_absolute() else PROJECT_ROOT / path)
        self.treatment_record = treatment_record
        self.treatment_replay = treatment_replay
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        base_work = Path(work_dir) if work_dir else PROJECT_ROOT / DEFAULT_WORKDIR
        self.work_dir = base_work if base_work.is_absolute() else PROJECT_ROOT / base_work
        base_results = (
            Path(results_dir) if results_dir else PROJECT_ROOT / DEFAULT_RESULTS_DIR
        )
        self.results_dir = (
            base_results if base_results.is_absolute() else PROJECT_ROOT / base_results
        )
        self.skills_dir = Path(skills_dir) if skills_dir else PROJECT_ROOT / "skills"

        self.config = GDLAgentConfig.load(self.config_path)
        self.compiler = MockHSFCompiler()
        self.arm_specs = {
            "control": ArmSpec("control", include_learned_skills=False, snapshot_dir=None),
            "treatment": ArmSpec(
                "treatment", include_learned_skills=True, snapshot_dir=self.snapshot_dir
            ),
        }
        self._snapshot_manifest_sha: str | None = None
        self._cached_llms: dict[str, object] = {}

    # ── preflight / postflight（AC-3 写隔离硬门禁）─────────────────

    def _snapshot_sha(self) -> str:
        import hashlib

        return hashlib.sha256(
            (self.snapshot_dir / "manifest.json").read_bytes()
        ).hexdigest() if self.snapshot_dir else ""

    def preflight(self) -> list[str]:
        problems: list[str] = []
        reuse = scan_skills_for_reuse_count(self.skills_dir)
        if reuse:
            problems.append(
                "skills/ 存在带 reuse_count frontmatter 的文件，实验会触发写回污染，拒绝启动："
                + ", ".join(reuse)
            )
        corpus = Path(self.control_corpus)
        if not corpus.is_file():
            problems.append(f"control 语料不存在：{corpus}")
        if self.treatment_replay and not Path(self.treatment_replay).is_file():
            problems.append(f"treatment 回放语料不存在：{self.treatment_replay}")
        if self.treatment_record and Path(self.treatment_record).exists():
            # 录制 = fresh 语料（RecordingLLM 语义），旧文件会混入脏行
            problems.append(
                f"treatment 录制目标已存在（录制 = fresh 语料，须先删除）：{self.treatment_record}"
            )
        if self.snapshot_dir is not None:
            if not (self.snapshot_dir / "manifest.json").is_file():
                problems.append(f"快照目录缺 manifest.json：{self.snapshot_dir}")
            elif not verify(self.snapshot_dir):
                ok, issues = verify_report_issues(self.snapshot_dir)
                problems.append(f"快照校验失败（跑前）：{'；'.join(issues)}")
            else:
                self._snapshot_manifest_sha = self._snapshot_sha()
        return problems

    def postflight(self) -> dict:
        """实验后校验：快照重算 + skills/benchmark 的 git 污染（含豁免说明）。"""
        out: dict[str, Any] = {"snapshot_ok": None, "skills_dirty": [], "benchmark_dirty": []}
        if self.snapshot_dir is not None:
            out["snapshot_ok"] = verify(self.snapshot_dir)
        out["skills_dirty"] = dirty_entries(PROJECT_ROOT, ["skills/"])
        out["benchmark_dirty"] = dirty_entries(PROJECT_ROOT, ["benchmark/"])
        return out

    # ── LLM 组装 ──────────────────────────────────────────

    def _build_arm_llm(self, spec: ArmSpec):
        if spec.label == "control":
            from benchmark.llm_replay import ReplayLLM

            return ReplayLLM(self.control_corpus)
        if self.treatment_replay:
            from benchmark.llm_replay import ReplayLLM

            return ReplayLLM(self.treatment_replay)
        if self.llm_mode == "mock":
            base = MockLLM()
        else:
            from openbrep.llm import LLMAdapter

            base = LLMAdapter(self.config.llm)
        if self.treatment_record:
            from benchmark.llm_replay import RecordingLLM

            return RecordingLLM(base, self.treatment_record)
        return base

    def _make_arm_llm(self, spec: ArmSpec):
        # 每臂 LLM 整次 run_suite 只构造一次并缓存：RecordingLLM 构造时以
        # "w" 模式打开语料（录制 = fresh），逐任务构造会把前序条目截断掉
        # （runner.py 也是在套件层包一次 RecordingLLM，同一原因）。
        cached = self._cached_llms.get(spec.label)
        if cached is None:
            cached = self._build_arm_llm(spec)
            self._cached_llms[spec.label] = cached
        return cached

    # ── 单任务单臂 ────────────────────────────────────────

    def _run_one(self, task, spec: ArmSpec) -> dict:
        from openbrep.runtime.pipeline import TaskPipeline, TaskRequest

        label = spec.label
        work_copy = self.work_dir / f"{task.id}__{label}"
        if work_copy.exists():
            shutil.rmtree(work_copy)
        fixture_src = _fixture_src(task)
        shutil.copytree(fixture_src, work_copy)
        if spec.snapshot_dir is not None:
            mount = materialize(spec.snapshot_dir, work_copy)
            mount_log = {"written": mount["written"], "removed": mount["removed"]}
        else:
            mount_log = {"written": [], "removed": []}
        project = HSFProject.load_from_disk(str(work_copy))

        probe = ProbeLLM(self._make_arm_llm(spec))
        pipeline = TaskPipeline(
            config=self.config,
            trace_dir=str(self.work_dir / "traces"),
            include_learned_skills=spec.include_learned_skills,
            quality_ledger_enabled=True,
        )
        pipeline._make_llm = lambda _req: probe
        pipeline._make_compiler = lambda: self.compiler

        request = TaskRequest(
            user_input=task.description,
            intent="MODIFY",
            project=project,
            work_dir=str(self.work_dir),
            output_dir=str(self.results_dir),
            gsm_name=f"{task.id}__{label}",
            agent_loop=True,
            agent_loop_budget=self.budget,
        )
        start = time.monotonic()
        try:
            result = pipeline.execute(request)
        except Exception as exc:
            # execute 之外崩溃（正常不应发生）：记 crash 行，不进 infra
            return {
                "task_id": task.id,
                "arm": label,
                "success": False,
                "error_summary": f"harness crash: {exc}",
                "crashed": True,
            }
        elapsed_sec = round(time.monotonic() - start, 3)

        compile_pass = bool(result.compile_result and result.compile_result.success)
        final_project = result.project or project
        naming_alignment = _apply_naming_alignment(task, final_project)
        static_result = StaticChecker().check(final_project)
        contract_result = GDLContractChecker().check(final_project)
        criteria_result = assert_success_criteria(final_project, task.success_criteria)
        success = (
            compile_pass
            and static_result.passed
            and contract_result.passed
            and criteria_result.passed
        )

        execution = (result.metadata or {}).get("execution") or {}
        run_id = (result.metadata or {}).get("run_id")
        record = self._harvest_record(work_copy, run_id)
        prompt_text = "\n".join(probe.message_texts)
        error_text = str(result.error or "") if result.error else ""
        all_text = prompt_text + "\n" + error_text
        infra = self._replay_miss_classify(all_text, result)

        row: dict[str, Any] = {
            "task_id": task.id,
            "arm": label,
            "success": success,
            "compile_pass": compile_pass,
            "static_pass": static_result.passed,
            "contract_pass": contract_result.passed,
            "criteria_pass": criteria_result.passed,
            "criteria_failures": criteria_result.failures,
            "llm_calls": execution.get("llm_calls"),
            "tool_calls": execution.get("tool_calls"),
            "repair_rounds": int(execution.get("repair_rounds") or 0),
            "timeout": bool(execution.get("timeout", False)),
            "budget_exhausted": bool(execution.get("budget_exhausted", False)),
            "elapsed_sec": elapsed_sec,
            "run_id": run_id or "",
            "learning_seen": self._learning_seen(prompt_text),
            "changed_files": sorted((result.scripts or {}).keys()),
            "naming_alignment": naming_alignment,
            "mount": mount_log,
            "quality_record": record,
            "error_summary": "" if success else (result.error or ""),
            "infra_excluded": infra is not None,
            "infra_reason": infra,
        }
        return row

    def _harvest_record(self, project_copy: Path, run_id: Any) -> dict | None:
        """从（即用即弃的）工作副本显式收割 QualityRecord 文件内容。"""
        runs_dir = project_copy / ".openbrep" / "quality" / "runs"
        candidates: list[Path] = []
        if run_id:
            target = runs_dir / f"{run_id}.json"
            if target.is_file():
                candidates.append(target)
        if runs_dir.is_dir() and not candidates:
            candidates = sorted(runs_dir.glob("*.json"))
        if not candidates:
            return None
        try:
            data = json.loads(candidates[0].read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _replay_miss_classify(all_text: str, result) -> dict | None:
        """ReplayLLM miss → infra 记录；否则 None。文案必须逐字匹配 llm_replay。"""
        if not any(marker in all_text for marker in _REPLAY_MISS_MARKERS):
            return None
        keys = sorted(set(_MISS_KEY_RE.findall(all_text)))
        return {"reason": "replay_miss", "miss_keys": keys}

    def _learning_seen(self, prompt_text: str) -> bool:
        if any(marker in prompt_text for marker in _LEARNING_LAYER_MARKERS):
            return True
        if self.snapshot_dir is None:
            return False
        for rel in (
            ".openbrep/memory/skills/learned_skill.md",
            ".openbrep/learnings/learned_skill.md",
        ):
            path = self.snapshot_dir / rel
            if not path.is_file():
                continue
            try:
                nonempty = (
                    line.strip()
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
                first_line = next(nonempty, "")
            except Exception:
                first_line = ""
            if first_line and first_line[:80] in prompt_text:
                return True
        return False

    # ── 套件 ──────────────────────────────────────────────

    def run_suite(self) -> dict:
        problems = self.preflight()
        if problems:
            raise HarnessError(problems)
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

        task_files = sorted(Path(self.suite_dir).glob("*.yaml"))
        rows: list[dict] = []
        skipped: list[str] = []
        for task_file in task_files:
            task = load_benchmark_task(task_file)
            if not task.fixture:
                print(f"Skipping {task_file.name}: 非 fixture 任务（A/B 只覆盖 MODIFY 任务）")
                skipped.append(task.id)
                continue
            for spec in (self.arm_specs["control"], self.arm_specs["treatment"]):
                print(f"A/B running {task.id} [{spec.label}]...")
                row = self._run_one(task, spec)
                rows.append(row)
                status = (
                    "⚠infra"
                    if row.get("infra_excluded")
                    else ("✅" if row.get("success") else "❌")
                )
                print(
                    f"  {status} {task.id} {spec.label}: "
                    f"success={row.get('success')} llm={row.get('llm_calls')} "
                    f"criteria={len(row.get('criteria_failures') or [])}"
                )
        experiment = self._build_experiment(rows, skipped)
        for llm in self._cached_llms.values():
            try:
                if hasattr(llm, "_fh"):
                    llm._fh.close()
            except Exception:
                pass
        self._cached_llms.clear()
        return experiment

    def _build_experiment(self, rows: list[dict], skipped: list[str]) -> dict:
        by_task: dict[str, dict] = {}
        for row in rows:
            by_task.setdefault(row["task_id"], {})[row["arm"]] = row
        paired: list[dict] = []
        excluded: list[dict] = []
        for task_id in sorted(by_task):
            arm_rows = by_task[task_id]
            control = arm_rows.get("control")
            treatment = arm_rows.get("treatment")
            if control is None or treatment is None:
                # 单臂行（crashed 行只有半套字段）：显式记 excluded，
                # 否则任务会从报告里无声消失。
                surviving = control or treatment
                missing = "treatment" if control is None else "control"
                excluded.append(
                    {
                        "task_id": task_id,
                        "excluded_arms": [
                            {
                                "arm": missing,
                                "reason": (
                                    "arm_crashed"
                                    if surviving.get("crashed")
                                    else "arm_missing"
                                ),
                            }
                        ],
                    }
                )
                continue
            if control.get("crashed") or treatment.get("crashed"):
                excluded.append(
                    {
                        "task_id": task_id,
                        "excluded_arms": [
                            {"arm": arm, "reason": "arm_crashed"}
                            for arm, row in (("control", control), ("treatment", treatment))
                            if row.get("crashed")
                        ],
                    }
                )
                continue
            if control.get("infra_excluded") or treatment.get("infra_excluded"):
                entry: dict[str, Any] = {
                    "task_id": task_id,
                    "excluded_arms": [
                        {
                            "arm": arm,
                            "reason": (row.get("infra_reason") or {}).get("reason"),
                            "miss_keys": (row.get("infra_reason") or {}).get("miss_keys", []),
                        }
                        for arm, row in (("control", control), ("treatment", treatment))
                        if row.get("infra_excluded")
                    ],
                }
                if control and not control.get("infra_excluded"):
                    entry["control_note"] = {
                        "success": control["success"],
                        "learning_seen": control["learning_seen"],
                    }
                if treatment and not treatment.get("infra_excluded"):
                    entry["treatment_note"] = {
                        "success": treatment["success"],
                        "learning_seen": treatment["learning_seen"],
                    }
                excluded.append(entry)
                continue
            paired.append(self._pair_record(control, treatment))

        suite_name = Path(self.suite_dir).name or "modify"
        snapshot_info = None
        if self.snapshot_dir is not None:
            manifest = load_manifest(self.snapshot_dir)
            snapshot_info = {
                "dir": str(self.snapshot_dir),
                "manifest_sha256": self._snapshot_manifest_sha or "",
                "entries": [
                    {
                        "rel_path": e.get("rel_path"),
                        "absent": bool(e.get("absent")),
                        "sha256": e.get("sha256"),
                    }
                    for e in manifest.get("entries") or []
                ],
            }
        summary = {
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "suite": suite_name,
            "commit": _git_commit(),
            "llm": self.llm_mode,
            "compiler": "mock",
            "agent_loop_budget": self.budget,
            "config": self.config_path,
            "control_corpus": self.control_corpus,
            "treatment_record": self.treatment_record,
            "treatment_replay": self.treatment_replay,
            "snapshot": snapshot_info,
            "environment": {"platform": platform.platform(), "system": platform.system()},
            "paired_tasks": len(paired),
            "excluded_tasks": len(excluded),
            "skipped_tasks": skipped,
        }
        aggregate = {arm: self._aggregate_arm([p[arm] for p in paired]) for arm in ARMS}
        return {
            "summary": summary,
            "paired": paired,
            "excluded": excluded,
            "aggregate": aggregate,
            "rows": rows,
        }

    @staticmethod
    def _pair_record(control: dict, treatment: dict) -> dict:
        def metric(row: dict, key: str):
            value = row.get(key)
            if isinstance(value, list):
                return len(value)
            if value is None:
                return None
            return value

        delta_keys = (
            "success", "criteria_failures", "llm_calls",
            "tool_calls", "repair_rounds", "elapsed_sec",
        )
        deltas: dict[str, Any] = {}
        for key in delta_keys:
            c = metric(control, key)
            t = metric(treatment, key)
            if c is None or t is None:
                deltas[key] = None
                continue
            if isinstance(c, bool):
                deltas[key] = t is not c
                continue
            deltas[key] = round(t - c, 3) if key == "elapsed_sec" else t - c
        if treatment["success"] and not control["success"]:
            winner = "treatment"
        elif control["success"] and not treatment["success"]:
            winner = "control"
        else:
            winner = "tie"
        return {
            "task_id": control["task_id"],
            "control": _row_public(control),
            "treatment": _row_public(treatment),
            "deltas": deltas,
            "winner": winner,
            "quality_delta": {
                "control_outcome": _record_outcome(control),
                "treatment_outcome": _record_outcome(treatment),
            },
        }

    @staticmethod
    def _aggregate_arm(rows: list[dict]) -> dict:
        total = len(rows)
        if not total:
            return {"tasks": 0}
        criteria_failures = sum(len(r.get("criteria_failures") or []) for r in rows)
        records = [r.get("quality_record") for r in rows]
        outcomes: dict[str, int] = {}
        record_unavailable = 0
        for record in records:
            if record is None:
                record_unavailable += 1
                continue
            outcome = str(record.get("outcome") or "unknown")
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        artifact: dict[str, Any] = {}
        dimensions = (
            "parametricity", "cross_script", "topology",
            "dimension_contract", "requirements",
        )
        for dimension in dimensions:
            counts: dict[str, int] = {}
            for record in records:
                if record is None:
                    counts["unavailable"] = counts.get("unavailable", 0) + 1
                    continue
                block = (record.get("artifact_quality") or {}).get(dimension) or {}
                status = str(block.get("status") or "unavailable")
                counts[status] = counts.get(status, 0) + 1
            artifact[dimension] = counts
        exec_metrics: dict[str, list] = {
            "llm_calls": [], "tool_calls": [], "repair_rounds": [], "elapsed_sec": []
        }
        timeout_count = 0
        budget_count = 0
        for record in records:
            block = (record.get("execution_cost") or {}) if record else {}
            for key in exec_metrics:
                value = block.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    exec_metrics[key].append(float(value))
            timeout_count += 1 if block.get("timeout") else 0
            budget_count += 1 if block.get("budget_exhausted") else 0

        def avg(values: list) -> float | None:
            return round(sum(values) / len(values), 3) if values else None

        return {
            "tasks": total,
            "delivery": {
                "success": sum(1 for r in rows if r["success"]),
                "success_rate": round(sum(1 for r in rows if r["success"]) / total, 4),
                "compile_pass": sum(1 for r in rows if r["compile_pass"]),
                "compile_pass_rate": round(sum(1 for r in rows if r["compile_pass"]) / total, 4),
                "criteria_pass": sum(1 for r in rows if r["criteria_pass"]),
                "criteria_failures_total": criteria_failures,
                "outcomes": outcomes,
                "quality_record_unavailable": record_unavailable,
            },
            "artifact_quality": artifact,
            "execution_cost": {
                "llm_calls_total": round(sum(exec_metrics["llm_calls"]), 1),
                "llm_calls_avg": avg(exec_metrics["llm_calls"]),
                "tool_calls_total": round(sum(exec_metrics["tool_calls"]), 1),
                "tool_calls_avg": avg(exec_metrics["tool_calls"]),
                "repair_rounds_total": round(sum(exec_metrics["repair_rounds"]), 1),
                "repair_rounds_avg": avg(exec_metrics["repair_rounds"]),
                "elapsed_total_sec": round(sum(exec_metrics["elapsed_sec"]), 3),
                "elapsed_avg_sec": avg(exec_metrics["elapsed_sec"]),
                "timeout_count": timeout_count,
                "budget_exhausted_count": budget_count,
            },
        }

    def write_results(self, experiment: dict) -> dict[str, str]:
        suite_name = experiment["summary"]["suite"]
        json_path = self.results_dir / f"learning_ab_{suite_name}.json"
        md_path = self.results_dir / f"learning_ab_{suite_name}_summary.md"
        json_path.write_text(
            json.dumps(experiment, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        md_path.write_text(render_learning_markdown(experiment), encoding="utf-8")
        return {"results_json": str(json_path), "summary_md": str(md_path)}


def _row_public(row: dict) -> dict:
    keys = (
        "success", "compile_pass", "static_pass", "contract_pass", "criteria_pass",
        "criteria_failures", "llm_calls", "tool_calls", "repair_rounds", "timeout",
        "budget_exhausted", "elapsed_sec", "learning_seen", "changed_files",
        "quality_record", "error_summary",
    )
    return {key: row.get(key) for key in keys}


def _record_outcome(row: dict) -> str | None:
    record = row.get("quality_record")
    if record is None:
        return "unavailable"
    return str(record.get("outcome") or "unknown")


def verify_report_issues(snapshot_dir: str | Path) -> tuple[bool, list[str]]:
    from benchmark.learning_snapshot import verify_report

    return verify_report(snapshot_dir)


# ── 报告渲染 ─────────────────────────────────────────────

def render_learning_markdown(experiment: dict) -> str:
    summary = experiment["summary"]
    lines = [
        "# OpenBrep Learning A/B Summary（control vs treatment）",
        "",
        f"- generated_at: {summary['generated_at']}",
        f"- commit: {summary.get('commit') or 'unknown'}",
        f"- suite: {summary.get('suite')}",
        f"- compiler: {summary.get('compiler')} / llm: {summary.get('llm')}",
        f"- agent_loop_budget: {summary.get('agent_loop_budget')}",
        f"- config: {summary.get('config')}",
        f"- control corpus: {summary.get('control_corpus')}",
        f"- treatment record: {summary.get('treatment_record') or '-'}",
        f"- treatment replay: {summary.get('treatment_replay') or '-'}",
    ]
    snapshot = summary.get("snapshot")
    if snapshot:
        lines += [
            f"- snapshot: {snapshot.get('dir')}",
            f"- snapshot manifest sha256: {snapshot.get('manifest_sha256') or '-'}",
        ]
        lines.append("- snapshot entries:")
        for entry in snapshot.get("entries") or []:
            state = "absent" if entry.get("absent") else f"sha {str(entry.get('sha256'))[:12]}…"
            lines.append(f"  - {entry.get('rel_path')}: {state}")
    lines += [
        "",
        "## 三轴汇总（paired N={}；excluded N={}，见末节）".format(
            summary["paired_tasks"], summary["excluded_tasks"]
        ),
        "",
        "### 轴 1：delivery（门禁 + 质量账本终态）",
        "",
        "| 臂 | 任务数 | 成功 | 成功率 | 编译通过 | 门禁 criteria 过 | criteria_failures 总数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARMS:
        agg = experiment["aggregate"].get(arm) or {}
        delivery = agg.get("delivery") or {}
        lines.append(
            "| {arm} | {tasks} | {succ} | {rate} | {comp} | {cri} | {cf} |".format(
                arm=arm,
                tasks=agg.get("tasks", 0),
                succ=delivery.get("success", 0),
                rate=_fmt_rate(delivery.get("success_rate")),
                comp=delivery.get("compile_pass", 0),
                cri=delivery.get("criteria_pass", 0),
                cf=delivery.get("criteria_failures_total", 0),
            )
        )
    lines.append("")
    for arm in ARMS:
        agg = experiment["aggregate"].get(arm) or {}
        delivery = agg.get("delivery") or {}
        lines.append(
            f"- {arm} 账本终态（completed/gate_fail/…，unavailable=未收割到记录）："
            f"{json.dumps(delivery.get('outcomes') or {}, ensure_ascii=False)}"
            f" unavailable={delivery.get('quality_record_unavailable', 0)}"
        )
    lines += [
        "",
        "### 轴 2：artifact_quality（质量账本观测维度，unavailable 如实记，无综合分）",
        "",
        "| 臂 | parametricity measured | parametricity unavailable |"
        " cross_script measured | cross_script unavailable |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARMS:
        agg = experiment["aggregate"].get(arm) or {}
        artifact = agg.get("artifact_quality") or {}
        parametricity = artifact.get("parametricity") or {}
        cross_script = artifact.get("cross_script") or {}
        lines.append(
            "| {arm} | {pm} | {pu} | {cm} | {cu} |".format(
                arm=arm,
                pm=parametricity.get("measured", 0),
                pu=parametricity.get("unavailable", 0),
                cm=cross_script.get("measured", 0),
                cu=cross_script.get("unavailable", 0),
            )
        )
    lines += [
        "",
        "### 轴 3：execution_cost（成本轴独立展示，不混入质量分）",
        "",
        "| 臂 | llm_calls 合计 | llm_calls 平均 | tool_calls 平均 | repair_rounds 平均 |"
        " 耗时合计(s) | 超时 | 预算耗尽 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARMS:
        agg = experiment["aggregate"].get(arm) or {}
        cost = agg.get("execution_cost") or {}
        row_fmt = (
            "| {arm} | {llm_t} | {llm_a} | {tool_a} | {repair_a} |"
            " {sec} | {timeout} | {budget} |"
        )
        lines.append(
            row_fmt.format(
                arm=arm,
                llm_t=_fmt_num(cost.get("llm_calls_total")),
                llm_a=_fmt_num(cost.get("llm_calls_avg")),
                tool_a=_fmt_num(cost.get("tool_calls_avg")),
                repair_a=_fmt_num(cost.get("repair_rounds_avg")),
                sec=_fmt_num(cost.get("elapsed_total_sec")),
                timeout=cost.get("timeout_count", 0),
                budget=cost.get("budget_exhausted_count", 0),
            )
        )
    lines += [
        "",
        "## 逐题 paired delta（仅配对的非 excluded 任务）",
        "",
        "| Task | C 成功 | T 成功 | winner | Δcriteria | Δllm | Δtool | Δrepair |"
        " Δ秒 | T learning_seen |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for record in experiment["paired"]:
        control = record["control"]
        treatment = record["treatment"]
        deltas = record["deltas"]
        lines.append(
            "| {task} | {cs} | {ts} | {w} | {dc} | {dl} | {dt} | {dr} | {ds} | {seen} |".format(
                task=record["task_id"],
                cs=_mark(control["success"]),
                ts=_mark(treatment["success"]),
                w=record["winner"],
                dc=_fmt_int(deltas.get("criteria_failures")),
                dl=_fmt_int(deltas.get("llm_calls")),
                dt=_fmt_int(deltas.get("tool_calls")),
                dr=_fmt_int(deltas.get("repair_rounds")),
                ds=_fmt_num(deltas.get("elapsed_sec")),
                seen="yes" if treatment["learning_seen"] else "no",
            )
        )
    win_count = sum(1 for p in experiment["paired"] if p["winner"] == "treatment")
    lose_count = sum(1 for p in experiment["paired"] if p["winner"] == "control")
    tie_count = sum(1 for p in experiment["paired"] if p["winner"] == "tie")
    lines += [
        "",
        f"- treatment 胜 {win_count} / control 胜 {lose_count} / 平 {tie_count}"
        f"（paired {summary['paired_tasks']} 题）",
    ]
    if experiment["excluded"]:
        lines += [
            "",
            "## infra_excluded（回放 miss 等基础设施原因，不计入配对统计）",
            "",
        ]
        for entry in experiment["excluded"]:
            arms_desc = []
            for arm_entry in entry.get("excluded_arms") or []:
                miss_suffix = (
                    f"，miss_key {arm_entry.get('miss_keys')}"
                    if arm_entry.get("miss_keys")
                    else ""
                )
                arms_desc.append(
                    f"{arm_entry['arm']}（{arm_entry.get('reason')}{miss_suffix}）"
                )
            lines.append(f"- {entry['task_id']}: {', '.join(arms_desc)}")
    lines.append("")
    return "\n".join(lines)


def _fmt_rate(value) -> str:
    return "-" if value is None else f"{value:.0%}"


def _fmt_num(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _fmt_int(value) -> str:
    return "-" if value is None else str(int(value))


def _mark(value: bool) -> str:
    return "pass" if value else "fail"


def _cli() -> None:
    parser = argparse.ArgumentParser(description="学习效果 A/B 实验架（G3）")
    parser.add_argument("--suite", default="benchmark/tasks/modify/", help="MODIFY 任务目录")
    parser.add_argument(
        "--llm", default="mock", choices=["mock", "real"],
        help="treatment LLM：mock=MockLLM 离线自检；real=config 真实模型（耗额度）",
    )
    parser.add_argument(
        "--record", default=None, metavar="CORPUS.jsonl",
        help="录制 treatment 语料（独立于 fixtures/llm_corpus/，放 results/corpora/）",
    )
    parser.add_argument("--replay", default=None, metavar="CORPUS.jsonl",
                        help="回放 treatment 实验语料（与录制同 snapshot/套件才零 miss）")
    parser.add_argument(
        "--snapshot", default=None, metavar="DIR",
        help="学习快照目录（learning_snapshot.py capture 产出）",
    )
    parser.add_argument("--budget", type=int, default=6, help="agent loop 工具调用预算（默认 6）")
    parser.add_argument(
        "--config", default=None,
        help="config 路径（默认 replay_config_modify.toml 密封配置；真实 LLM 请传 config.toml）",
    )
    parser.add_argument(
        "--control-corpus", default=None,
        help="control 语料（默认 fixtures/llm_corpus/modify.jsonl）",
    )
    parser.add_argument(
        "--workdir", default=None,
        help="工作副本目录（默认 benchmark/workdir/learning_ab，gitignored）",
    )
    parser.add_argument(
        "--results-dir", default=None,
        help="结果输出目录（默认 benchmark/results，gitignored）",
    )
    parser.add_argument(
        "--skills-dir", default=None,
        help="skills 扫描目录（默认仓库 skills/，测试用）",
    )
    args = parser.parse_args()

    runner = LearningABRunner(
        suite_dir=args.suite,
        llm_mode=args.llm,
        budget=args.budget,
        config_path=args.config,
        control_corpus=args.control_corpus,
        treatment_record=args.record,
        treatment_replay=args.replay,
        snapshot_dir=args.snapshot,
        work_dir=args.workdir,
        results_dir=args.results_dir,
        skills_dir=args.skills_dir,
    )
    try:
        problems = runner.preflight()
    except Exception as exc:
        print(f"preflight 失败：{exc}")
        sys.exit(1)
    if problems:
        print("preflight 未通过：")
        for problem in problems:
            print(f"  ✗ {problem}")
        sys.exit(1)
    experiment = runner.run_suite()
    paths = runner.write_results(experiment)
    post = runner.postflight()
    print(f"\n实验完成：paired={experiment['summary']['paired_tasks']} "
          f"excluded={experiment['summary']['excluded_tasks']}")
    print(f"results: {paths['results_json']}")
    print(f"summary: {paths['summary_md']}")
    failures: list[str] = []
    if post.get("snapshot_ok") is False:
        failures.append("快照跑后校验失败（跑前一致、跑后被改）")
    for name, entries in (
        ("skills/", post["skills_dirty"]),
        ("benchmark/", post["benchmark_dirty"]),
    ):
        if entries:
            failures.append(f"{name} git 状态不干净：")
            failures.extend(f"    {entry}" for entry in entries)
    if failures:
        print("\npostflight 未通过：")
        for line in failures:
            print(f"  ✗ {line}")
        sys.exit(1)
    print("postflight ✓（快照一致 / skills/ 与 benchmark/ git 零污染）")


if __name__ == "__main__":
    _cli()
