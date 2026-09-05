"""反馈事件 → 教训候选的加工层（F2 提炼 + F3 状态机/注入查询，自我优化系统）。

输入：F1 采集的项目级反馈事件（<project_root>/.openbrep/feedback.jsonl，8 种 kind）。
加工链：确定性预聚类（按 (kind, 规范化签名) 分组计数）→ 一次 LLM 语义提炼
（温度 0 / 小 max_tokens / 非流式）→ 写入 proposed 态教训库
<work_dir>/.openbrep/memory/learnings/distilled_lessons.jsonl
（与 ErrorLearningStore 同目录、独立新文件）。

教训生命周期（F3 状态机）：
- 任何自动提炼产物一律 proposed；**过闸才 active**——晋升是人工/调用方
  显式决策（set_lesson_status），本模块没有任何自动晋升逻辑（防自我污染红线）。
- proposed → active（promote 晋升）/ proposed → rejected（reject 拒绝，永久
  不再浮现）/ active → proposed（demote 撤回）。rejected 是终态：merge_lessons
  同 fingerprint 合并只刷新 count/pattern/guidance，**status 字段不动**，
  被拒绝的教训不会被再提炼复活。

注入纪律：
- proposed / rejected 教训**不进任何 prompt**；只有 active 教训经
  build_distilled_lessons_prompt 渲染、由 learning.build_skill_prompt 的
  distilled 层注入，且仍受 pipeline include_learned_skills 闸控制
  （benchmark 关闭，生产默认开）。
- 增量提炼：watermark 按文件行数记录已处理进度，只把新事件送提炼；
  零事件/零增量 → 不建 LLM 直接返回。
- 宁缺毋滥：LLM 调用失败/超时、坏 JSON、形态非法、空候选 → 静默返回空
  （不抛异常、不影响主流程）；任何文件写入失败 best-effort。

G4 扩展（证据驱动蒸馏，AC-G4-2，不动 distill() 语义）：
distill_quality_records() 从质量账本（Reflector 选出的失败候选）提炼教训，
写同一教训库。教训带 evidence_refs [{run_id, check_type, before_revision,
after_revision}]（只引用 revision，不整存脚本），自动产物一律 proposed；
raw_excerpt 行级去 GDL 脚本 + 500 字符封顶，parse/merge 双侧强制。
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
ACTIVE_STATUS = "active"
REJECTED_STATUS = "rejected"
ALL_STATUSES: frozenset[str] = frozenset({PROPOSED_STATUS, ACTIVE_STATUS, REJECTED_STATUS})

# 状态机：decision → {from: 合法出发状态, to: 目标状态, idempotent_with: 幂等状态}
# - promote: proposed→active 晋升；已是 active → 幂等 ok（不变）
# - reject:  proposed→rejected 拒绝（永久）；已是 rejected → 幂等 ok（不变）
# - demote:  active→proposed 撤回；proposed/rejected 都不是合法出发态（报错）
_LESSON_DECISIONS: dict[str, dict[str, str | None]] = {
    "promote": {"from": PROPOSED_STATUS, "to": ACTIVE_STATUS, "idempotent_with": ACTIVE_STATUS},
    "reject": {"from": PROPOSED_STATUS, "to": REJECTED_STATUS, "idempotent_with": REJECTED_STATUS},
    "demote": {"from": ACTIVE_STATUS, "to": PROPOSED_STATUS, "idempotent_with": None},
}


# ── G4 常量与落盘文本清洗（证据蒸馏专用） ───────────────────

QUALITY_PREFIX = "quality:"        # 教训指纹前缀（与 distill: 区分）
RAW_EXCERPT_MAX_CHARS = 500        # raw_excerpt 清洗后封顶（parse/merge 双侧）
QUALITY_BATCH_SIZE = 6             # 单次 LLM 调用的候选批大小（对齐解析可控）
QUALITY_LLM_MAX_TOKENS = 2200      # 质量提炼响应预算（逐批小响应）
QUALITY_CASE_SHOWN = 12            # 单案例证据展示/可引用上限（提示与校验共用）
QUALITY_MAX_REFS = 20              # 合并后 evidence_refs 上限（确定性截断）
CASE_DETAIL_MAX_CHARS = 120        # 案例卡片 detail 展示截断

_ASCII_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _load_gdl_command_tokens() -> frozenset[str]:
    """静态命令表（openbrep/data/gdl_commands.txt，与 static_checker 同源）。

    加载失败 → 空集；赋值式语句行过滤仍在，脚本不落盘纪律不失效。
    """
    tokens: set[str] = set()
    try:
        path = Path(__file__).resolve().parent / "data" / "gdl_commands.txt"
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            tokens.add(line.upper())
    except OSError:
        pass
    return frozenset(tokens)


_GDL_COMMAND_TOKENS: frozenset[str] = _load_gdl_command_tokens()


def _is_gdl_script_line(line: str) -> bool:
    """整行判为 GDL 语句：ASCII 行且（命令开头 或 变量赋值开头）。

    含中文字符的行视为 prose（pattern/guidance 正文），一律不判脚本行。
    """
    stripped = line.strip()
    if not stripped or _has_cjk(stripped):
        return False
    if _ASCII_ASSIGN_RE.match(stripped):
        return True
    first = stripped.split(None, 1)[0].rstrip(":：").upper()
    return first in _GDL_COMMAND_TOKENS


def _clean_quality_text(text: Any, limit: int) -> str:
    """Curator 落盘文本清洗：删整行 GDL 语句、空白归一、截断上限。"""
    kept = [ln for ln in str(text or "").splitlines() if not _is_gdl_script_line(ln)]
    return re.sub(r"\s+", " ", "\n".join(kept)).strip()[:limit]


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lesson_error(code: str, message: str, details: Any = None) -> dict:
    """统一错误形态（与 mcp_tools._make_error 同构，保持模块独立）。"""
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"ok": False, "error": error}


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
    """同 fingerprint 合并：count 累加、first_seen 取最早、last_seen 取最晚、
    pattern/guidance/evidence_kinds 刷新；quality 教训（G4）的 evidence_refs
    确定性并集（按 (run_id, check_type) 去重排序，上限 QUALITY_MAX_REFS）、
    raw_excerpt 清洗封顶后取最新值。**status 字段不动**——rejected 是终态，
    被拒绝的教训不会被再提炼复活。文件顺序保持稳定（dict 保序）。
    """
    by_fp: dict[str, dict] = {lesson["fingerprint"]: lesson for lesson in existing}
    for new_lesson in new_lessons:
        fp = new_lesson["fingerprint"]
        prev = by_fp.get(fp)
        if prev is None:
            by_fp[fp] = dict(new_lesson)
            continue
        prev["count"] = int(prev.get("count") or 0) + int(new_lesson.get("count") or 0)
        prev_first = str(prev.get("first_seen") or "")
        new_first = str(new_lesson.get("first_seen") or "")
        if new_first and (not prev_first or new_first < prev_first):
            prev["first_seen"] = new_first
        new_last = str(new_lesson.get("last_seen") or "")
        if new_last and new_last > str(prev.get("last_seen") or ""):
            prev["last_seen"] = new_last
        prev["pattern"] = new_lesson["pattern"]
        prev["guidance"] = new_lesson["guidance"]
        if "evidence_kinds" in new_lesson:
            prev["evidence_kinds"] = new_lesson["evidence_kinds"]
        new_refs = new_lesson.get("evidence_refs")
        if isinstance(new_refs, list) and new_refs:
            prev_refs = prev.get("evidence_refs")
            prev["evidence_refs"] = _merge_evidence_refs(
                prev_refs if isinstance(prev_refs, list) else [], new_refs
            )
        raw = new_lesson.get("raw_excerpt")
        if isinstance(raw, str) and raw.strip():
            prev["raw_excerpt"] = _clean_quality_text(raw, RAW_EXCERPT_MAX_CHARS)
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


# ── 4.5 状态机与注入查询（F3：晋升/拒绝/撤回 + active 注入视图） ──

def set_lesson_status(
    work_dir: str | Path,
    fingerprint: str,
    decision: str,
) -> dict:
    """状态机迁移：proposed→active（promote）/ proposed→rejected（reject）/
    active→proposed（demote）。

    幂等：promote 一个已 active / reject 一个已 rejected → ok 但不变。
    非法迁移（如 rejected→active、demote 一个 proposed）→ 返回错误（不静默）；
    未知 fingerprint / 非法 decision / 写盘失败 → 返回错误。
    迁移成功写 status_changed_at（ISO 秒级），写盘走 save_lessons（原子）。

    返回 {ok, fingerprint, decision, status, changed} 或
    {ok: False, error: {code, message, details?}}。
    """
    if decision not in _LESSON_DECISIONS:
        return _lesson_error(
            "invalid_decision",
            f"非法 decision: {decision!r}（可选: promote / reject / demote）",
            details={"decision": decision},
        )
    lessons = load_lessons(work_dir)
    target = next((lesson for lesson in lessons if lesson.get("fingerprint") == fingerprint), None)
    if target is None:
        return _lesson_error(
            "lesson_not_found",
            f"教训不存在: {fingerprint}",
            details={"fingerprint": fingerprint},
        )
    current = str(target.get("status") or PROPOSED_STATUS)
    spec = _LESSON_DECISIONS[decision]
    target_status = str(spec["to"])
    if current == target_status and spec["idempotent_with"] == current:
        # 幂等：状态已是该 decision 的结果状态，ok 但不变
        return {
            "ok": True,
            "fingerprint": fingerprint,
            "decision": decision,
            "status": current,
            "changed": False,
        }
    if current != spec["from"]:
        return _lesson_error(
            "invalid_transition",
            f"非法迁移: {current} --{decision}--> {target_status}",
            details={
                "fingerprint": fingerprint,
                "decision": decision,
                "from": current,
                "to": target_status,
            },
        )
    target["status"] = target_status
    target["status_changed_at"] = _now_iso()
    if not save_lessons(work_dir, lessons):
        return _lesson_error("write_failed", "教训库写盘失败")
    return {
        "ok": True,
        "fingerprint": fingerprint,
        "decision": decision,
        "status": target_status,
        "changed": True,
    }


def list_lessons_view(
    work_dir: str | Path,
    status: str | None = None,
) -> list[dict]:
    """读库过滤返回视图行（不含样本等内部字段）。

    每条 {fingerprint, pattern, guidance, evidence_kinds, count, status,
    first_seen, last_seen}；按 (status, -count, last_seen) 稳定排序。
    status 非法值 → 返回空列表（由 MCP 层做参数校验）。
    """
    lessons = load_lessons(work_dir)
    if status is not None:
        lessons = [
            lesson for lesson in lessons
            if (lesson.get("status") or PROPOSED_STATUS) == status
        ]
    lessons.sort(
        key=lambda lesson: (
            str(lesson.get("status") or PROPOSED_STATUS),
            -int(lesson.get("count") or 0),
            str(lesson.get("last_seen") or ""),
        )
    )
    fields = (
        "fingerprint", "pattern", "guidance", "evidence_kinds",
        "count", "status", "first_seen", "last_seen",
    )
    return [{field: lesson.get(field) for field in fields} for lesson in lessons]


def build_distilled_lessons_prompt(work_dir: str | Path, limit: int = 8) -> str:
    """渲染 active 教训为注入用 prompt 层文本（learning.build_skill_prompt 的
    distilled 层消费）。

    只取 active；按 (-count, last_seen) 排序取前 limit 条；每条渲染为
    "- <pattern>：<guidance>"。零 active / 文件缺失 / 任何异常 → 返回 ""
    （best-effort，不抛）。proposed / rejected 一律不进。
    """
    try:
        lessons = [
            lesson for lesson in load_lessons(work_dir)
            if (lesson.get("status") or PROPOSED_STATUS) == ACTIVE_STATUS
        ]
    except Exception:
        return ""
    if not lessons:
        return ""
    lessons.sort(
        key=lambda lesson: (
            -int(lesson.get("count") or 0),
            str(lesson.get("last_seen") or ""),
        )
    )
    selected = lessons[: max(0, int(limit))]
    lines = [
        f"- {str(lesson.get('pattern') or '').strip()}：{str(lesson.get('guidance') or '').strip()}"
        for lesson in selected
        if (lesson.get("pattern") or "").strip()
    ]
    return "\n".join(lines)


# ── 4.6 G4 Curator：证据驱动蒸馏（quality ledger → lessons） ──

def candidate_ref_targets(candidate: dict) -> dict[str, list[str]]:
    """候选的可引用证据索引 {check_type: [detail…]}。

    案例卡展示与 parse 校验共用同一口径：来源顺序 = 反射器证据顺序
    （check_failures 先、issues 后，确定），只取前 QUALITY_CASE_SHOWN 条，
    detail 截断 CASE_DETAIL_MAX_CHARS。
    """
    evidence = candidate.get("evidence")
    targets: dict[str, list[str]] = {}
    if not isinstance(evidence, dict):
        return targets
    entries: list[tuple[str, str]] = []
    for key in ("check_failures", "issues"):
        raw = evidence.get(key)
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            ct = str(entry.get("check_type") or "").strip()
            if ct:
                detail = str(entry.get("detail") or "")
                entries.append((ct, detail[:CASE_DETAIL_MAX_CHARS]))
    for ct, detail in entries[:QUALITY_CASE_SHOWN]:
        targets.setdefault(ct, []).append(detail)
    return targets


def quality_lesson_fingerprint(candidate: dict) -> str:
    """候选 → 教训指纹（预 LLM、跨 run 稳定、不依赖输出文本）。

    输入 = 意图 + outcome + 归一化证据（check_type + 清洗后 detail）。
    同失败模式的不同 run（不同 run_id）产生同一指纹 → 跨 run 合并为一条
    教训（count 累加、evidence_refs 并集）。
    """
    parts = [str(candidate.get("intent") or ""), str(candidate.get("outcome") or "")]
    targets = candidate_ref_targets(candidate)
    for ct in sorted(targets):
        for detail in targets[ct]:
            parts.append(f"{ct}:{_normalize_signature(detail)}")
    digest = hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{QUALITY_PREFIX}{digest}"


def _candidate_revisions(candidate: dict) -> dict[str, Any]:
    revisions = candidate.get("revisions")
    if isinstance(revisions, dict):
        return {
            "before_revision": revisions.get("before_revision") or None,
            "after_revision": revisions.get("after_revision") or None,
        }
    return {"before_revision": None, "after_revision": None}


def _raw_excerpt_for(refs_by_ct: dict[str, dict], targets: dict[str, list[str]]) -> str:
    """raw_excerpt = 被引用 check 的 detail 摘录（解释器装配，绝不来自 LLM）。

    按案例卡顺序取被引用 check_type 的 detail，去重拼接，清洗 + 500 封顶。
    """
    parts: list[str] = []
    for ct, details in targets.items():
        if ct not in refs_by_ct:
            continue
        for detail in details:
            cleaned = str(detail or "").strip()
            if cleaned and cleaned not in parts:
                parts.append(cleaned)
    if not parts:
        return ""
    return _clean_quality_text("；".join(parts), RAW_EXCERPT_MAX_CHARS)


def build_quality_distill_messages(candidates: list[dict]) -> list[dict]:
    """构造质量提炼消息：反思案例卡 + 证据引用契约。

    案例卡只呈现 run_id / 项目 / 意图 / 指令摘要（≤120 既定隐私上限）/
    outcome / [check_type] detail 证据行 / 对照 run。行首 [check_type]
    标记即 parse 校验的允许引用集（与 candidate_ref_targets 同源）。
    """
    rules = [
        "你是 GDL 质量教训提炼器（Curator）。输入若干反思案例，每个案例来自一条",
        "质量账本记录（失败终态或实测质量轴有问题，可带同类已完成对照）。为每个",
        "案例提炼一条教训候选；不足以形成教训的案例该位置输出 null（宁缺毋滥）。",
        "只输出一个 JSON 数组，长度与案例数一致、顺序一致，不要 Markdown 标记。",
        '每项：{"pattern": "<模式描述，中文，≤100 字>",',
        ' "guidance": "<可执行修复/预防指引，中文，≤300 字>",',
        ' "evidence_refs": [{"run_id": "<案例卡 run_id 原样>",',
        ' "check_type": "<案例卡 [标记] 原样>"}]}',
        "硬性纪律：",
        "- 只依据本案例卡内容，禁止编造卡片外的检查/运行；",
        "- evidence_refs 至少 1 条；run_id/check_type 必须逐字抄自本案例卡；",
        "- guidance 不得整行写 GDL 代码（BLOCK/ADDZ/FOR/NEXT 等语句行会被删除），",
        "  用中文叙述修法与预防步骤。",
    ]
    lines: list[str] = []
    for index, cand in enumerate(candidates):
        lines.append(f"[案例 {index}]")
        lines.append(f"run_id: {cand.get('run_id') or ''}")
        lines.append(f"project: {cand.get('project_name') or ''}")
        lines.append(f"intent: {cand.get('intent') or 'unknown'}")
        lines.append(f"outcome: {cand.get('outcome') or ''}")
        summary = str(cand.get("instruction_summary") or "").replace("\n", " ")
        if summary:
            lines.append(f"指令摘要: {summary}")
        for ct, details in candidate_ref_targets(cand).items():
            for detail in details:
                lines.append(f"- [{ct}] {detail or ''}")
        contrast = cand.get("contrast_run_id")
        if contrast:
            lines.append(f"对照(同类已完成零问题): {contrast}")
        else:
            lines.append("对照: 无")
        lines.append("")
    return [
        {"role": "system", "content": "\n".join(rules)},
        {"role": "user", "content": "\n".join(lines).strip()},
    ]


def parse_quality_distill_response(
    text: Any, candidates: list[dict]
) -> Optional[tuple[list[dict], int]]:
    """严格解析质量提炼结果（与 candidates 一一对应）。

    - 结构坏响应（非数组 / 长度不符）→ None（整批判废，调用方静默，同
      distill() 语义；不算 rejected，因为无法逐条归因）；
    - item = null → LLM 显式「该案例无教训」，静默跳过（不计 rejected）；
    - 硬校验（判定靠解释器）：非 dict / pattern|guidance 缺失或空 /
      evidence_refs 缺失或空 / 任一 ref 元素缺 run_id|check_type /
      ref 语义不匹配（run_id ≠ 本案例 run_id 或 check_type 不在
      candidate_ref_targets）→ 整条丢弃，rejected + 1；
    - 通过校验后由解释器注入 before/after revision、装配 raw_excerpt
      （只引用案例证据 detail，绝不采信 LLM 文本），status 一律 proposed。
    """
    data = _extract_json(text)
    if not isinstance(data, list) or len(data) != len(candidates):
        return None
    lessons: list[dict] = []
    rejected = 0
    for item, candidate in zip(data, candidates):
        if item is None:
            continue
        if not isinstance(item, dict):
            rejected += 1
            continue
        pattern = item.get("pattern")
        guidance = item.get("guidance")
        if not isinstance(pattern, str) or not pattern.strip():
            rejected += 1
            continue
        if not isinstance(guidance, str) or not guidance.strip():
            rejected += 1
            continue
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            rejected += 1
            continue
        run_id = str(candidate.get("run_id") or "")
        targets = candidate_ref_targets(candidate)
        revisions = _candidate_revisions(candidate)
        valid: list[dict[str, Any]] = []
        for ref in refs:
            if not isinstance(ref, dict):
                valid = []
                break
            rid = ref.get("run_id")
            check_type = ref.get("check_type")
            ok_shape = (
                isinstance(rid, str) and bool(rid)
                and isinstance(check_type, str) and bool(check_type)
            )
            if not ok_shape or rid != run_id or check_type not in targets:
                valid = []
                break
            valid.append({
                "run_id": rid,
                "check_type": check_type,
                "before_revision": revisions["before_revision"],
                "after_revision": revisions["after_revision"],
            })
        if not valid:
            rejected += 1
            continue
        refs_by_ct: dict[str, dict] = {}
        for ref in valid:
            refs_by_ct.setdefault(ref["check_type"], ref)
        refs_sorted = [refs_by_ct[ct] for ct in sorted(refs_by_ct)]
        excerpt = _raw_excerpt_for(refs_by_ct, targets)
        lesson = {
            "fingerprint": quality_lesson_fingerprint(candidate),
            "pattern": _clean_quality_text(pattern, PATTERN_MAX_CHARS),
            "guidance": _clean_quality_text(guidance, GUIDANCE_MAX_CHARS),
            "evidence_refs": refs_sorted,
            "count": 1,
            "first_seen": str(candidate.get("ts") or ""),
            "last_seen": str(candidate.get("ts") or ""),
            "status": PROPOSED_STATUS,
        }
        if excerpt:
            lesson["raw_excerpt"] = excerpt
        lessons.append(lesson)
    return lessons, rejected


def _merge_evidence_refs(prev: list[Any], new: list[Any]) -> list[dict[str, Any]]:
    """evidence_refs 确定性并集：按 (run_id, check_type) 去重排序，上限截断。"""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for ref in [*prev, *new]:
        if not isinstance(ref, dict):
            continue
        rid = ref.get("run_id")
        ct = ref.get("check_type")
        if not isinstance(rid, str) or not rid or not isinstance(ct, str) or not ct:
            continue
        merged.setdefault((rid, ct), {
            "run_id": rid,
            "check_type": ct,
            "before_revision": ref.get("before_revision") or None,
            "after_revision": ref.get("after_revision") or None,
        })
    return [merged[key] for key in sorted(merged)][:QUALITY_MAX_REFS]


def lesson_cards_view(
    work_dir: str | Path,
    status: str | None = None,
) -> list[dict]:
    """GUI 确认卡视图：list_lessons_view 的扩展（带 evidence_refs/raw_excerpt）。

    MCP 消费的 list_lessons_view（8 固定字段）保持不变；本视图供 memory
    service 确认卡渲染。排序同 list_lessons_view：(status, -count, last_seen)。
    旧格式 lesson（无 evidence_refs）字段缺省返回 None，可读可渲染。
    """
    lessons = load_lessons(work_dir)
    if status is not None:
        lessons = [
            lesson for lesson in lessons
            if (lesson.get("status") or PROPOSED_STATUS) == status
        ]
    lessons.sort(
        key=lambda lesson: (
            str(lesson.get("status") or PROPOSED_STATUS),
            -int(lesson.get("count") or 0),
            str(lesson.get("last_seen") or ""),
        )
    )
    fields = (
        "fingerprint", "pattern", "guidance", "status", "count",
        "first_seen", "last_seen", "evidence_refs", "raw_excerpt",
    )
    return [{field: lesson.get(field) for field in fields} for lesson in lessons]


def distill_quality_records(
    work_dir: str | Path,
    scan_root: str | Path | None = None,
    llm: Any = None,
) -> dict:
    """G4 Curator 主入口：Reflector 候选 → LLM 提炼 → merge 进同一教训库。

    与 distill() 的差异：输入是质量账本（scan_root，缺省 = work_dir）而非
    feedback.jsonl；watermark 是已处理 run_id 集合（reflector_watermark.json，
    原子写），逐批在 LLM 调用成功后推进；产物带 evidence_refs 引用，自动
    产物一律 proposed。绝不抛异常。

    返回 {ok, new_lessons, total_lessons, rejected, note?}；note 仅在
    llm_unavailable / llm_failed / parse_failed 时出现。
    """
    try:
        wd = Path(work_dir)
        root = Path(scan_root) if scan_root is not None else wd
        # 函数级 import（G3 教训：避免测试 top-level import 引发 isort 漂移）
        from openbrep.quality import reflector

        watermark = reflector.load_watermark(wd)
        candidates, _ = reflector.select_reflection_candidates(root, watermark)
        if not candidates:
            return {
                "ok": True,
                "new_lessons": 0,
                "total_lessons": len(load_lessons(wd)),
                "rejected": 0,
            }
        if llm is None:
            llm = _build_distill_llm()
            if llm is None:
                return {
                    "ok": True,
                    "new_lessons": 0,
                    "total_lessons": len(load_lessons(wd)),
                    "rejected": 0,
                    "note": "llm_unavailable",
                }
        lessons = load_lessons(wd)
        rejected = 0
        new_count = 0
        note: Optional[str] = None
        done_ids: list[str] = []
        batches = [
            candidates[offset:offset + QUALITY_BATCH_SIZE]
            for offset in range(0, len(candidates), QUALITY_BATCH_SIZE)
        ]
        for batch in batches:
            try:
                resp = llm.generate(
                    build_quality_distill_messages(batch),
                    temperature=LLM_TEMPERATURE,
                    max_tokens=QUALITY_LLM_MAX_TOKENS,
                    stream=False,
                )
            except Exception as exc:
                logger.warning(
                    "feedback_distill 质量提炼批失败（本批下次重试）: %s", exc
                )
                note = "llm_failed"
                break
            done_ids.extend(str(c.get("run_id") or "") for c in batch)
            parsed = parse_quality_distill_response(
                getattr(resp, "content", "") or "", batch
            )
            if parsed is None:
                note = note or "parse_failed"
                continue
            batch_lessons, batch_rejected = parsed
            rejected += batch_rejected
            if batch_lessons:
                lessons = merge_lessons(lessons, batch_lessons)
                new_count += len(batch_lessons)
        if done_ids:
            processed: set[str] = set()
            raw_ids = watermark.get(reflector.WATERMARK_RUN_IDS_KEY)
            if isinstance(raw_ids, list):
                processed = {str(item) for item in raw_ids if isinstance(item, str)}
            reflector.save_watermark(wd, {
                reflector.WATERMARK_RUN_IDS_KEY: sorted(processed | set(done_ids)),
            })
        if new_count:
            save_lessons(wd, lessons)
        result = {
            "ok": True,
            "new_lessons": new_count,
            "total_lessons": len(lessons),
            "rejected": rejected,
        }
        if note:
            result["note"] = note
        return result
    except Exception as exc:  # 兜底：加工层绝不抛异常
        logger.warning(
            "feedback_distill distill_quality_records failed (best-effort): %s", exc
        )
        return {
            "ok": True,
            "new_lessons": 0,
            "total_lessons": 0,
            "rejected": 0,
            "note": "distill_error",
        }


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
