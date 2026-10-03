"""Immutable, in-memory HSF context and save-aware source version checks."""
from __future__ import annotations

import copy
import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.paramlist_builder import build_paramlist_xml, parameters_semantically_equal, parse_paramlist_xml
from openbrep.source_fingerprint import collect_managed_source_files, compute_source_fingerprint

MAX_DRAFT_BYTES = 2 * 1024 * 1024
DRAFT_NAMES = frozenset([st.value for st in ScriptType] + ['paramlist.xml', 'libpartdata.xml'])


def _digest(value) -> str:
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _xml(text: str):
    try:
        node = ET.fromstring(text.lstrip('\ufeff'))
        def visit(element):
            return [element.tag, sorted(element.attrib.items()), (element.text or '').strip(), [visit(c) for c in element]]
        return visit(node)
    except ET.ParseError:
        return text


def _extensions(raw: str, generated: str):
    """Retain XML content not represented by HSFProject's serializer."""
    try:
        a, b = ET.fromstring(raw.lstrip('\ufeff')), ET.fromstring(generated.lstrip('\ufeff'))
    except ET.ParseError:
        return raw if raw != generated else None
    def extra(old, new):
        attrs = {k: v for k, v in old.attrib.items() if k not in new.attrib}
        children = []
        remaining = list(new)
        for child in old:
            match = next((c for c in remaining if c.tag == child.tag and (c.attrib.get('Name') == child.attrib.get('Name'))), None)
            if match is None:
                children.append(_xml(ET.tostring(child, encoding='unicode')))
            else:
                remaining.remove(match)
                residual = extra(child, match)
                if residual:
                    children.append([child.tag, child.attrib.get('Name'), residual])
        text = (old.text or '').strip() if not (new.text or '').strip() else ''
        return [attrs, text, children] if attrs or text or children else None
    return extra(a, b)


def serialized_source(project: HSFProject) -> dict[str, str]:
    raw = project._paramlist_raw
    if raw is None or not parameters_semantically_equal(parse_paramlist_xml(raw), project.parameters):
        raw = build_paramlist_xml(project.parameters)
    files = {'libpartdata.xml': project._build_libpartdata(), 'paramlist.xml': raw,
             'ancestry.xml': project._build_ancestry(), 'calledmacros.xml': project._build_calledmacros(),
             'libpartdocs.xml': project._build_libpartdocs()}
    files.update({f'scripts/{st.value}': content for st, content in project.scripts.items()})
    return files


def _context(project: HSFProject | None, files: Mapping[str, bytes]) -> str:
    if project is None:
        return _digest(None)
    generated = serialized_source(project)
    extensions = {}
    for name, data in files.items():
        if name.endswith('.xml'):
            raw = data.decode('utf-8-sig')
            residual = _extensions(raw, generated[name]) if name in generated else _xml(raw)
            if residual is not None:
                extensions[name] = residual
        elif name not in generated:
            extensions[name] = data.hex()
    payload = {name: _xml(text) if name.endswith('.xml') else text.lstrip('\ufeff').replace('\r\n', '\n') for name, text in generated.items()}
    return _digest({'effective_source': payload, 'extensions': extensions})


@dataclass(frozen=True)
class SourceSnapshot:
    project_epoch: int
    source_fingerprint: str | None
    context_fingerprint: str
    draft_hash: str | None
    dependency_context_version: str | None
    files: Mapping[str, bytes] = field(repr=False)
    drafts: Mapping[str, str] = field(repr=False)
    _project: HSFProject | None = field(repr=False, compare=False)

    def project_copy(self) -> HSFProject | None:
        return copy.deepcopy(self._project)

    @property
    def source_version(self) -> dict:
        return {k: getattr(self, k) for k in ('project_epoch', 'source_fingerprint', 'context_fingerprint', 'draft_hash', 'dependency_context_version')}

    def matches(self, project: HSFProject | None, epoch: int, dependency_version: str | None = None) -> bool:
        if epoch != self.project_epoch or dependency_version != self.dependency_context_version:
            return False
        now = capture_snapshot(project, epoch, dependency_context_version=dependency_version)
        if now.context_fingerprint != self.context_fingerprint:
            return False
        # Compare every managed file, including unknown/pass-through XML. Only
        # accept original bytes or the actual HSF serializer's predicted save.
        expected = serialized_source(self._project) if self._project is not None else {}
        for name in set(self.files) | set(now.files):
            actual = now.files.get(name)
            if name not in self.drafts and name.removeprefix('scripts/') not in self.drafts and actual == self.files.get(name):
                continue
            if actual is None or name not in expected:
                return False
            if name.endswith('.xml'):
                if _xml(actual.decode('utf-8-sig')) != _xml(expected[name]):
                    return False
            elif actual.decode('utf-8-sig').replace('\r\n', '\n') != expected[name].replace('\r\n', '\n'):
                return False
        return True


def capture_snapshot(project: HSFProject | None, project_epoch: int, draft_scripts=None, *, dependency_context_version: str | None = None) -> SourceSnapshot:
    drafts = draft_scripts or {}
    if not isinstance(drafts, dict) or any(name not in DRAFT_NAMES or not isinstance(content, str) for name, content in drafts.items()):
        raise ValueError('Invalid draft script target or content')
    if sum(len(content.encode()) for content in drafts.values()) > MAX_DRAFT_BYTES:
        raise ValueError('Draft scripts exceed request limit')
    if drafts and project is None:
        raise ValueError('Draft scripts require a project')
    effective = copy.deepcopy(project)
    files = {}
    saved = None
    if project is not None and project.root.is_dir():
        files = {name: (project.root / name).read_bytes() for name in collect_managed_source_files(project.root)}
        saved = compute_source_fingerprint(project.root)
        # Disk XML may have been externally edited without refreshing session.
        disk = HSFProject.load_from_disk(str(project.root))
        disk.scripts = copy.deepcopy(project.scripts)
        disk.parameters = copy.deepcopy(project.parameters)
        effective = disk
    for name, content in drafts.items():
        if name == 'paramlist.xml':
            effective.parameters = parse_paramlist_xml(content)
            effective._paramlist_raw = content
            files[name] = content.encode('utf-8-sig')
        elif name == 'libpartdata.xml':
            ET.fromstring(content)
            effective._parse_libpartdata(content)
            files[name] = content.encode('utf-8-sig')
        else:
            effective.set_script(ScriptType(name), content)
    return SourceSnapshot(project_epoch, saved, _context(effective, files), _digest(drafts) if drafts else None,
                          dependency_context_version, MappingProxyType(files), MappingProxyType(dict(drafts)), effective)
