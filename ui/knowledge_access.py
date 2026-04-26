from __future__ import annotations

import binascii
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from openbrep.knowledge import KnowledgeBase
from ui import view_models as ui_view_models


def load_knowledge(task_type: str = "all", *, work_dir: str, pro_unlocked: bool, project_root: Path | None = None):
    root = project_root or (Path(__file__).parent.parent)
    project_kb = root / "knowledge"
    kb = KnowledgeBase(str(project_kb))
    try:
        kb.load()
    except Exception:
        kb._docs = {}

    def _merge_layer(layer_dir: Path, *, overwrite: bool) -> None:
        try:
            layer_kb = KnowledgeBase(str(layer_dir))
            layer_kb.load()
            if overwrite:
                kb._docs.update(layer_kb._docs)
            else:
                for k, v in layer_kb._docs.items():
                    if k not in kb._docs:
                        kb._docs[k] = v
        except Exception:
            return

    user_kb_dir = Path(work_dir) / "knowledge"
    if user_kb_dir.exists() and user_kb_dir != project_kb:
        _merge_layer(user_kb_dir, overwrite=True)

    if pro_unlocked:
        pro_kb_dir = Path(work_dir) / "pro_knowledge"
        if pro_kb_dir.exists():
            _merge_layer(pro_kb_dir, overwrite=True)

    return kb.get_by_task_type(task_type)


def _license_file(work_dir: str) -> Path:
    return Path(work_dir) / ".openbrep" / "license_v1.json"


def _empty_license_record() -> dict:
    return ui_view_models.empty_license_record()


def _load_license(work_dir: str) -> dict:
    fp = _license_file(work_dir)
    if not fp.exists():
        return _empty_license_record()
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _empty_license_record()


def _save_license(work_dir: str, data: dict) -> None:
    fp = _license_file(work_dir)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_pro_public_key(root: Path):
    key_file = root / "openbrep" / "public_keys" / "pro_public.pem"
    if not key_file.exists():
        raise FileNotFoundError(f"缺少公钥文件：{key_file}")
    return serialization.load_pem_public_key(key_file.read_bytes())


def _urlsafe_b64decode(data: str) -> bytes:
    return ui_view_models.urlsafe_b64decode(data)


def _urlsafe_b64encode(data: bytes) -> str:
    return ui_view_models.urlsafe_b64encode(data)


def _canonical_license_payload(payload: dict) -> bytes:
    return ui_view_models.canonical_license_payload(payload)


def _normalize_license_record(payload: dict, signature_b64: str) -> dict:
    return ui_view_models.normalize_license_record(payload, signature_b64)


