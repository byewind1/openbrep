"""学习状态快照：冻结一坨学习记忆，供学习效果 A/B 实验（G3）离线挂载。

为什么需要快照：学习记忆是累积态（decisions.md append、error_lessons 更新），
直接拿"活"workspace 跑 treatment 臂，prompt 会随运行漂移，录制语料不可回放。
快照把某时刻的学习状态复制成只读目录 + manifest（每文件来源路径 / sha256 /
字节数），实验期间快照本身不可变——跑前 verify、跑后重算必须一致。

快照内容（相对挂载目标的项目根，与 openbrep.learning / project_context
的落盘布局一致，任何"根目录"统一探测以下 5 个受管路径）：

    .openbrep/memory/learnings/error_lessons.jsonl   工作区错误教训（新路径）
    .openbrep/memory/skills/learned_skill.md         压缩技能（新路径）
    .openbrep/learnings/error_lessons.jsonl          legacy 错误教训
    .openbrep/learnings/learned_skill.md             legacy 压缩技能
    .openbrep/memory/decisions.md                    项目决策记忆（HF5）

capture(sources) 对每个 source 根目录探测全部受管路径：
- 存在 → 快照条目 present（多来源同路径内容一致则合并，冲突 ValueError）；
- 不存在 → 快照条目 absent。空快照（全部 absent）合法，verify 通过。

materialize(snapshot, target) 把快照状态"强加"到目标目录（fixture 副本 /
work_dir）：
- present → 复制/覆盖目标文件（目录不存在则创建）；
- absent → 删除目标中同路径文件（如 fixture 自带的 decisions.md 被快照声明
  为 absent 时必须移除，否则回放语料与录制不一致会 miss）。
materialize 只动 5 个受管路径，绝不触碰其它文件。

快照目录布局：<快照目录>/<rel_path> 原样存放文件 + manifest.json（条目按
rel_path 排序，json 序列化确定，便于复算一致性）。

AC-1 边界：本模块不 import openbrep.*（纯 hashlib/shutil/json），
tests 与 benchmark/learning_ab.py 通过本模块打交道。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1

# 受管路径（相对挂载根）。顺序即报告/遍历顺序。
MANAGED_REL_PATHS: tuple[str, ...] = (
    ".openbrep/memory/learnings/error_lessons.jsonl",
    ".openbrep/memory/skills/learned_skill.md",
    ".openbrep/learnings/error_lessons.jsonl",
    ".openbrep/learnings/learned_skill.md",
    ".openbrep/memory/decisions.md",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(data: dict) -> str:
    """确定序列化：indent=2 + sort_keys + 尾换行（manifest 重算可复现）。"""
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def capture(sources: str | Path | list[str | Path], dest: str | Path) -> Path:
    """把学习状态文件复制进快照目录并写 manifest.json。返回快照目录。

    - sources：学习状态根目录（workspace 根 / 项目根 / fixture 副本根），
      单一路径或路径列表均可，每个根目录探测全部受管路径；
    - dest：目标快照目录（已存在且非空 → 清空重建，保证快照即声明状态）；
    - 缺失文件记 absent 条目；空快照合法。
    """
    dest_dir = Path(dest)
    if isinstance(sources, (str, Path)):
        sources = [sources]
    found: dict[str, list[tuple[Path, str, int]]] = {}
    for raw in sources:
        root = Path(raw)
        for rel in MANAGED_REL_PATHS:
            candidate = root / rel
            if not candidate.is_file():
                continue
            digest = _sha256_file(candidate)
            found.setdefault(rel, []).append((candidate, digest, candidate.stat().st_size))
    entries: dict[str, dict] = {}
    for rel in MANAGED_REL_PATHS:
        candidates = found.get(rel, [])
        if not candidates:
            entries[rel] = {
                "rel_path": rel,
                "absent": True,
                "sources": [],
                "sha256": None,
                "size": None,
            }
            continue
        first_sha = candidates[0][1]
        if any(sha != first_sha for _, sha, _ in candidates[1:]):
            conflict_src = next(c[0] for c in candidates[1:] if c[1] != first_sha)
            raise ValueError(
                f"快照来源冲突：{rel} 在多个来源中内容不一致"
                f"（{candidates[0][0]} vs {conflict_src}），拒绝合并"
            )
        entries[rel] = {
            "rel_path": rel,
            "absent": False,
            "sources": [str(c[0].expanduser().resolve()) for c in candidates],
            "sha256": first_sha,
            "size": candidates[0][2],
        }
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for rel, entry in entries.items():
        if entry["absent"]:
            continue
        target = dest_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        source = next((Path(s) for s in entry["sources"] if Path(s).is_file()), None)
        if source is None:
            raise ValueError(f"快照来源文件不存在：{entry['sources']}")
        shutil.copyfile(source, target)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "entries": [
            {
                "rel_path": entry["rel_path"],
                "absent": entry["absent"],
                "sources": list(entry["sources"]),
                "sha256": entry["sha256"],
                "size": entry["size"],
            }
            for entry in entries.values()
        ],
    }
    (dest_dir / MANIFEST_NAME).write_text(_canonical_json(manifest), encoding="utf-8")
    return dest_dir


def load_manifest(snapshot_dir: str | Path) -> dict:
    path = Path(snapshot_dir) / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def verify_report(snapshot_dir: str | Path) -> tuple[bool, list[str]]:
    """重算 manifest 全部条目的一致性。返回 (ok, 问题清单)。

    - present 条目：文件存在且 sha256 / 字节数一致；
    - absent 条目：快照目录里不得出现该路径文件（被意外物化 = 不通过）。
    """
    root = Path(snapshot_dir)
    issues: list[str] = []
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return False, [f"快照缺少 {MANIFEST_NAME}：{root}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"快照 {MANIFEST_NAME} 解析失败：{exc}"]
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return False, ["快照 manifest 缺少 entries 列表"]
    for entry in entries:
        rel = str(entry.get("rel_path") or "")
        absent = bool(entry.get("absent"))
        expected_sha = entry.get("sha256")
        expected_size = entry.get("size")
        file_path = root / rel
        if absent:
            if file_path.exists():
                issues.append(f"absent 条目被物化：{rel}")
            continue
        if not file_path.is_file():
            issues.append(f"present 条目缺文件：{rel}")
            continue
        actual_sha = _sha256_file(file_path)
        if expected_sha is not None and actual_sha != expected_sha:
            issues.append(f"sha256 不一致：{rel}（manifest {expected_sha} vs 实际 {actual_sha}）")
        actual_size = file_path.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            issues.append(
                f"字节数不一致：{rel}（manifest {expected_size} vs 实际 {actual_size}）"
            )
    return (not issues), issues


def verify(snapshot_dir: str | Path) -> bool:
    ok, _ = verify_report(snapshot_dir)
    return ok


def materialize(snapshot_dir: str | Path, target_dir: str | Path) -> dict[str, list[str]]:
    """把快照状态挂载进目标目录（目录不存在则创建）。

    返回 {"written": [rel…], "removed": [rel…]}：
    - written：present 条目复制（覆盖目标同路径文件）；
    - removed：absent 条目且目标存在同路径文件 → 删除（保持实验状态与
      快照声明一致，防止回放 miss）。
    """
    target = Path(target_dir)
    result: dict[str, list[str]] = {"written": [], "removed": []}
    manifest = load_manifest(snapshot_dir)
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"快照 manifest 缺少 entries 列表：{snapshot_dir}")
    snapshot_root = Path(snapshot_dir)
    for entry in entries:
        rel = str(entry.get("rel_path") or "")
        if not rel:
            continue
        dest = target / rel
        if entry.get("absent"):
            if dest.is_file():
                dest.unlink()
                result["removed"].append(rel)
            continue
        source = snapshot_root / rel
        if not source.is_file():
            raise ValueError(f"快照缺少 present 条目文件：{source}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        result["written"].append(rel)
    return result


def _default_snapshot_dest(name: str) -> Path:
    return PROJECT_ROOT / "benchmark" / "results" / "snapshots" / name


def _cli() -> None:
    parser = argparse.ArgumentParser(description="学习状态快照：capture / verify / materialize")
    sub = parser.add_subparsers(dest="command", required=True)

    p_capture = sub.add_parser("capture", help="从学习状态根目录抓取快照")
    p_capture.add_argument(
        "--name", required=True,
        help="快照名（默认落 benchmark/results/snapshots/<name>/）",
    )
    p_capture.add_argument(
        "--source", action="append", required=True, metavar="DIR",
        help="学习状态根目录（可多次）",
    )
    p_capture.add_argument(
        "--dest", default=None,
        help="快照目标目录（默认 benchmark/results/snapshots/<name>）",
    )

    p_verify = sub.add_parser("verify", help="重算 manifest 校验快照")
    p_verify.add_argument("--snapshot", required=True, help="快照目录")

    p_materialize = sub.add_parser("materialize", help="把快照挂载进目标目录")
    p_materialize.add_argument("--snapshot", required=True, help="快照目录")
    p_materialize.add_argument(
        "--target", required=True, help="目标目录（fixture 副本 / work_dir）",
    )

    args = parser.parse_args()
    if args.command == "capture":
        dest = Path(args.dest) if args.dest else _default_snapshot_dest(args.name)
        path = capture(args.source, dest)
        ok, issues = verify_report(path)
        if not ok:
            print("快照写入后校验失败：")
            for issue in issues:
                print(f"  ✗ {issue}")
            sys.exit(1)
        print(f"快照已写入并校验通过：{path}")
        return
    if args.command == "verify":
        ok, issues = verify_report(args.snapshot)
        for issue in issues:
            print(f"  ✗ {issue}")
        print("快照一致 ✓" if ok else "快照不一致 ✗")
        sys.exit(0 if ok else 1)
    if args.command == "materialize":
        summary = materialize(args.snapshot, args.target)
        print(f"挂载完成：写入 {len(summary['written'])}，移除 {len(summary['removed'])}")
        sys.exit(0)


if __name__ == "__main__":
    _cli()
