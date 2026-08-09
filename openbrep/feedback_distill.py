"""反馈事件 → proposed 态教训候选的加工层（F2，自我优化系统）。

输入：F1 采集的项目级反馈事件（<project_root>/.openbrep/feedback.jsonl，8 种 kind）。
加工链：确定性预聚类（按 (kind, 规范化签名) 分组计数）→ 一次 LLM 语义提炼
（温度 0 / 小 max_tokens / 非流式）→ 写入 proposed 态教训库
<work_dir>/.openbrep/memory/learnings/distilled_lessons.jsonl
（与 ErrorLearningStore 同目录、独立新文件）。

纪律（本单红线）：
- proposed 教训**不进任何 prompt**：本模块与注入链路（build_skill_prompt /
  pipeline / knowledge_selector）零耦合，distilled_lessons.jsonl 注入层不读；
  晋升/入闸是 F3 的事，本单不做。
- 增量提炼：watermark 按文件行数记录已处理进度，只把新事件送提炼；
  零事件/零增量 → 不建 LLM 直接返回。
- 宁缺毋滥：LLM 调用失败/超时、坏 JSON、形态非法、空候选 → 静默返回空
  （不抛异常、不影响主流程）；任何文件写入失败 best-effort。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from openbrep.learning import LEARNINGS_DIR

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

DISTILLED_LESSONS_FILE = "distilled_lessons.jsonl"
WATERMARK_FILE = "distill_watermark.json"

LLM_TEMPERATURE = 0.0
LLM_MAX_TOKENS = 700          # 小 max_tokens：提炼结果很短
PATTERN_MAX_CHARS = 100       # pattern ≤ 100 字
GUIDANCE_MAX_CHARS = 300      # guidance ≤ 300 字
MAX_SAMPLES_PER_CLUSTER = 3   # 每簇保留 ≤3 条截断样本
SAMPLE_MAX_CHARS = 240        # 单条样本截断长度
SIGNATURE_MAX_CHARS = 500     # 规范化签名上限
PROPOSED_STATUS = "proposed"


def _lessons_path(work_dir: str | Path) -> Path:
    return Path(work_dir) / LEARNINGS_DIR / DISTILLED_LESSONS_FILE


def _watermark_path(work_dir: str | Path) -> Path:
    return Path(work_dir) / LEARNINGS_DIR / WATERMARK_FILE


# ── 1. 事件采集 ───────────────────────────────────────────

def collect_feedback_events(root: str | Path) -> dict[str, list[tuple[int, dict]]]:
    """采集反馈事件，返回 {feedback.jsonl 绝对路径: [(行号, 事件), ...]}。

    root 是项目目录（含 .openbrep/feedback.jsonl）→ 单项目；
    root 是工作区目录（含 hsf/）→ 扫 hsf/*/.openbrep/feedback.jsonl 聚合；
    坏行（非 JSON / 非 dict / 无 kind）跳过不计入。
    无任何反馈文件 → 空 dict。
    """
    root_path = Path(root)
    out: dict[str, list[tuple[int, dict]]] = {}
    direct = root_path / ".openbrep" / "feedback.jsonl"
    if direct.is_file():
        events = _read_feedback_file(direct)
        if events:
            out[str(direct)] = events
        return out
    hsf_dir = root_path / "hsf"
    if hsf_dir.is_dir():
        for entry in sorted(hsf_dir.iterdir()):
            if not entry.is_dir():
                continue
            fp = entry / ".openbrep" / "feedback.jsonl"
            if fp.is_file():
                events = _read_feedback_file(fp)
                if events:
                    out[str(fp)] = events
        return out
    return {}


def _read_feedback_file(path: Path) -> list[tuple[int, dict]]:
    """读一个 feedback.jsonl：坏行跳过；返回 (行号, 事件) 列表。

    行号用于 watermark 增量（按文件行数记进度），坏行也占行号——坏行只被
    跳过不进入聚类，但会随 watermark 前进，避免每次重读坏行。
    """
    events: list[tuple[int, dict]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 坏行跳过
                if isinstance(event, dict) and event.get("kind"):
                    events.append((lineno, event))
    except Exception as exc:
        logger.warning("feedback_distill read failed (skip file %s): %s", path, exc)
        return []
    return events


def _count_lines(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except Exception:
        return 0


# ── 2. 确定性预聚类 ───────────────────────────────────────

def _event_text(event: dict) -> str:
    """把事件压成用于签名/样本的文本：summary + detail 的字符串叶子。"""
    parts = [str(event.get("summary") or "")]
    detail = event.get("detail")
    if isinstance(detail, dict):
        for value in detail.values():
            if isinstance(value, str):
                parts.append(value)
            else:
                try:
                    parts.append(json.dumps(value, ensure_ascii=False, default=str))
                except Exception:
                    pass
    elif detail is not None:
        try:
            parts.append(json.dumps(detail, ensure_ascii=False, default=str))
        except Exception:
            pass
    return " ".join(parts)


def _normalize_signature(text: str) -> str:
    """规范化错误文本：去路径 / 数字 / 引号内容（思路同 learning.error_fingerprint）。

    保证同一失败模式的不同实例（路径、行号、数值、引号内的具体报错片段不同）
    归一为同一签名，跨项目聚簇。
    """
    t = (text or "").lower()
    t = re.sub(r"《[^》]+\.gsm》", "《<gsm>》", t)
    t = re.sub(r"第[\d、,，\s]+行", "第<n>行", t)
    t = re.sub(r"/[\w./\- ]+", "<path>", t)
    t = re.sub(r"\bline\s+\d+\b", "line <n>", t)
    t = re.sub(r"[“”「」]", '"', t)
    t = re.sub(r'"[^"]*"', "<quote>", t)
    t = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:SIGNATURE_MAX_CHARS]


def _lesson_fingerprint(kind: str, signature: str) -> str:
    """教训指纹：确定性签名（同簇跨次运行稳定，用于合并 count/last_seen）。"""
    digest = hashlib.sha1(f"{kind}\n{signature}".encode("utf-8")).hexdigest()[:12]
    return f"distill:{kind}:{digest}"


def precluster(events_by_file: dict[str, list[tuple[int, dict]]]) -> list[dict]:
    """确定性预聚类：按 (kind, 规范化签名) 分组计数。

    每簇：{kind, signature, count, samples(≤3), first_seen, last_seen,
    fingerprint}。输出按 (kind, -count, signature) 稳定排序，LLM 输入顺序确定。
    """
    clusters: dict[str, dict] = {}
    for _path, events in events_by_file.items():
        for _lineno, event in events:
            kind = str(event.get("kind") or "unknown")
            signature = _normalize_signature(_event_text(event))
            key = f"{kind}\n{signature}"
            ts = str(event.get("ts") or "")
            cluster = clusters.get(key)
            if cluster is None:
                clusters[key] = {
                    "kind": kind,
                    "signature": signature,
                    "count": 1,
                    "samples": [_sample_text(event)],
                    "first_seen": ts,
                    "last_seen": ts,
                }
            else:
                cluster["count"] += 1
                if ts and (not cluster["first_seen"] or ts < cluster["first_seen"]):
                    cluster["first_seen"] = ts
                if ts and ts > cluster["last_seen"]:
                    cluster["last_seen"] = ts
                if len(cluster["samples"]) < MAX_SAMPLES_PER_CLUSTER:
                    sample = _sample_text(event)
                    if sample not in cluster["samples"]:
                        cluster["samples"].append(sample)
    out: list[dict] = []
    for key, cluster in clusters.items():
        kind, signature = key.split("\n", 1)
        cluster["fingerprint"] = _lesson_fingerprint(kind, signature)
        out.append(cluster)
    out.sort(key=lambda c: (c["kind"], -c["count"], c["signature"]))
    return out


def _sample_text(event: dict) -> str:
    return _event_text(event)[:SAMPLE_MAX_CHARS]


def _clusters_summary(clusters: list[dict]) -> list[dict]:
    """对外返回的轻量聚类摘要（不含样本，避免响应过大）。"""
    return [
        {
            "kind": c["kind"],
            "count": c["count"],
            "fingerprint": c["fingerprint"],
            "signature": c["signature"][:80],
        }
        for c in clusters
    ]


# ── 3. 一次 LLM 语义提炼 ──────────────────────────────────

def build_distill_messages(clusters: list[dict]) -> list[dict]:
    """构造提炼用消息：聚类输入 + 严格 JSON 数组输出契约。"""
    system = (
        "你是 GDL 反馈教训提炼器。输入是若干聚类，每簇代表一类重复出现的失败反馈。"
        "请为每簇提炼一条模式级教训候选：抽象可复用的失败模式与修复/预防指引，"
        "不要复述单次实例（禁止路径、行号、数值、项目名）。\n\n"
        "只输出一个 JSON 数组，不要 Markdown 代码块标记，不要任何解释文字。"
        "数组长度必须等于输入聚类数、顺序一致。每项：\n"
        '{"pattern": "<模式描述，≤100 字>",\n'
        ' "guidance": "<可执行指引，≤300 字>",\n'
        ' "evidence_kinds": ["<该簇 kind>"],\n'
        ' "evidence_count": <该簇事件数>}\n\n'
        "硬性纪律：pattern/guidance 用中文、模式级抽象；宁缺毋滥，"
        "不确定时 guidance 写可执行的最小检查步骤。"
    )
    user_lines: list[str] = []
    for i, c in enumerate(clusters):
        user_lines.append(f"[聚类 {i}]")
        user_lines.append(f"kind: {c['kind']}")
        user_lines.append(f"事件数: {c['count']}")
        user_lines.append(f"时间范围: {c['first_seen']} ~ {c['last_seen']}")
        for sample in c["samples"]:
            user_lines.append(f"- {sample}")
        user_lines.append("")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_lines).strip()},
    ]


def _extract_json(text: str) -> Any:
    """严格 JSON 提取：先整段解析，再退化到第一个平衡的 {…} 或 […] 块。"""
    content = (text or "").strip()
    if not content:
        return None
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start == -1:
        start = stripped.find("[")
    if start == -1:
        return None
    open_ch, close_ch = ("{", "}") if stripped[start] == "{" else ("[", "]")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(stripped[start : i + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def parse_distill_response(text: str, clusters: list[dict]) -> Optional[list[dict]]:
    """严格解析 LLM 提炼结果。

    返回 [{pattern, guidance, evidence_kinds, evidence_count}]（与 clusters
    一一对应）；坏 JSON / 非数组 / 长度不匹配 / 字段非法 → None（静默空，
    宁缺毋滥）。pattern/guidance 超长截断到上限。
    """
    data = _extract_json(text)
    if not isinstance(data, list):
        return None
    if len(data) != len(clusters):
        return None
    items: list[dict] = []
    for item, cluster in zip(data, clusters):
        if not isinstance(item, dict):
            return None
        pattern = item.get("pattern")
        guidance = item.get("guidance")
        if not isinstance(pattern, str) or not pattern.strip():
            return None
        if not isinstance(guidance, str) or not guidance.strip():
            return None
        kinds = item.get("evidence_kinds")
        if not isinstance(kinds, list) or not kinds or not all(
            isinstance(k, str) and k.strip() for k in kinds
        ):
            kinds = [cluster["kind"]]
        count = item.get("evidence_count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            count = cluster["count"]
        items.append({
            "pattern": pattern.strip()[:PATTERN_MAX_CHARS],
            "guidance": guidance.strip()[:GUIDANCE_MAX_CHARS],
            "evidence_kinds": [str(k).strip() for k in kinds][:8],
            "evidence_count": count,
        })
    return items


# ── 4. proposed 教训库（与 ErrorLearningStore 同目录、独立文件） ──

def load_lessons(work_dir: str | Path) -> list[dict]:
    """读现有 proposed 教训；坏行跳过；文件缺失/读失败 → 空列表。"""
    path = _lessons_path(work_dir)
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("fingerprint"):
                out.append(rec)
    except Exception:
        return []
    return out


def save_lessons(work_dir: str | Path, lessons: list[dict]) -> bool:
    """原子写回教训库（tmp + rename）；失败 best-effort（返回 False）。"""
    try:
        path = _lessons_path(work_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for lesson in lessons:
                fh.write(json.dumps(lesson, ensure_ascii=False, sort_keys=True) + "\n")
        tmp.replace(path)
        return True
    except Exception as exc:
        logger.warning("feedback_distill save_lessons failed (best-effort): %s", exc)
        return False


def merge_lessons(existing: list[dict], new_lessons: list[dict]) -> list[dict]:
    """同 fingerprint 合并：count 累加、last_seen 取新、pattern/guidance 刷新。

    first_seen 保留最早值；文件顺序保持稳定（dict 保序）。
    """
    by_fp: dict[str, dict] = {lesson["fingerprint"]: lesson for lesson in existing}
    for new_lesson in new_lessons:
        fp = new_lesson["fingerprint"]
        prev = by_fp.get(fp)
        if prev is None:
            by_fp[fp] = dict(new_lesson)
            continue
        prev["count"] = int(prev.get("count") or 0) + int(new_lesson.get("count") or 0)
        if new_lesson.get("last_seen"):
            prev["last_seen"] = new_lesson["last_seen"]
        prev["pattern"] = new_lesson["pattern"]
        prev["guidance"] = new_lesson["guidance"]
        prev["evidence_kinds"] = new_lesson["evidence_kinds"]
    return list(by_fp.values())


def load_watermark(work_dir: str | Path) -> dict[str, int]:
    """读增量 watermark：{feedback.jsonl 绝对路径: 已处理行数}。"""
    try:
        data = json.loads(_watermark_path(work_dir).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items() if isinstance(v, (int, float))}
    except Exception:
        pass
    return {}


def save_watermark(work_dir: str | Path, watermark: dict[str, int]) -> bool:
    try:
        path = _watermark_path(work_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(watermark, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        logger.warning("feedback_distill save_watermark failed (best-effort): %s", exc)
        return False


# ── 5. 主入口 ─────────────────────────────────────────────

def _default_work_dir(root: Path) -> Path:
    """work_dir 缺省：工作区目录 → 根即工作区；项目目录 → 父目录。"""
    if (root / "hsf").is_dir():
        return root
    return root.parent


def _build_distill_llm():
    """从 GDLAgentConfig 构建提炼用 LLMAdapter；失败返回 None（静默跳过）。"""
    try:
        from openbrep.config import GDLAgentConfig
        from openbrep.llm import LLMAdapter

        config = GDLAgentConfig.load()
        return LLMAdapter(config.llm)
    except Exception as exc:
        logger.warning("feedback_distill llm build failed (skip distillation): %s", exc)
        return None


def distill(
    root: str | Path,
    work_dir: str | Path | None = None,
    llm: Any = None,
) -> dict:
    """核心加工入口：采集 → 增量筛选 → 预聚类 → LLM 提炼 → 合并写盘。

    绝不抛出（内部全 try/except）。返回：
    {ok, new_lessons, total_lessons, clusters, events_seen, note?}
    note 仅在不常见时出现：llm_unavailable（配置构建失败）/
    llm_failed（调用异常，watermark 不前进，下次重试）。
    """
    try:
        root_path = Path(root)
        wd = Path(work_dir) if work_dir else _default_work_dir(root_path)
        events_by_file = collect_feedback_events(root_path)
        if not events_by_file:
            return {
                "ok": True,
                "new_lessons": 0,
                "total_lessons": len(load_lessons(wd)),
                "clusters": [],
                "events_seen": 0,
            }

        watermark = load_watermark(wd)
        total_lines: dict[str, int] = {}
        new_events_by_file: dict[str, list[tuple[int, dict]]] = {}
        for path, events in events_by_file.items():
            total_lines[path] = _count_lines(path)
            seen = watermark.get(path, 0)
            new_events = [(ln, ev) for ln, ev in events if ln > seen]
            if new_events:
                new_events_by_file[path] = new_events
        events_seen = sum(len(v) for v in new_events_by_file.values())
        if not new_events_by_file:
            # 零增量：不建 LLM，直接返回
            return {
                "ok": True,
                "new_lessons": 0,
                "total_lessons": len(load_lessons(wd)),
                "clusters": [],
                "events_seen": 0,
            }

        clusters = precluster(new_events_by_file)
        if not clusters:
            return {
                "ok": True,
                "new_lessons": 0,
                "total_lessons": len(load_lessons(wd)),
                "clusters": [],
                "events_seen": events_seen,
            }

        if llm is None:
            llm = _build_distill_llm()
            if llm is None:
                return {
                    "ok": True,
                    "new_lessons": 0,
                    "total_lessons": len(load_lessons(wd)),
                    "clusters": _clusters_summary(clusters),
                    "events_seen": events_seen,
                    "note": "llm_unavailable",
                }

        try:
            resp = llm.generate(
                build_distill_messages(clusters),
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
                stream=False,
            )
        except Exception as exc:
            logger.warning("feedback_distill LLM 提炼失败，静默跳过: %s", exc)
            return {
                "ok": True,
                "new_lessons": 0,
                "total_lessons": len(load_lessons(wd)),
                "clusters": _clusters_summary(clusters),
                "events_seen": events_seen,
                "note": "llm_failed",
            }

        items = parse_distill_response(getattr(resp, "content", "") or "", clusters)

        # LLM 调用已成功（即使候选为空）：事件已处理 → 推进 watermark
        new_watermark = dict(watermark)
        for path, total in total_lines.items():
            new_watermark[path] = total
        save_watermark(wd, new_watermark)

        if not items:
            return {
                "ok": True,
                "new_lessons": 0,
                "total_lessons": len(load_lessons(wd)),
                "clusters": _clusters_summary(clusters),
                "events_seen": events_seen,
            }

        new_lessons: list[dict] = []
        for cluster, item in zip(clusters, items):
            new_lessons.append({
                "fingerprint": cluster["fingerprint"],
                "pattern": item["pattern"],
                "guidance": item["guidance"],
                "evidence_kinds": item["evidence_kinds"],
                "count": cluster["count"],
                "first_seen": cluster["first_seen"],
                "last_seen": cluster["last_seen"],
                "status": PROPOSED_STATUS,
            })
        lessons = merge_lessons(load_lessons(wd), new_lessons)
        save_lessons(wd, lessons)
        return {
            "ok": True,
            "new_lessons": len(new_lessons),
            "total_lessons": len(lessons),
            "clusters": _clusters_summary(clusters),
            "events_seen": events_seen,
        }
    except Exception as exc:  # 兜底：加工层绝不抛异常
        logger.warning("feedback_distill distill failed (best-effort): %s", exc)
        return {
            "ok": True,
            "new_lessons": 0,
            "total_lessons": 0,
            "clusters": [],
            "events_seen": 0,
            "note": "distill_error",
        }
