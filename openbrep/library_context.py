"""
openbrep/library_context.py — 项目图库上下文 + 按需 GSM→HSF 转换缓存
（GSM-CALL 研究 P2，2026-09-12）

背景
----
导入的 GSM 对象的 2D/3D 几何常由 ``CALL "宏名" PARAMETERS ...`` 委托给
图库宏对象；预览器（gdl_previewer）本身不执行 CALL，需要外部注入
macro_resolver 把宏名解析成宏的脚本与默认参数（返回协议见
``gdl_previewer.MacroLookup`` / ``MacroResolver``）。本模块的
``build_macro_resolver(...)`` 构造该解析器。

设计与契约
----------
- 图库根惰性索引：第一次 lookup 才建立 "宏名 → 候选 .gsm 路径列表"；
  索引只存路径，不转换任何 .gsm。三种图库根：
    * 目录 —— 递归收集 *.gsm（不深入 .lcf/.libpack，容器要单独配置为 root）；
    * .lcf —— LP_XMLConverter ``extractcontainer`` 解到缓存后按目录索引；
    * .libpack —— 先 ``extractpackage``，再对内层每个 .lcf ``extractcontainer``。
  转换器不可用时容器 root 记 error 诊断（并入后续 lookup 的 message），不崩溃。
- 解析规则（优先级固定）：名称找候选（精确匹配优先，其次 case-insensitive）；
  单候选直接转换；多候选靠 guid_hint 比对 MainGUID 唯一定位，无法唯一区分
  一律 ambiguous——绝不静默选第一个。单候选 guid_hint 不符仍 resolved，
  但 message 注明 GUID 不一致。
- 转换缓存（内容寻址，默认 ``~/.openbrep/cache/library``）：
  fingerprint = sha256(绝对路径 + size + mtime_ns + converter_path)[:16]；
  GSM→HSF 结果在 ``<cache>/hsf/<fp>/``，容器解包在 ``<cache>/containers/<fp>/``、
  包解包在 ``<cache>/packages/<fp>/``；每个缓存目录写 meta.json（source/
  size/mtime/converter/created_at）。**磁盘只缓存成功结果**；失败结果只在
  内存缓存（同一 resolver 实例内不重复跑失败的转换），新实例/下次预览会
  重试——解包/转换失败多为瞬时（文件锁、磁盘压力），持久化失败会让一次
  抖动永久毒化缓存（实测 WL-AC图库.lcf 首次 exit=1 后一直被跳过）。
  密码保护的 GSM 只报告、绝不尝试绕过。
- 许可红线：图库内容只出现在用户本机缓存目录，绝不复制进项目或仓库。
- 项目依赖清单：project_root 非空时记录每次 lookup；对返回的 resolver 调用
  ``flush_manifest()`` 原子写入 ``<project_root>/.openbrep/dependencies/
  library-parts.json``（写失败仅 log warning，绝不抛；不触碰 .openbrep/ 下
  其他目录）。

测试注入点：模块级函数 ``_convert_gsm_to_hsf`` / ``_extract_container`` /
``_extract_package`` 是全部外部进程边界，测试用 monkeypatch 替换它们即可
离线运行（无需真实 LP_XMLConverter / Archicad）。
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from openbrep.compiler import HSFCompiler
from openbrep.gdl_previewer import MacroLookup
from openbrep.hsf_project import HSFProject

_LOGGER = logging.getLogger(__name__)

# 默认缓存根：用户本机目录（图库内容绝不落进项目/仓库，许可红线）。
DEFAULT_CACHE_ROOT = Path.home() / ".openbrep" / "cache" / "library"

_GSM_CONVERT_TIMEOUT = 120      # libpart2hsf 单个 GSM
_CONTAINER_CONVERT_TIMEOUT = 300  # extractcontainer / extractpackage 可能很大

# macOS Archicad 安装自带的图库包目录（内部是 .libpack 文件）。
# 只 glob 存在才加入；刻意不探测 Teamwork 缓存路径。
_AUTODETECT_PATTERN = "/Applications/GRAPHISOFT/Archicad */Archicad Library Packages"

_MANIFEST_RELATIVE = Path(".openbrep") / "dependencies" / "library-parts.json"

# 转换器诊断里的密码保护关键字（只用于提示，绝不尝试绕过）。
_PASSWORD_HINTS = ("password", "protect", "密码", "加密")


def _autodetect_library_package_dirs() -> list[str]:
    """macOS 本机 Archicad Library Packages 目录（最低优先级兜底）。"""
    if platform.system() != "Darwin":
        return []
    return sorted(p for p in glob.glob(_AUTODETECT_PATTERN) if Path(p).is_dir())


def _content_fingerprint(path: Path, extra: str = "") -> str:
    """内容寻址指纹：绝对路径 + size + mtime_ns + extra（通常 converter 路径）。"""
    stat = path.stat()
    payload = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{extra}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _write_meta(cache_dir: Path, data: dict[str, Any]) -> None:
    """meta.json 原子写（tmp + rename）；失败仅 warning。"""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_dir / "meta.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, cache_dir / "meta.json")
    except OSError as exc:
        _LOGGER.warning("图库缓存 meta.json 写入失败 %s: %s", cache_dir, exc)


def _read_meta(cache_dir: Path) -> Optional[dict[str, Any]]:
    try:
        text = (cache_dir / "meta.json").read_text(encoding="utf-8")
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _find_hsf_root(base: Path) -> Optional[Path]:
    """在转换输出里定位 HSF 根（含 libpartdata.xml + scripts/ 的目录）。"""
    if not base.is_dir():
        return None
    if (base / "libpartdata.xml").is_file() and (base / "scripts").is_dir():
        return base
    for candidate in base.rglob("libpartdata.xml"):
        root = candidate.parent
        if (root / "scripts").is_dir():
            return root
    return None


def _password_hint(diagnostics: str) -> str:
    """转换诊断疑似密码保护时给出明确提示（绝不尝试绕过）。"""
    lower = diagnostics.lower()
    if any(h in lower for h in _PASSWORD_HINTS):
        return "（该对象可能受密码保护，OpenBrep 不会尝试绕过）"
    return ""


def _run_lp_converter(
    command: str,
    source: str,
    dest: str,
    converter_path: Optional[str],
    timeout: int,
) -> tuple[bool, str]:
    """调用 LP_XMLConverter；返回 (success, 诊断文本)。

    诊断收集 stdout + stderr（AC28+ macOS 版把诊断写 stdout，
    见 compiler.HSFCompiler._run_converter 的实测注释）。
    """
    if not converter_path:
        return False, "LP_XMLConverter 不可用（未配置 converter 路径，见 config.compiler.path）"
    if not Path(converter_path).exists():
        return False, f"LP_XMLConverter 路径不存在: {converter_path}"
    try:
        proc = subprocess.run(
            [converter_path, command, source, dest],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"LP_XMLConverter {command} 超时（>{timeout}s）: {source}"
    except OSError as exc:
        return False, f"LP_XMLConverter {command} 调用失败: {exc}"
    stdout = HSFCompiler._decode_process_output(proc.stdout).strip()
    stderr = HSFCompiler._decode_process_output(proc.stderr).strip()
    diag = "\n".join(part for part in (stderr, stdout) if part)
    if proc.returncode != 0:
        return False, f"LP_XMLConverter {command} 失败（exit={proc.returncode}）: {diag[:800]}"
    return True, diag


# ── 外部进程边界（测试 monkeypatch 注入点）────────────────────────────────


def _convert_gsm_to_hsf(
    gsm_path: str, out_dir: str, converter_path: Optional[str]
) -> tuple[bool, str]:
    """libpart2hsf：GSM → HSF 目录。返回 (success, 诊断文本)。"""
    ok, diag = _run_lp_converter(
        "libpart2hsf", gsm_path, out_dir, converter_path, _GSM_CONVERT_TIMEOUT
    )
    if not ok:
        return False, diag
    if _find_hsf_root(Path(out_dir)) is None:
        return False, f"LP_XMLConverter libpart2hsf 声称成功但缺少 HSF 输出: {out_dir}"
    return True, diag


def _extract_container(
    lcf_path: str, out_dir: str, converter_path: Optional[str]
) -> tuple[bool, str]:
    """extractcontainer：.lcf → 目录。返回 (success, 诊断文本)。"""
    return _run_lp_converter(
        "extractcontainer", lcf_path, out_dir, converter_path, _CONTAINER_CONVERT_TIMEOUT
    )


def _extract_package(
    libpack_path: str, out_dir: str, converter_path: Optional[str]
) -> tuple[bool, str]:
    """extractpackage：.libpack → 目录（内层含 .lcf）。返回 (success, 诊断文本)。"""
    return _run_lp_converter(
        "extractpackage", libpack_path, out_dir, converter_path, _CONTAINER_CONVERT_TIMEOUT
    )


# ── 图库根索引 ──────────────────────────────────────────────────────────


@dataclass
class _Candidate:
    """一个名称候选：某个图库根下的具体 .gsm 文件。"""

    gsm_path: Path
    source: str       # 来源描述（图库根/容器路径）
    root_order: int   # 图库根优先级（小在前）


def _guid_eq(a: Optional[str], b: Optional[str]) -> bool:
    return (a or "").strip().upper() == (b or "").strip().upper()


class LibraryContext:
    """图库上下文解析器：callable ``(宏名, guid_hint or None) -> MacroLookup``。

    由 ``build_macro_resolver`` 构造。附带方法：

    - ``flush_manifest()`` —— 把记录的 lookup 结果原子写入项目依赖清单
      （仅当构造时传了 project_root；返回写出的路径，未配置/写失败返回 None）。
    """

    def __init__(
        self,
        roots: list[str],
        converter_path: Optional[str],
        cache_dir: Optional[str] = None,
        project_root: Optional[str] = None,
    ):
        self._root_specs = list(roots)
        self._converter_path = converter_path or ""
        self._cache_root = Path(cache_dir).expanduser() if cache_dir else DEFAULT_CACHE_ROOT
        self._project_root = Path(project_root).expanduser() if project_root else None
        # 惰性索引：第一次 lookup 才建（容器 root 的解包也发生在那一刻）。
        self._index: Optional[dict[str, list[_Candidate]]] = None
        self._index_lower: dict[str, str] = {}   # lower(name) → 精确名（先见优先）
        self._root_errors: list[str] = []        # 容器 root 解包失败诊断
        self._manifest: dict[str, dict[str, Any]] = {}  # name → 最新一条 lookup 记录
        # 失败结果只在内存缓存（本实例内不重复跑失败的转换）；磁盘只缓存成功
        # 结果——解包/转换失败多为瞬时（文件锁、磁盘压力），持久化会让一次
        # 失败永久毒化缓存（实测 WL-AC图库.lcf 首次 exit=1 后一直被跳过）。
        self._failed_ops: dict[str, str] = {}    # "kind:路径" → 诊断

    # ── 公开协议 ────────────────────────────────────────────────────────

    def __call__(self, name: str, guid_hint: Optional[str] = None) -> MacroLookup:
        name = (name or "").strip().strip('"').strip("'")
        hint = (guid_hint or "").strip() or None
        self._ensure_index()
        lookup = self._lookup(name, hint)
        self._record_manifest(name, hint, lookup)
        return lookup

    def flush_manifest(self) -> Optional[Path]:
        """项目依赖清单原子落盘；写失败仅 warning，绝不抛。"""
        if self._project_root is None:
            return None
        target = self._project_root / _MANIFEST_RELATIVE
        payload = {
            "version": 1,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "entries": [
                self._manifest[key] for key in sorted(self._manifest)
            ],
        }
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, target)
            return target
        except OSError as exc:
            _LOGGER.warning("项目图库依赖清单写入失败 %s: %s", target, exc)
            return None

    # ── 索引构建（惰性） ────────────────────────────────────────────────

    def _ensure_index(self) -> None:
        if self._index is not None:
            return
        self._index = {}
        self._index_lower = {}
        self._root_errors = []
        for order, spec in enumerate(self._root_specs):
            self._index_root(Path(spec), order)

    def _index_root(self, root: Path, order: int) -> None:
        suffix = root.suffix.lower()
        if root.is_dir():
            self._index_directory(root, str(root), order)
        elif suffix == ".lcf":
            extracted = self._container_dir(root)
            if extracted is not None:
                self._index_directory(extracted, f"{root} (LCF)", order)
        elif suffix == ".libpack":
            extracted = self._package_dir(root)
            if extracted is not None:
                self._index_directory(extracted, f"{root} (LIBPACK)", order)
        else:
            _LOGGER.warning("不支持的图库根类型（跳过）: %s", root)
            self._root_errors.append(f"不支持的图库根类型: {root}")

    def _index_directory(self, base: Path, source: str, order: int) -> None:
        assert self._index is not None
        gsm_paths: list[Path] = []
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in filenames:
                if filename.lower().endswith(".gsm"):
                    gsm_paths.append(Path(dirpath) / filename)
        for gsm_path in sorted(gsm_paths):
            name = gsm_path.stem
            self._index.setdefault(name, []).append(
                _Candidate(gsm_path=gsm_path, source=source, root_order=order)
            )
            self._index_lower.setdefault(name.lower(), name)

    def _container_dir(self, lcf_path: Path) -> Optional[Path]:
        """解包 .lcf（带缓存）→ 可索引目录；失败记诊断返回 None。"""
        fingerprint = _content_fingerprint(lcf_path, self._converter_path)
        target = self._cache_root / "containers" / fingerprint
        meta = _read_meta(target)
        if meta and meta.get("ok"):
            return target
        fail_key = f"container:{lcf_path}"
        if fail_key in self._failed_ops:
            self._root_errors.append(
                f"图库根解包失败 {lcf_path}: {self._failed_ops[fail_key]}"
            )
            return None
        ok, diag = _extract_container(str(lcf_path), str(target), self._converter_path or None)
        if not ok:
            # 失败不落盘（瞬时失败可下次重试），清掉可能的部分输出，只内存缓存
            shutil.rmtree(target, ignore_errors=True)
            self._failed_ops[fail_key] = diag
            self._root_errors.append(f"图库根解包失败 {lcf_path}: {diag}")
            return None
        self._write_container_meta(target, lcf_path)
        return target
    def _package_dir(self, pack_path: Path) -> Optional[Path]:
        """解包 .libpack（带缓存）→ 内层每个 .lcf 再 extractcontainer。"""
        fingerprint = _content_fingerprint(pack_path, self._converter_path)
        target = self._cache_root / "packages" / fingerprint
        meta = _read_meta(target)
        if meta and meta.get("ok"):
            return target
        fail_key = f"package:{pack_path}"
        if fail_key in self._failed_ops:
            self._root_errors.append(
                f"图库包解包失败 {pack_path}: {self._failed_ops[fail_key]}"
            )
            return None
        ok, diag = _extract_package(str(pack_path), str(target), self._converter_path or None)
        if not ok:
            shutil.rmtree(target, ignore_errors=True)
            self._failed_ops[fail_key] = diag
            self._root_errors.append(f"图库包解包失败 {pack_path}: {diag}")
            return None
        # 内层 .lcf 逐个解包到包目录下；单个失败只记诊断、继续其余。
        inner_errors: list[str] = []
        inner_lcfs = sorted(
            p for p in target.rglob("*") if p.is_file() and p.suffix.lower() == ".lcf"
        )
        for inner in inner_lcfs:
            inner_fp = _content_fingerprint(inner, self._converter_path)
            inner_target = target / "_lcfs" / inner_fp
            inner_meta = _read_meta(inner_target)
            if inner_meta and inner_meta.get("ok"):
                continue
            inner_key = f"container:{inner}"
            if inner_key in self._failed_ops:
                inner_errors.append(f"{inner.name}: {self._failed_ops[inner_key]}")
                continue
            inner_ok, inner_diag = _extract_container(
                str(inner), str(inner_target), self._converter_path or None
            )
            if not inner_ok:
                shutil.rmtree(inner_target, ignore_errors=True)
                self._failed_ops[inner_key] = inner_diag
                inner_errors.append(f"{inner.name}: {inner_diag}")
                continue
            self._write_container_meta(inner_target, inner)
        note = "；".join(inner_errors)
        self._write_container_meta(target, pack_path, note)
        if inner_errors:
            self._root_errors.append(f"图库包部分内层容器解包失败 {pack_path}: {note}")
        return target

    def _write_container_meta(self, target: Path, source: Path, note: str = "") -> None:
        """成功解包才落盘 meta（磁盘缓存只存成功结果）。"""
        try:
            stat = source.stat()
            size, mtime_ns = stat.st_size, stat.st_mtime_ns
        except OSError:
            size, mtime_ns = 0, 0
        meta: dict[str, Any] = {
            "ok": True,
            "source": str(source),
            "size": size,
            "mtime_ns": mtime_ns,
            "converter": self._converter_path,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        if note:
            meta["note"] = note
        _write_meta(target, meta)

    # ── 解析规则 ────────────────────────────────────────────────────────

    def _lookup(self, name: str, guid_hint: Optional[str]) -> MacroLookup:
        assert self._index is not None
        candidates = self._index.get(name)
        if candidates is None:
            canonical = self._index_lower.get(name.lower())
            if canonical is not None:
                candidates = self._index.get(canonical)
        candidates = candidates or []

        if not candidates:
            message = (
                f"已配置图库中未找到宏 '{name}'"
                f"（已搜索 {len(self._root_specs)} 个图库根）"
            )
            if self._root_errors:
                message += "；图库根诊断: " + "；".join(self._root_errors)
            return MacroLookup(status="missing", name=name, message=message)

        if len(candidates) == 1:
            return self._resolve_single(name, candidates[0], guid_hint)
        return self._resolve_ambiguous(name, candidates, guid_hint)

    def _resolve_single(
        self, name: str, candidate: _Candidate, guid_hint: Optional[str]
    ) -> MacroLookup:
        hsf, error = self._load_hsf_for_gsm(candidate.gsm_path)
        if hsf is None:
            return MacroLookup(
                status="error", name=name, source=candidate.source, message=error
            )
        message = ""
        if guid_hint and not _guid_eq(hsf.guid, guid_hint):
            message = (
                f"按名称解析，GUID 与 calledmacros 记录不符"
                f"（记录 {guid_hint}，实际 {hsf.guid}）"
            )
        return self._resolved_lookup(name, candidate, hsf, message)

    def _resolve_ambiguous(
        self, name: str, candidates: list[_Candidate], guid_hint: Optional[str]
    ) -> MacroLookup:
        matches: list[tuple[_Candidate, HSFProject]] = []
        convert_errors: list[str] = []
        for candidate in candidates:
            hsf, error = self._load_hsf_for_gsm(candidate.gsm_path)
            if hsf is None:
                convert_errors.append(f"{candidate.gsm_path}: {error}")
                continue
            if guid_hint and _guid_eq(hsf.guid, guid_hint):
                matches.append((candidate, hsf))

        if len(matches) == 1:
            candidate, hsf = matches[0]
            message = ""
            if convert_errors:
                message = "部分同名候选转换失败: " + "；".join(convert_errors)
            return self._resolved_lookup(name, candidate, hsf, message)

        paths = "、".join(str(c.gsm_path) for c in candidates)
        if guid_hint:
            message = (
                f"宏 '{name}' 有 {len(candidates)} 个同名候选，"
                f"GUID 提示无法唯一区分（匹配 {len(matches)} 个）: {paths}"
            )
        else:
            message = (
                f"宏 '{name}' 有 {len(candidates)} 个同名候选且无 GUID 提示，"
                f"无法区分: {paths}"
            )
        if convert_errors:
            message += "；部分候选转换失败: " + "；".join(convert_errors)
        return MacroLookup(status="ambiguous", name=name, message=message)

    def _resolved_lookup(
        self,
        name: str,
        candidate: _Candidate,
        hsf: HSFProject,
        message: str,
    ) -> MacroLookup:
        return MacroLookup(
            status="resolved",
            name=name,
            guid=hsf.guid,
            message=message,
            source=candidate.source,
            parameters={p.name: p.value for p in hsf.parameters},
            scripts={st.value: content for st, content in hsf.scripts.items()},
            called_macros=hsf.called_macro_guid_map(),
        )

    # ── GSM → HSF 转换缓存 ─────────────────────────────────────────────

    def _load_hsf_for_gsm(self, gsm_path: Path) -> tuple[Optional[HSFProject], str]:
        """按需转换 GSM → HSF（内容寻址缓存；失败只内存缓存，不落盘）。

        返回 (hsf, "") 或 (None, 错误诊断)。
        """
        fingerprint = _content_fingerprint(gsm_path, self._converter_path)
        cache_dir = self._cache_root / "hsf" / fingerprint

        fail_key = f"gsm:{gsm_path}"
        if fail_key in self._failed_ops:
            return None, self._failed_ops[fail_key]

        hsf_root = _find_hsf_root(cache_dir)
        if hsf_root is None:
            # 缓存未命中 → 转换
            ok, diag = _convert_gsm_to_hsf(
                str(gsm_path), str(cache_dir), self._converter_path or None
            )
            if not ok:
                message = f"宏对象转换失败 {gsm_path}: {diag}{_password_hint(diag)}"
                shutil.rmtree(cache_dir, ignore_errors=True)
                self._failed_ops[fail_key] = message
                return None, message
            self._write_gsm_meta(cache_dir, gsm_path)
            hsf_root = _find_hsf_root(cache_dir)
            if hsf_root is None:
                message = f"宏对象转换输出缺少 HSF 结构: {cache_dir}"
                shutil.rmtree(cache_dir, ignore_errors=True)
                self._failed_ops[fail_key] = message
                return None, message

        try:
            return HSFProject.load_from_disk(str(hsf_root)), ""
        except Exception as exc:
            message = f"缓存 HSF 载入失败 {hsf_root}: {exc}"
            self._failed_ops[fail_key] = message
            return None, message

    def _write_gsm_meta(self, cache_dir: Path, gsm_path: Path) -> None:
        """成功转换才落盘 meta（磁盘缓存只存成功结果）。"""
        try:
            stat = gsm_path.stat()
            size, mtime_ns = stat.st_size, stat.st_mtime_ns
        except OSError:
            size, mtime_ns = 0, 0
        meta: dict[str, Any] = {
            "ok": True,
            "source": str(gsm_path),
            "size": size,
            "mtime_ns": mtime_ns,
            "converter": self._converter_path,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_meta(cache_dir, meta)

    # ── 项目依赖清单 ────────────────────────────────────────────────────

    def _record_manifest(
        self, name: str, guid_hint: Optional[str], lookup: MacroLookup
    ) -> None:
        if self._project_root is None:
            return
        self._manifest[name] = {
            "name": name,
            "guid": lookup.guid or "",
            "requested_guid": guid_hint or "",
            "status": lookup.status,
            "source": lookup.source,
            "message": lookup.message,
        }


def build_macro_resolver(
    roots: list[str],
    converter_path: Optional[str],
    cache_dir: Optional[str] = None,
    project_root: Optional[str] = None,
    include_autodetect: bool = True,
) -> Optional[Callable[[str, Optional[str]], MacroLookup]]:
    """构造预览器注入用的宏解析器；无可用图库根时返回 None。

    roots 按优先级排序，不存在的路径跳过（log warning 记录诊断）。
    ``include_autodetect=True`` 时在配置 roots 之后追加最低优先级兜底：
    macOS ``/Applications/GRAPHISOFT/Archicad */Archicad Library Packages``
    （glob 存在才加入）。

    返回值是 ``LibraryContext`` 实例（即可调用的 resolver）；project_root
    非空时可再调 ``flush_manifest()`` 落盘项目依赖清单。调用方用
    ``resolver is None`` 区分 "未配置图库" 与 "宏缺失"（missing）。
    """
    root_specs: list[str] = []
    for raw in roots or []:
        path = Path(str(raw)).expanduser()
        if path.exists():
            root_specs.append(str(path))
        else:
            _LOGGER.warning("图库根不存在，跳过: %s", raw)
    if include_autodetect:
        for detected in _autodetect_library_package_dirs():
            if detected not in root_specs:
                root_specs.append(detected)
    if not root_specs:
        return None
    return LibraryContext(
        root_specs,
        converter_path,
        cache_dir=cache_dir,
        project_root=project_root,
    )
