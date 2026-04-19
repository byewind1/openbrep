from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


def do_compile(
    proj,
    gsm_name: str,
    instruction: str = "",
    *,
    session_state,
    safe_compile_revision_fn,
    versioned_gsm_path_fn,
    get_compiler_fn,
    compiler_mode: str,
) -> tuple:
    try:
        requested_rev = int(session_state.get("script_revision", 0)) or 1
        compile_rev = safe_compile_revision_fn(gsm_name or proj.name, session_state.work_dir, requested_rev)
        if compile_rev != requested_rev:
            session_state.script_revision = compile_rev
        output_gsm = versioned_gsm_path_fn(gsm_name or proj.name, session_state.work_dir, revision=compile_rev)
        hsf_dir = proj.save_to_disk()
        result = get_compiler_fn().hsf2libpart(str(hsf_dir), output_gsm)
        mock_tag = " [Mock]" if compiler_mode.startswith("Mock") else ""

        if result.success:
            session_state.compile_log.append({
                "project": proj.name,
                "instruction": instruction,
                "success": True,
                "attempts": 1,
                "message": "Success",
            })
            msg = f"✅ **编译成功{mock_tag}**\n\n📦 `{output_gsm}`"
            if compiler_mode.startswith("Mock"):
                msg += "\n\n⚠️ Mock 模式不生成真实 .gsm，切换 LP_XMLConverter 进行真实编译。"
            return True, msg

        session_state.compile_log.append({
            "project": proj.name,
            "instruction": instruction,
            "success": False,
            "attempts": 1,
            "message": result.stderr,
        })
        return False, f"❌ **编译失败**\n\n```\n{result.stderr[:500]}\n```"
    except Exception as e:
        return False, f"❌ **错误**: {str(e)}"


def import_gsm(
    gsm_bytes: bytes,
    filename: str,
    *,
    get_compiler_fn,
    mock_compiler_class,
    work_dir: str,
) -> tuple:
    compiler = get_compiler_fn()

    if isinstance(compiler, mock_compiler_class):
        return None, "❌ GSM 导入需要 LP_XMLConverter，Mock 模式不支持。请在侧边栏选择 LP 模式并指定路径。"

    bin_path = compiler.converter_path or "(未检测到)"
    if not compiler.is_available:
        return (
            None,
            f"❌ LP_XMLConverter 未找到\n\n"
            f"检测路径: `{bin_path}`\n\n"
            f"macOS 正确路径示例:\n"
            f"`/Applications/GRAPHISOFT/ArchiCAD 28/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter`\n\n"
            f"请在侧边栏手动填写正确路径。",
        )

    tmp = Path(tempfile.mkdtemp())
    gsm_path = tmp / filename
    gsm_path.write_bytes(gsm_bytes)
    hsf_out = tmp / "hsf_out"
    hsf_out.mkdir()

    result = compiler.libpart2hsf(str(gsm_path), str(hsf_out))

    if not result.success:
        diag = result.stderr or result.stdout or "(无输出)"
        shutil.rmtree(tmp, ignore_errors=True)
        return (
            None,
            f"❌ GSM 解包失败 (exit={result.exit_code})\n\n"
            f"**Binary**: `{bin_path}`\n\n"
            f"**输出**:\n```\n{diag[:800]}\n```",
        )

    try:
        def _find_hsf_root(base: Path) -> Path:
            if (base / "libpartdata.xml").exists():
                return base
            if (base / "scripts").is_dir():
                return base
            for d in sorted(base.iterdir()):
                if d.is_dir() and (d / "libpartdata.xml").exists():
                    return d
            for d in sorted(base.iterdir()):
                if d.is_dir() and (d / "scripts").is_dir():
                    return d
            subdirs = [d for d in base.iterdir() if d.is_dir()]
            return subdirs[0] if subdirs else base

        hsf_dir = _find_hsf_root(hsf_out)

        if not hsf_dir.exists():
            contents = list(hsf_out.iterdir())
            shutil.rmtree(tmp, ignore_errors=True)
            return (
                None,
                f"❌ 无法定位 HSF 根目录\n\n"
                f"hsf_out 内容: `{[str(c.name) for c in contents]}`\n\n"
                f"stdout: {result.stdout[:300]}\nstderr: {result.stderr[:300]}",
            )

        gsm_stem = Path(filename).stem
        project_dir = Path(work_dir) / gsm_stem
        suffix = 1
        while project_dir.exists():
            suffix += 1
            project_dir = Path(work_dir) / f"{gsm_stem}_imported_{suffix}"
        shutil.copytree(hsf_dir, project_dir)

        from openbrep.hsf_project import HSFProject

        hsf_files = sorted(str(p.relative_to(project_dir)) for p in project_dir.rglob("*") if p.is_file())
        loaded_proj = HSFProject.load_from_disk(str(project_dir))
        scripts_found = [s.value for s in loaded_proj.scripts]
        diag = (
            f"\n\n**HSF 文件列表**: `{hsf_files}`"
            f"\n**已识别脚本**: `{scripts_found}`"
        )
        return (
            project_dir,
            f"✅ 已导入 `{loaded_proj.name}` — {len(loaded_proj.parameters)} 参数，{len(loaded_proj.scripts)} 脚本{diag}",
        )
    except Exception as e:
        return None, f"❌ HSF 解析失败: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def handle_hsf_directory_load(
    project_dir: str,
    *,
    normalize_pasted_path_fn,
    load_project_from_disk_fn,
    finalize_loaded_project_fn,
) -> tuple[bool, str]:
    raw_path = normalize_pasted_path_fn(project_dir)
    if not raw_path:
        return False, "❌ 请输入 HSF 项目目录"

    hsf_dir = Path(raw_path).expanduser()
    if not hsf_dir.exists():
        return False, f"❌ 目录不存在: {hsf_dir}"
    if not hsf_dir.is_dir():
        return False, f"❌ 不是目录: {hsf_dir}"

    try:
        proj = load_project_from_disk_fn(str(hsf_dir))
    except Exception as e:
        return False, f"❌ 载入 HSF 项目失败: {e}"

    msg = f"✅ 已加载 HSF 项目 `{proj.name}` — {len(proj.parameters)} 参数，{len(proj.scripts)} 脚本"
    return finalize_loaded_project_fn(proj, msg, pending_gsm_name=proj.name)


def handle_unified_import(
    uploaded_file,
    *,
    import_gsm_fn,
    parse_gdl_source_fn,
    derive_gsm_name_from_filename_fn,
    finalize_loaded_project_fn,
) -> tuple[bool, str]:
    fname = uploaded_file.name
    ext = Path(fname).suffix.lower()

    if ext == ".gsm":
        imported_project, msg = import_gsm_fn(uploaded_file.read(), fname)
        if not imported_project:
            return False, msg
        if isinstance(imported_project, (str, Path)):
            from openbrep.hsf_project import HSFProject

            proj = HSFProject.load_from_disk(str(imported_project))
        else:
            proj = imported_project
    else:
        try:
            content = uploaded_file.read().decode("utf-8", errors="replace")
            proj = parse_gdl_source_fn(content, Path(fname).stem)
        except Exception as e:
            return False, f"❌ 导入失败: {e}"
        msg = f"✅ 已导入 GDL `{proj.name}` — {len(proj.parameters)} 参数，{len(proj.scripts)} 脚本"

    import_gsm_name = derive_gsm_name_from_filename_fn(fname) or proj.name
    if ext == ".gsm":
        proj.save_to_disk()
    return finalize_loaded_project_fn(proj, msg, import_gsm_name)