def _verify_license_payload(payload: dict, signature_b64: str, *, root: Path | None = None) -> tuple[bool, str, dict | None]:
    expire_date = str(payload.get("expire_date", "")).strip()
    if expire_date:
        try:
            if datetime.now().date() > datetime.strptime(expire_date, "%Y-%m-%d").date():
                return False, "授权码已过期", None
        except ValueError:
            return False, "授权日期格式无效", None

    try:
        project_root = root or Path(__file__).parent.parent
        public_key = _load_pro_public_key(project_root)
        signature = _urlsafe_b64decode(signature_b64)
        public_key.verify(
            signature,
            _canonical_license_payload(payload),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except FileNotFoundError as e:
        return False, str(e), None
    except (ValueError, binascii.Error):
        return False, "授权码格式错误", None
    except InvalidSignature:
        return False, "授权签名无效", None
    except Exception as e:
        return False, f"授权校验失败: {e}", None

    return True, "授权码有效", _normalize_license_record(payload, signature_b64)


def _decode_signed_license_code(code: str, *, root: Path | None = None) -> tuple[bool, str, dict | None]:
    return ui_view_models.decode_signed_license_code(
        code,
        verify_license_payload=lambda payload, signature_b64: _verify_license_payload(payload, signature_b64, root=root),
    )


def _verify_pro_code(code: str, *, root: Path | None = None) -> tuple[bool, str, dict | None]:
    return ui_view_models.verify_pro_code(
        code,
        decode_signed_license_code_fn=lambda token: _decode_signed_license_code(token, root=root),
    )


def _license_record_is_active(data: dict, *, root: Path | None = None) -> tuple[bool, str, dict | None]:
    return ui_view_models.license_record_is_active(
        data,
        verify_license_payload=lambda payload, signature_b64: _verify_license_payload(payload, signature_b64, root=root),
    )


def _verify_pro_package(unpacked_dir: Path, *, root: Path | None = None) -> tuple[bool, str, dict | None]:
    manifest_path = unpacked_dir / "manifest.json"
    signature_path = unpacked_dir / "signature.sig"
    docs_dir = unpacked_dir / "docs"

    if not manifest_path.exists() or not signature_path.exists():
        return False, "知识包缺少 manifest.json 或 signature.sig", None
    if not docs_dir.exists() or not docs_dir.is_dir():
        return False, "知识包缺少 docs 目录", None

    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception:
        return False, "知识包 manifest 无法解析", None

    if not isinstance(manifest, dict):
        return False, "知识包 manifest 格式错误", None

    required_fields = ["buyer_id", "plan", "issued_at"]
    missing = [field for field in required_fields if not str(manifest.get(field, "")).strip()]
    if missing:
        return False, f"知识包 manifest 缺少字段：{', '.join(missing)}", None

    try:
        project_root = root or Path(__file__).parent.parent
        public_key = _load_pro_public_key(project_root)
        public_key.verify(
            signature_path.read_bytes(),
            manifest_bytes,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except FileNotFoundError as e:
        return False, str(e), None
    except InvalidSignature:
        return False, "知识包签名无效", None
    except Exception as e:
        return False, f"知识包验签失败：{e}", None

    expire_date = str(manifest.get("expire_date", "")).strip()
    if expire_date:
        try:
            if datetime.now().date() > datetime.strptime(expire_date, "%Y-%m-%d").date():
                return False, "知识包已过期", None
        except ValueError:
            return False, "知识包日期格式无效", None

    return True, "知识包验签通过", manifest


def _license_matches_package(license_record: dict, package_manifest: dict) -> tuple[bool, str]:
    return ui_view_models.license_matches_package(license_record, package_manifest)


def _import_pro_knowledge_zip(file_bytes: bytes, filename: str, work_dir: str, *, root: Path | None = None) -> tuple[bool, str]:
    if not filename.lower().endswith((".zip", ".obrk")):
        return False, "仅支持 .zip 或 .obrk 知识包"

    license_record = _load_license(work_dir)
    if not bool(license_record.get("pro_unlocked", False)):
        return False, "请先激活 Pro 后再导入知识包"

    ok, msg, normalized_license = _license_record_is_active(license_record, root=root)
    if not ok or normalized_license is None:
        _save_license(work_dir, _empty_license_record())
        return False, f"请先重新激活 Pro：{msg}"

    _save_license(work_dir, normalized_license)

    target = Path(work_dir) / "pro_knowledge"
    tmp = Path(work_dir) / ".openbrep" / "tmp_pro_knowledge"
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)

        zpath = tmp / "package.zip"
        zpath.write_bytes(file_bytes)

        with zipfile.ZipFile(zpath, "r") as zf:
            zf.extractall(tmp / "unpacked")

        unpacked = tmp / "unpacked"
        ok, msg, manifest = _verify_pro_package(unpacked, root=root)
        if not ok or manifest is None:
            return False, f"❌ 导入失败：{msg}"

        ok, msg = _license_matches_package(normalized_license, manifest)
        if not ok:
            return False, f"❌ 导入失败：{msg}"

        docs_dir = unpacked / "docs"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        for item in docs_dir.iterdir():
            dest = target / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        return True, f"✅ Pro 知识包导入完成：{target}"
    except Exception as e:
        return False, f"❌ 导入失败：{e}"
    finally:
        if tmp.exists():
            try:
                shutil.rmtree(tmp)
            except Exception:
                pass
