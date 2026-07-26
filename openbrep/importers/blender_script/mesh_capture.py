"""
BS2G mesh capture — execute a Blender script with fake bpy/bmesh modules.

bmesh-based scripts (lofts, airfoils, arbitrary meshes) cannot be parsed
statically the way primitive-assembly scripts can — their geometry lives
in computed vertex lists.  Instead we EXECUTE the script with stub
``bpy`` / ``bmesh`` modules that record every ``verts.new`` / ``faces.new``
call into a :class:`CapturedMesh`.

Trust model: the script runs with full Python privileges, exactly as if
the user pressed "Run Script" in Blender.  Only convert scripts you
would run in Blender.
"""

from __future__ import annotations

import sys
import traceback
import types
from dataclasses import dataclass, field


class MeshCaptureError(Exception):
    """The script could not be executed or produced no mesh geometry."""


@dataclass
class CapturedMesh:
    """Raw mesh recorded from bmesh calls."""

    verts: list[tuple[float, float, float]] = field(default_factory=list)
    faces: list[list[int]] = field(default_factory=list)


# ── Fake bmesh ──────────────────────────────────────────────


class _FakeVert:
    __slots__ = ("co", "index")

    def __init__(self, co: tuple[float, float, float], index: int):
        self.co = co
        self.index = index


class _FakeFace:
    __slots__ = ("verts",)

    def __init__(self, verts: list[_FakeVert]):
        self.verts = verts


class _VertSeq:
    def __init__(self, owner: "_FakeBMesh"):
        self._owner = owner

    def new(self, co) -> _FakeVert:
        point = tuple(float(c) for c in co)
        if len(point) != 3:
            raise MeshCaptureError(f"verts.new() 需要三维坐标，得到: {co!r}")
        vert = _FakeVert(point, len(self._owner._verts))
        self._owner._verts.append(vert)
        return vert

    def ensure_lookup_table(self) -> None:
        pass

    def __getitem__(self, item):
        return self._owner._verts[item]

    def __len__(self) -> int:
        return len(self._owner._verts)

    def __iter__(self):
        return iter(self._owner._verts)


class _FaceSeq:
    def __init__(self, owner: "_FakeBMesh"):
        self._owner = owner

    def new(self, verts) -> _FakeFace:
        face = _FakeFace(list(verts))
        self._owner._faces.append(face)
        return face

    def __getitem__(self, item):
        return self._owner._faces[item]

    def __len__(self) -> int:
        return len(self._owner._faces)

    def __iter__(self):
        return iter(self._owner._faces)


class _FakeBMesh:
    def __init__(self):
        self._verts: list[_FakeVert] = []
        self._faces: list[_FakeFace] = []
        self.verts = _VertSeq(self)
        self.faces = _FaceSeq(self)

    def normal_update(self) -> None:
        pass

    def to_mesh(self, mesh: "_FakeMesh") -> None:
        mesh.polygons = [_FakePolygon(f) for f in self._faces]

    def free(self) -> None:
        pass


class _BmeshModule(types.ModuleType):
    """Stand-in for the ``bmesh`` module; records created BMeshes."""

    def __init__(self, registry: list[_FakeBMesh]):
        super().__init__("bmesh")
        self._registry = registry
        self.ops = types.SimpleNamespace(
            recalc_face_normals=lambda *a, **k: None,
        )

    def new(self) -> _FakeBMesh:
        bm = _FakeBMesh()
        self._registry.append(bm)
        return bm


# ── Fake bpy ────────────────────────────────────────────────


class _FakePolygon:
    def __init__(self, face: _FakeFace):
        self.vertices = list(face.verts)
        self.use_smooth = False


class _FakeMesh:
    def __init__(self, name: str):
        self.name = name
        self.polygons: list[_FakePolygon] = []

    def update(self) -> None:
        pass


class _FakeObject:
    def __init__(self, name: str, data=None):
        self.name = name
        self.data = data

    def select_set(self, value) -> None:
        pass


class _LinkableObjects:
    def link(self, obj) -> None:
        pass

    def unlink(self, obj) -> None:
        pass


class _FakeCollection:
    def __init__(self, name: str):
        self.name = name
        self.objects = _LinkableObjects()
        self.children = _LinkableObjects()


class _ObjectsContainer:
    def __init__(self):
        self._items: list[_FakeObject] = []

    def __iter__(self):
        return iter(list(self._items))

    def __len__(self) -> int:
        return len(self._items)

    def new(self, name: str, data=None) -> _FakeObject:
        obj = _FakeObject(name, data)
        self._items.append(obj)
        return obj

    def remove(self, obj, do_unlink: bool = False) -> None:
        if obj in self._items:
            self._items.remove(obj)


class _MeshesContainer:
    def __init__(self):
        self._items: list[_FakeMesh] = []

    def __iter__(self):
        return iter(list(self._items))

    def __len__(self) -> int:
        return len(self._items)

    def new(self, name: str) -> _FakeMesh:
        mesh = _FakeMesh(name)
        self._items.append(mesh)
        return mesh

    def remove(self, mesh, do_unlink: bool = False) -> None:
        if mesh in self._items:
            self._items.remove(mesh)


class _CollectionsContainer:
    def __init__(self):
        self._items: dict[str, _FakeCollection] = {}

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._items[name]

    def new(self, name: str) -> _FakeCollection:
        col = _FakeCollection(name)
        self._items[name] = col
        return col


class _BpyData:
    def __init__(self):
        self.objects = _ObjectsContainer()
        self.meshes = _MeshesContainer()
        self.collections = _CollectionsContainer()


def _make_bpy_module() -> types.ModuleType:
    mod = types.ModuleType("bpy")
    mod.data = _BpyData()
    mod.context = types.SimpleNamespace(
        scene=types.SimpleNamespace(collection=_FakeCollection("__scene__")),
        view_layer=types.SimpleNamespace(
            objects=types.SimpleNamespace(active=None),
        ),
    )
    mod.ops = types.SimpleNamespace()
    return mod


# ── Public entry point ──────────────────────────────────────


def run_mesh_capture(code: str) -> CapturedMesh:
    """Execute *code* with stub bpy/bmesh and return the recorded mesh.

    Raises:
        MeshCaptureError: the script raised, or produced no geometry.
    """
    created: list[_FakeBMesh] = []
    stubs = {
        "bpy": _make_bpy_module(),
        "bmesh": _BmeshModule(created),
    }
    saved: dict[str, types.ModuleType | None] = {}
    for name, mod in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod
    try:
        namespace: dict = {"__name__": "__bs2g_mesh__"}
        exec(compile(code, "<blender_script>", "exec"), namespace)  # noqa: S102
    except MeshCaptureError:
        raise
    except Exception as exc:
        last_line = traceback.format_exc().strip().splitlines()[-1]
        raise MeshCaptureError(f"脚本执行失败: {last_line}") from exc
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    verts: list[tuple[float, float, float]] = []
    faces: list[list[int]] = []
    for bm in created:
        base = len(verts)
        verts.extend(v.co for v in bm._verts)
        faces.extend([[base + v.index for v in f.verts] for f in bm._faces])

    if not verts or not faces:
        raise MeshCaptureError(
            "脚本未产生任何 bmesh 几何（未捕获到顶点/面）"
        )
    return CapturedMesh(verts=verts, faces=faces)
