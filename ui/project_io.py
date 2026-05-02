from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class _LocalPathUpload:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.size = path.stat().st_size

    def read(self) -> bytes:
        return self.path.read_bytes()


def _existing_hsf_root(proj) -> Path | None:
    raw_root = getattr(proj, "root", None)
    if not raw_root:
        return None
    root = Path(raw_root).expanduser().resolve()
    if not root.is_dir():
        return None
    if (root / "libpartdata.xml").exists() or (root / "scripts").is_dir():
        return root
    return None


def _format_compile_result(*, result, output_gsm: str, compiler_mode: str, hsf_dir: str | Path | None = None) -> tuple[bool, str]:
    mock_tag = " [Mock]" if compiler_mode.startswith("Mock") else ""
    if result.success:
        msg = f"✅ **编译成功{mock_tag}**\n\n📦 GSM: `{output_gsm}`"
        if hsf_dir:
            msg += f"\n\n📁 HSF 源目录: `{hsf_dir}`"
        if compiler_mode.startswith("Mock"):
            msg += "\n\n⚠️ Mock 模式不生成真实 .gsm，切换 LP_XMLConverter 进行真实编译。"
        return True, msg

    return False, f"❌ **编译失败**\n\n```\n{result.stderr[:500]}\n```"


def _prepare_project_for_compile(proj, gsm_name: str, work_dir: str) -> None:
    project_name = str(getattr(proj, "name", "") or "").strip()
    output_name = str(gsm_name or "").strip()
    existing_root = _existing_hsf_root(proj)

    if (not project_name or project_name == "untitled") and output_name:
        proj.name = output_name
        project_name = output_name

    if existing_root is not None:
        if hasattr(proj, "work_dir"):
            proj.work_dir = existing_root.parent
        if hasattr(proj, "root"):
            proj.root = existing_root
        return

    compile_work_dir = Path(work_dir)
    if hasattr(proj, "work_dir"):
        proj.work_dir = compile_work_dir
    if hasattr(proj, "root") and project_name:
        proj.root = compile_work_dir / project_name


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
    format_compile_result_fn=None,
) -> tuple:
    format_compile_result_fn = format_compile_result_fn or _format_compile_result
    try:
        work_dir = str(session_state.work_dir)
        _prepare_project_for_compile(proj, gsm_name, work_dir)
        compile_name = gsm_name or proj.name

        requested_rev = int(session_state.get("script_revision", 0)) or 1
        compile_rev = safe_compile_revision_fn(compile_name, work_dir, requested_rev)
        if compile_rev != requested_rev:
            session_state.script_revision = compile_rev
        output_gsm = versioned_gsm_path_fn(compile_name, work_dir, revision=compile_rev)
        hsf_dir = proj.save_to_disk()
        result = get_compiler_fn().hsf2libpart(str(hsf_dir), output_gsm)

        ok, msg = format_compile_result_fn(
            result=result,
            output_gsm=output_gsm,
            compiler_mode=compiler_mode,
            hsf_dir=hsf_dir,
        )
        session_state.compile_log.append({
            "project": proj.name,
            "instruction": instruction,
            "success": bool(result.success),
            "attempts": 1,
            "message": "Success" if result.success else result.stderr,
        })
        return ok, msg
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
        # 附加 Windows 特有诊断
        extra = ""
        import platform
        if platform.system() == "Windows":
            extra = (
                f"\n\n**Windows 诊断**:\n"
                f"- 路径是否存在: {Path(compiler.converter_path).exists() if compiler.converter_path else False}\n"
                f"- 是否为文件: {Path(compiler.converter_path).is_file() if compiler.converter_path else False}\n"
                f"- 扩展名: {Path(compiler.converter_path).suffix if compiler.converter_path else ''}\n"
            )
        return (
            None,
            f"❌ GSM 解包失败 (exit={result.exit_code})\n\n"
            f"**Binary**: `{bin_path}`\n\n"
            f"**输出**:\n```\n{diag[:800]}\n```{extra}",
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

    input_dir = Path(raw_path).expanduser()
    hsf_dir = _resolve_hsf_project_dir(input_dir)
    if not hsf_dir.exists():
        return False, f"❌ 目录不存在: {hsf_dir}"
    if not hsf_dir.is_dir():
        return False, f"❌ 不是目录: {hsf_dir}"
    if not _looks_like_hsf_project(hsf_dir):
        candidates = _find_hsf_project_candidates(input_dir)
        if candidates:
            names = ", ".join(candidate.name for candidate in candidates[:8])
            suffix = "..." if len(candidates) > 8 else ""
            return False, f"❌ 目录下有多个 HSF 项目，请选择其中一个: {names}{suffix}"
        return False, f"❌ 不是有效 HSF 项目目录: {hsf_dir}"

    try:
        proj = load_project_from_disk_fn(str(hsf_dir))
    except Exception as e:
        return False, f"❌ 载入 HSF 项目失败: {e}"

    msg = (
        f"✅ 已加载 HSF 项目 `{proj.name}` — {len(proj.parameters)} 参数，{len(proj.scripts)} 脚本"
        f"\n\n源目录: `{hsf_dir}`"
    )
    return finalize_loaded_project_fn(proj, msg, pending_gsm_name=proj.name, preserve_project_root=True)


def _resolve_hsf_project_dir(path: Path) -> Path:
    """Accept either an HSF root or a parent folder containing one HSF project."""
    if _looks_like_hsf_project(path):
        return path
    if not path.is_dir():
        return path

    candidates = _find_hsf_project_candidates(path)
    if len(candidates) == 1:
        return candidates[0]
    return path


def _find_hsf_project_candidates(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return [child for child in sorted(path.iterdir()) if _looks_like_hsf_project(child)]


def _looks_like_hsf_project(path: Path) -> bool:
    return path.is_dir() and ((path / "libpartdata.xml").exists() or (path / "scripts").is_dir())


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
            detail = str(msg or "GSM 导入失败")
            if detail.startswith("❌"):
                return False, f"❌ [IMPORT_GSM] {detail[1:].strip()}"
            return False, f"❌ [IMPORT_GSM] {detail}"
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


def handle_open_path(
    source_path: str,
    *,
    normalize_pasted_path_fn,
    load_project_from_disk_fn,
    import_gsm_fn,
    parse_gdl_source_fn,
    derive_gsm_name_from_filename_fn,
    finalize_loaded_project_fn,
) -> tuple[bool, str]:
    raw_path = normalize_pasted_path_fn(source_path)
    if not raw_path:
        return False, "❌ 请选择要打开的文件或 HSF 项目目录"

    path = Path(raw_path).expanduser()
    if not path.exists():
        return False, f"❌ 路径不存在: {path}"

    if path.is_dir():
        return handle_hsf_directory_load(
            str(path),
            normalize_pasted_path_fn=normalize_pasted_path_fn,
            load_project_from_disk_fn=load_project_from_disk_fn,
            finalize_loaded_project_fn=finalize_loaded_project_fn,
        )

    if not path.is_file():
        return False, f"❌ 不支持的路径类型: {path}"

    ext = path.suffix.lower()
    if ext not in {".gdl", ".txt", ".gsm"}:
        return False, f"❌ 不支持的文件类型: {path.name}。请选择 .gdl、.txt、.gsm 文件，或 HSF 项目目录。"

    return handle_unified_import(
        _LocalPathUpload(path),
        import_gsm_fn=import_gsm_fn,
        parse_gdl_source_fn=parse_gdl_source_fn,
        derive_gsm_name_from_filename_fn=derive_gsm_name_from_filename_fn,
        finalize_loaded_project_fn=finalize_loaded_project_fn,
    )
