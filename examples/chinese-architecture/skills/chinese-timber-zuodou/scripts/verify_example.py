"""Verify this example with OpenBrep; optionally convert a temporary HSF copy.

Run from the OpenBrep checkout with PYTHONPATH=. or with openbrep installed.
No live project is edited and no LLM is called. JSON is written to stdout.
"""

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from pathlib import Path

from openbrep.compiler import HSFCompiler
from openbrep.gdl_previewer import preview_3d_script
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.skills_loader import SkillsLoader
from openbrep.static_checker import StaticChecker
from openbrep.workbench.project_parameter_service import parameter_values


def bounds(m):
    return [(min(getattr(m, k)), max(getattr(m, k))) for k in ("x", "y", "z")]


def near(a, b):
    assert math.isclose(a, b, abs_tol=1e-9), (a, b)


def section(m, z):
    coords = []
    pts = list(zip(m.x, m.y, m.z))
    for i, j, k in zip(m.i, m.j, m.k):
        for a, b in ((i, j), (j, k), (k, i)):
            u, v = pts[a], pts[b]
            if min(u[2], v[2]) <= z <= max(u[2], v[2]) and abs(u[2] - v[2]) > 1e-12:
                t = (z - u[2]) / (v[2] - u[2])
                coords.append([u[c] + t * (v[c] - u[c]) for c in (0, 1)])
    return [max(q[c] for q in coords) - min(q[c] for q in coords) for c in (0, 1)]


def main():
    base = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compile", action="store_true", help="Require an installed LP_XMLConverter"
    )
    args = parser.parse_args()
    root = base / "assets/zuodou"
    p = HSFProject.load_from_disk(str(root))
    static = StaticChecker().check(p)
    assert static.passed, static.errors
    report = {
        "static_passed": True,
        "static_warnings": [str(w) for w in static.warnings],
        "scenarios": [],
    }
    for width, depth, height in ((0.396, 0.396, 0.3), (0.6, 0.4, 0.45), (0.2, 0.3, 0.15)):
        r = preview_3d_script(
            p.get_script(ScriptType.SCRIPT_3D),
            parameters=parameter_values(p, {"A": width, "B": depth, "ZZYZX": height}),
            setup_script=p.get_script(ScriptType.MASTER),
        )
        assert not r.warnings, r.warnings
        assert len(r.meshes) == 8
        lower, body, *rest = r.meshes
        ears = rest[:4]
        front, back = rest[-2:]
        overall = [
            (min(min(getattr(m, k)) for m in r.meshes), max(max(getattr(m, k)) for m in r.meshes))
            for k in ("x", "y", "z")
        ]
        for actual, target in zip(
            overall, ((-width / 2, width / 2), (-depth / 2, depth / 2), (0, height))
        ):
            for a, b in zip(actual, target):
                near(a, b)
        for fraction in (0.25, 0.5, 0.75):
            sw, sd = section(lower, height * 0.4 * fraction)
            near(sw, width * (17 / 22 + (1 - 17 / 22) * fraction))
            near(sd, depth * (17 / 22 + (1 - 17 / 22) * fraction))
        normals = set()
        for i, j, k in zip(lower.i, lower.j, lower.k):
            a = [getattr(lower, c)[j] - getattr(lower, c)[i] for c in ("x", "y", "z")]
            b = [getattr(lower, c)[k] - getattr(lower, c)[i] for c in ("x", "y", "z")]
            n = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
            norm = math.sqrt(sum(x * x for x in n))
            if norm > 1e-12 and 1e-6 < abs(n[2] / norm) < 1 - 1e-6:
                normals.add(tuple(round(x / norm, 6) for x in n))
        assert len(normals) == 4, normals
        near(max(lower.z), min(body.z))
        for c in ("x", "y"):
            near(min(getattr(lower, c)), min(getattr(body, c)))
            near(max(getattr(lower, c)), max(getattr(body, c)))
        for m in ears + [front, back]:
            near(min(m.z), max(body.z))
        for rail in (front, back):
            near(min(rail.x), -width * 15 / 88)
            near(max(rail.x), width * 15 / 88)
            near(max(rail.z) - min(rail.z), height * 0.38 / 3)
            near(max(rail.y) - min(rail.y), (depth - depth * 15 / 44) / 4)
        near(max(front.y), min(back.y) * -1)
        near(max(front.y), -depth * 15 / 88)
        near(min(back.y), depth * 15 / 88)
        near(max(ears[0].x), min(front.x))
        near(min(ears[1].x), max(front.x))
        near(max(ears[0].y), max(front.y))
        near(max(ears[1].y), max(front.y))
        near(max(ears[2].x), min(back.x))
        near(min(ears[3].x), max(back.x))
        near(min(ears[2].y), min(back.y))
        near(min(ears[3].y), min(back.y))
        report["scenarios"].append(
            {
                "A": width,
                "B": depth,
                "ZZYZX": height,
                "meshes": 8,
                "sloped_sides": 4,
                "continuous_sections": True,
                "inner_alignment": True,
                "rails_bounds": [bounds(front), bounds(back)],
            }
        )
    with tempfile.TemporaryDirectory(prefix="zuodou-skill-") as td:
        temp = Path(td)
        skilldir = temp / "skills"
        skilldir.mkdir()
        shutil.copy2(base / "SKILL.md", skilldir / "chinese-timber-zuodou.md")
        loader = SkillsLoader(str(skilldir))
        injected = loader.get_for_task("修改坐斗，把两条低连接块与斗耳内侧对齐")
        assert "chinese-timber-zuodou" in loader.last_injected
        assert "_ear_y - _rail_d" in injected
        report["project_skill_injected"] = loader.last_injected
        if args.compile:
            copied = temp / "zuodou"
            shutil.copytree(root, copied, ignore=shutil.ignore_patterns(".openbrep"))
            c = HSFCompiler().hsf2libpart(str(copied), str(temp / "zuodou.gsm"))
            report["compile"] = {
                "success": c.success,
                "exit_code": c.exit_code,
                "stdout": c.stdout,
                "stderr": c.stderr,
                "gsm_bytes": (temp / "zuodou.gsm").stat().st_size if c.success else 0,
            }
            assert c.success, report["compile"]
        else:
            report["compile"] = {"status": "not_run"}
    report["asset_sha256"] = {
        str(f.relative_to(base)): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted([*root.glob("*.xml"), *root.glob("scripts/*.gdl")])
        if f.is_file()
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
