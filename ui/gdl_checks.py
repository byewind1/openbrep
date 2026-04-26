from __future__ import annotations

import re


def check_gdl_script(content: str, script_type: str = "") -> list:
    issues = []
    if not content.strip():
        if script_type == "2d":
            issues.append("⚠️ 2D 脚本为空，必须至少包含 PROJECT2 3, 270, 2")
        return issues

    lines = content.splitlines()

    if_multi = sum(
        1 for line in lines
        if re.search(r"\bIF\b", line, re.I)
        and re.search(r"\bTHEN\s*$", line.strip(), re.I)
    )
    endif_count = sum(1 for line in lines if re.match(r"\s*ENDIF\b", line, re.I))
    if if_multi != endif_count:
        issues.append(f"⚠️ IF/ENDIF 不匹配：{if_multi} 个多行 IF，{endif_count} 个 ENDIF")

    for_count = sum(1 for line in lines if re.match(r"\s*FOR\b", line, re.I))
    next_count = sum(1 for line in lines if re.match(r"\s*NEXT\b", line, re.I))
    if for_count != next_count:
        issues.append(f"⚠️ FOR/NEXT 不匹配：{for_count} 个 FOR，{next_count} 个 NEXT")

    add_count = sum(1 for line in lines if re.match(r"\s*ADD(X|Y|Z)?\b", line, re.I))
    del_count = sum(1 for line in lines if re.match(r"\s*DEL\b", line, re.I))
    if add_count != del_count:
        issues.append(f"⚠️ ADD/DEL 不匹配：{add_count} 个 ADD/ADDX/ADDY/ADDZ，{del_count} 个 DEL")

    if any(line.strip().startswith("```") for line in lines):
        issues.append("⚠️ 脚本含有 ``` 标记 — AI 格式化残留，请删除所有反引号行")

    if script_type == "3d":
        _check_3d_termination(lines, issues)

    if script_type == "2d":
        has_proj = any(
            re.search(r"\bPROJECT2\b|\bRECT2\b|\bPOLY2\b", line, re.I)
            for line in lines
        )
        if not has_proj:
            issues.append("⚠️ 2D 脚本缺少平面投影语句（PROJECT2 / RECT2）")

    assigned = set(re.findall(r"\b(_[A-Za-z]\w*)\s*=", content))
    used = set(re.findall(r"\b(_[A-Za-z]\w*)\b", content))
    undefined = used - assigned
    if undefined:
        issues.append(
            f"ℹ️ 变量 {', '.join(sorted(undefined))} 在本脚本未赋值 — "
            "若已在 Master 脚本中定义可忽略，否则会导致 ArchiCAD 运行时不显示"
        )

    if not issues:
        issues = ["✅ 检查通过"]
    return issues


def _check_3d_termination(lines: list[str], issues: list[str]) -> None:
    sub_label_pat = re.compile(r'^\s*"[^"]+"\s*:')
    has_subs = any(sub_label_pat.match(line) for line in lines)

    if not has_subs:
        last_non_empty = next((line.strip() for line in reversed(lines) if line.strip()), "")
        if not re.match(r"^END\s*$", last_non_empty, re.I):
            issues.append("⚠️ 3D 脚本最后一行必须是 END")
        return

    main_body = []
    for line in lines:
        if sub_label_pat.match(line):
            break
        main_body.append(line)
    last_main = next((line.strip() for line in reversed(main_body) if line.strip()), "")
    if not re.match(r"^END\s*$", last_main, re.I):
        issues.append("⚠️ 3D 主体部分（第一个子程序之前）最后一行必须是 END")

    current_sub = None
    sub_lines: list[str] = []
    for line in lines:
        if sub_label_pat.match(line):
            if current_sub and sub_lines:
                last_sub = next((item.strip() for item in reversed(sub_lines) if item.strip()), "")
                if not re.match(r"^RETURN\s*$", last_sub, re.I):
                    issues.append(f"⚠️ 子程序 {current_sub} 末尾应为 RETURN，不是 END")
            current_sub = line.strip()
            sub_lines = []
        else:
            sub_lines.append(line)

    if current_sub and sub_lines:
        last_sub = next((item.strip() for item in reversed(sub_lines) if item.strip()), "")
        if not re.match(r"^RETURN\s*$", last_sub, re.I):
            issues.append(f"⚠️ 子程序 {current_sub} 末尾应为 RETURN")
