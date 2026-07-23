"""生成 benchmark MODIFY 任务的 fixture（"改动前"HSF 工程）。

用法（仓库根目录下）：
    python benchmark/fixtures/modify/build_fixtures.py

幂等：先清空 benchmark/fixtures/modify/<task_id>/ 再重建。
fixture 是签入仓库的基准输入；如需修改"改动前"状态，改这里再重跑。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType

FIXTURE_ROOT = PROJECT_ROOT / "benchmark" / "fixtures" / "modify"

_2D = "PROJECT2 3, 270, 2\n"


def _base_project(task_id: str) -> HSFProject:
    """书架类 fixture 的公共骨架：A/B/ZZYZX + 2d 投影。"""
    proj = HSFProject.create_new(task_id, work_dir=str(FIXTURE_ROOT))
    proj.scripts[ScriptType.SCRIPT_2D] = _2D
    return proj


def _save(proj: HSFProject) -> None:
    dest = FIXTURE_ROOT / proj.name
    if dest.exists():
        shutil.rmtree(dest)
    proj.save_to_disk()
    print(f"fixture written: {dest.relative_to(PROJECT_ROOT)}")


def build_m01() -> None:
    """M01 加层板（几何改动）：shelf_count 驱动的 FOR 循环层板书架。"""
    proj = _base_project("M01")
    proj.parameters += [
        GDLParameter("shelf_count", "Integer", "层板数量", "2"),
        GDLParameter("shelf_thk", "Length", "层板厚度", "0.018"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = (
        "! 书架：两侧板 + 顶底板 + 循环层板\n"
        "shelf_gap = (ZZYZX - shelf_thk * (shelf_count + 1)) / (shelf_count + 1)\n"
        "\n"
        "BLOCK shelf_thk, B, ZZYZX\n"
        "ADDX A - shelf_thk\n"
        "BLOCK shelf_thk, B, ZZYZX\n"
        "DEL 1\n"
        "\n"
        "BLOCK A, B, shelf_thk\n"
        "ADDZ ZZYZX - shelf_thk\n"
        "BLOCK A, B, shelf_thk\n"
        "DEL 1\n"
        "\n"
        "FOR i = 1 TO shelf_count\n"
        "    ADDZ i * (shelf_gap + shelf_thk)\n"
        "    BLOCK A - 2 * shelf_thk, B, shelf_thk\n"
        "    DEL 1\n"
        "NEXT i\n"
        "\n"
        "END\n"
    )
    _save(proj)


def build_m02() -> None:
    """M02 修编译错误：3d.gdl 故意缺一个 ENDIF（mock 编译必失败）。"""
    proj = _base_project("M02")
    proj.parameters += [
        GDLParameter("shelf_thk", "Length", "层板厚度", "0.018"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = (
        "BLOCK A, B, shelf_thk\n"
        "ADDZ ZZYZX - shelf_thk\n"
        "BLOCK A, B, shelf_thk\n"
        "DEL 1\n"
        "\n"
        "IF A > 0.4 THEN\n"
        "    ADDZ -ZZYZX / 2\n"
        "    BLOCK A, B, shelf_thk\n"
        "    DEL 1\n"
        "\n"
        "END\n"
    )
    _save(proj)


def build_m03() -> None:
    """M03 改参数默认值：shelf_thk 默认 0.018，任务要求改成 0.025。"""
    proj = _base_project("M03")
    proj.parameters += [
        GDLParameter("shelf_thk", "Length", "层板厚度", "0.018"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = (
        "BLOCK A, B, shelf_thk\n"
        "ADDZ ZZYZX - shelf_thk\n"
        "BLOCK A, B, shelf_thk\n"
        "DEL 1\n"
        "ADDZ -ZZYZX / 2\n"
        "BLOCK A - 0.036, B, shelf_thk\n"
        "DEL 1\n"
        "END\n"
    )
    _save(proj)


def build_m04() -> None:
    """M04 加材质引用：无材质的简单书架，任务要求 DEFINE MATERIAL + MATERIAL。"""
    proj = _base_project("M04")
    proj.parameters += [
        GDLParameter("shelf_thk", "Length", "层板厚度", "0.018"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = (
        "BLOCK A, B, shelf_thk\n"
        "ADDZ ZZYZX - shelf_thk\n"
        "BLOCK A, B, shelf_thk\n"
        "DEL 1\n"
        "ADDZ -ZZYZX / 2\n"
        "BLOCK A - 0.036, B, shelf_thk\n"
        "DEL 1\n"
        "END\n"
    )
    _save(proj)


def build_m05() -> None:
    """M05 跨脚本参数改名：shelf_h 被 paramlist 与 3d.gdl 共同引用。"""
    proj = _base_project("M05")
    proj.parameters += [
        GDLParameter("shelf_h", "Length", "中层板高度", "0.9"),
    ]
    proj.scripts[ScriptType.SCRIPT_3D] = (
        "BLOCK A, B, 0.018\n"
        "ADDZ shelf_h\n"
        "BLOCK A, B, 0.018\n"
        "DEL 1\n"
        "ADDZ ZZYZX - shelf_h - 0.036\n"
        "BLOCK A, B, 0.018\n"
        "DEL 1\n"
        "END\n"
    )
    _save(proj)


def main() -> None:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    build_m01()
    build_m02()
    build_m03()
    build_m04()
    build_m05()


if __name__ == "__main__":
    main()
