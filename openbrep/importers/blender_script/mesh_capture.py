"""
BS2G mesh capture v2 — execute a Blender script with fake bpy/bmesh/mathutils.

The stub records geometry per OBJECT (mesh datablock + matrix_world),
then merges all visible objects into one world-space CapturedMesh —
mirroring what the Blender viewport would show:

- ``bm.to_mesh(mesh)`` copies bmesh content into the mesh datablock
- ``bpy.data.objects.new(name, mesh)`` links mesh to an object
- ``obj.matrix_world`` (mathutils shim Matrix) places it in the scene
- removed objects are excluded; boolean modifiers are stubbed as no-op
  and reported in ``CapturedMesh.warnings``

Trust model: the script runs with full Python privileges, exactly as if
the user pressed "Run Script" in Blender.  Only convert scripts you
would run in Blender.
"""

from __future__ import annotations

import math
import sys
import traceback
import types
from dataclasses import dataclass, field

from openbrep.importers.blender_script.mathutils_shim import (
    Matrix,
    Vector,
    make_mathutils_module,
)


class MeshCaptureError(Exception):
    """The script could not be executed or produced no mesh geometry."""


@dataclass
class CapturedMesh:
    """World-space mesh merged from all visible scene objects."""

    verts: list[tuple[float, float, float]] = field(default_factory=list)
    faces: list[list[int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── Fake bmesh ──────────────────────────────────────────────


class _FakeVert:
    __slots__ = ("co", "index")

    def __init__(self, co: tuple[float, float, float], index: int):
        self.co = Vector(co)
        self.index = index


class _FakeLoop:
    """One face corner; supports ``loop[uv_layer].uv = (u, v)``."""

    __slots__ = ("uv",)

    def __init__(self):
        self.uv = (0.0, 0.0)

    def __getitem__(self, _layer):
        return self


class _FakeFace:
    __slots__ = ("verts", "loops")

    def __init__(self, verts: list[_FakeVert]):
        self.verts = verts
        self.loops = [_FakeLoop() for _ in verts]


class _VertSeq:
    def __init__(self, owner: "_FakeBMesh"):
        self._owner = owner

    def new(self, co) -> _FakeVert:
        point = Vector(co)
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


class _UvLayer:
    def verify(self) -> "_UvLayer":
        return self


class _FakeLoops:
    def __init__(self):
        self.layers = types.SimpleNamespace(uv=_UvLayer())


class _FakeEdge:
    __slots__ = ("verts", "link_faces")

    def __init__(self, verts: tuple, link_faces: list):
        self.verts = verts
        self.link_faces = link_faces


class _EdgeSeq:
    """bm.edges — edges derived (lazily) from the bmesh's faces."""

    def __init__(self, owner: "_FakeBMesh"):
        self._owner = owner

    def _materialize(self) -> list[_FakeEdge]:
        table: dict[tuple[int, int], _FakeEdge] = {}
        for face in self._owner._faces:
            ids = [v.index for v in face.verts]
            for k in range(len(ids)):
                a, b = ids[k], ids[(k + 1) % len(ids)]
                key = (a, b) if a < b else (b, a)
                edge = table.get(key)
                if edge is None:
                    edge = _FakeEdge(
                        (self._owner._verts[a], self._owner._verts[b]), []
                    )
                    table[key] = edge
                edge.link_faces.append(face)
        return list(table.values())

    def __iter__(self):
        return iter(self._materialize())

    def __len__(self) -> int:
        return len(self._materialize())

    def __getitem__(self, item):
        return self._materialize()[item]


class _FakeBMesh:
    def __init__(self):
        self._verts: list[_FakeVert] = []
        self._faces: list[_FakeFace] = []
        self.verts = _VertSeq(self)
        self.faces = _FaceSeq(self)
        self.edges = _EdgeSeq(self)
        self.loops = _FakeLoops()

    def normal_update(self) -> None:
        pass

    def to_mesh(self, mesh: "_FakeMesh") -> None:
        """Replace the mesh datablock content with this bmesh."""
        mesh._set_geometry(
            [v.co for v in self._verts],
            [[v.index for v in f.verts] for f in self._faces],
        )

    def free(self) -> None:
        pass


# ── bmesh.ops ───────────────────────────────────────────────


def _ops_create_cone(bm, cap_ends=True, cap_tris=False, segments=32,
                     radius1=1.0, radius2=0.0, depth=2.0, matrix=None, **_kw):
    """bmesh.ops.create_cone — bottom ring at z=-depth/2 (radius1)."""
    seg = max(3, int(segments))
    bottom = [
        bm.verts.new((radius1 * math.cos(2 * math.pi * i / seg),
                      radius1 * math.sin(2 * math.pi * i / seg), -depth / 2.0))
        for i in range(seg)
    ]
    top = [
        bm.verts.new((radius2 * math.cos(2 * math.pi * i / seg),
                      radius2 * math.sin(2 * math.pi * i / seg), depth / 2.0))
        for i in range(seg)
    ]
    for i in range(seg):
        j = (i + 1) % seg
        bm.faces.new([bottom[i], bottom[j], top[j], top[i]])
    if cap_ends:
        if radius1 > 0:
            bm.faces.new(list(reversed(bottom)))
        if radius2 > 0:
            bm.faces.new(list(top))
    if matrix is not None:
        _transform_bm(bm, matrix)
    return {"verts": bottom + top}


def _ops_create_uvsphere(bm, u_segments=32, v_segments=16, radius=1.0, matrix=None, **_kw):
    """bmesh.ops.create_uvsphere — poles at ±z."""
    nu = max(3, int(u_segments))
    nv = max(3, int(v_segments))
    south = bm.verts.new((0.0, 0.0, -radius))
    rings: list[list] = []
    for k in range(1, nv):
        phi = math.pi * k / nv
        r = radius * math.sin(phi)
        z = -radius * math.cos(phi)
        rings.append([
            bm.verts.new((r * math.cos(2 * math.pi * i / nu),
                          r * math.sin(2 * math.pi * i / nu), z))
            for i in range(nu)
        ])
    north = bm.verts.new((0.0, 0.0, radius))

    first = rings[0]
    for i in range(nu):
        j = (i + 1) % nu
        bm.faces.new([south, first[j], first[i]])
    for k in range(len(rings) - 1):
        a, b = rings[k], rings[k + 1]
        for i in range(nu):
            j = (i + 1) % nu
            bm.faces.new([a[i], a[j], b[j], b[i]])
    last = rings[-1]
    for i in range(nu):
        j = (i + 1) % nu
        bm.faces.new([last[i], last[j], north])
    if matrix is not None:
        _transform_bm(bm, matrix)
    return {"verts": [south] + [v for ring in rings for v in ring] + [north]}


def _ops_create_cube(bm, size=1.0, matrix=None, **_kw):
    h = size / 2.0
    vs = [
        bm.verts.new((x, y, z))
        for x in (-h, h) for y in (-h, h) for z in (-h, h)
    ]
    # index layout: 0:(-,-,-) 1:(-,-,+) 2:(-,+,-) 3:(-,+,+) 4:(+,-,-) 5:(+,-,+) 6:(+,+,-) 7:(+,+,+)
    quads = [
        (0, 2, 3, 1), (4, 5, 7, 6),
        (0, 1, 5, 4), (2, 6, 7, 3),
        (0, 4, 6, 2), (1, 3, 7, 5),
    ]
    for q in quads:
        bm.faces.new([vs[i] for i in q])
    if matrix is not None:
        _transform_bm(bm, matrix)
    return {"verts": vs}


def _transform_bm(bm, matrix) -> None:
    for v in bm._verts:
        v.co = matrix @ v.co


class _BmeshOps:
    def __init__(self, warnings: list[str]):
        self._warnings = warnings

    def recalc_face_normals(self, *a, **k) -> None:
        pass

    def remove_doubles(self, *a, **k) -> None:
        pass

    def delete(self, *a, **k) -> None:
        pass

    def create_cone(self, bm, **kw):
        return _ops_create_cone(bm, **kw)

    def create_uvsphere(self, bm, **kw):
        return _ops_create_uvsphere(bm, **kw)

    def create_cube(self, bm, **kw):
        return _ops_create_cube(bm, **kw)


class _BmeshModule(types.ModuleType):
    """Stand-in for the ``bmesh`` module."""

    def __init__(self, warnings: list[str], registry: list["_FakeBMesh"]):
        super().__init__("bmesh")
        self.ops = _BmeshOps(warnings)
        self._registry = registry

    def new(self) -> _FakeBMesh:
        bm = _FakeBMesh()
        self._registry.append(bm)
        return bm


# ── Fake bpy: mesh datablocks ───────────────────────────────


class _FakePolygon:
    def __init__(self, indices: list[int], mesh: "_FakeMesh"):
        self.vertices = list(indices)
        self._mesh = mesh
        self.use_smooth = False

    @property
    def center(self) -> Vector:
        pts = [self._mesh.verts[i] for i in self.vertices]
        n = len(pts)
        return Vector((
            sum(p[0] for p in pts) / n,
            sum(p[1] for p in pts) / n,
            sum(p[2] for p in pts) / n,
        ))

    @property
    def normal(self) -> Vector:
        pts = [self._mesh.verts[i] for i in self.vertices]
        nx = ny = nz = 0.0
        for k in range(len(pts)):
            x1, y1, z1 = pts[k]
            x2, y2, z2 = pts[(k + 1) % len(pts)]
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        return Vector((nx, ny, nz)).normalized()


class _MeshVertSeq:
    """mesh.vertices accessor (read-only, .co per vertex)."""

    def __init__(self, mesh: "_FakeMesh"):
        self._mesh = mesh

    def __len__(self) -> int:
        return len(self._mesh.verts)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return [self[i] for i in range(*item.indices(len(self)))]
        return types.SimpleNamespace(co=Vector(self._mesh.verts[item]), index=item)

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]


class _FakeMesh:
    def __init__(self, name: str):
        self.name = name
        self.verts: list[tuple[float, float, float]] = []
        self.faces: list[list[int]] = []
        self.polygons: list[_FakePolygon] = []
        self.materials: list = []

    @property
    def vertices(self) -> _MeshVertSeq:
        return _MeshVertSeq(self)

    def _set_geometry(self, verts, faces) -> None:
        self.verts = [tuple(v) for v in verts]
        self.faces = [list(f) for f in faces]
        self.polygons = [_FakePolygon(f, self) for f in faces]

    def update(self, *a, **k) -> None:
        pass


# ── Fake bpy: objects / collections ─────────────────────────


class _FakeModifier:
    def __init__(self, name: str, type_: str):
        self.name = name
        self.type = type_
        self.show_viewport = True


class _ModifiersContainer:
    def __init__(self, warnings: list[str]):
        self._warnings = warnings

    def new(self, name: str, type: str = "", **kw) -> _FakeModifier:
        type_ = type or kw.get("type_", "")
        if str(type_).upper() == "BOOLEAN":
            self._warnings.append(
                "布尔修改器未求值（BOOLEAN modifier 以 no-op 处理，输出为未切割几何）"
            )
        return _FakeModifier(name, type_)

    def remove(self, *a, **k) -> None:
        pass

    def clear(self) -> None:
        pass


class _FakeObject:
    def __init__(self, name: str, data, warnings: list[str]):
        self.name = name
        self.data = data
        self.type = "MESH" if data is not None else "EMPTY"
        self._matrix_world = Matrix.Identity(4)
        self._matrix_world_set = False
        self.location = Vector((0.0, 0.0, 0.0))
        self.rotation_euler = [0.0, 0.0, 0.0]
        self.scale = Vector((1.0, 1.0, 1.0))
        self.modifiers = _ModifiersContainer(warnings)
        self.hide_viewport = False
        self.hide_render = False

    @property
    def matrix_world(self) -> Matrix:
        return self._matrix_world

    @matrix_world.setter
    def matrix_world(self, value: Matrix) -> None:
        self._matrix_world = value
        self._matrix_world_set = True

    def world_matrix(self) -> Matrix:
        """Effective placement: explicit matrix_world wins, otherwise
        compose from location / rotation_euler (XYZ) / scale."""
        if self._matrix_world_set:
            return self._matrix_world
        rx, ry, rz = (float(a) for a in self.rotation_euler)
        rot = (
            Matrix.Rotation(rz, 4, "Z")
            @ Matrix.Rotation(ry, 4, "Y")
            @ Matrix.Rotation(rx, 4, "X")
        )
        return Matrix.LocRotScale(self.location, rot, self.scale)

    def select_set(self, value) -> None:
        pass

    def hide_set(self, value) -> None:
        self.hide_viewport = bool(value)

    @property
    def users_collection(self) -> list:
        # Membership back-references are not tracked by the shim
        return []

    @property
    def bound_box(self) -> list[Vector]:
        verts = getattr(self.data, "verts", None) or []
        if not verts:
            return [Vector((0.0, 0.0, 0.0))] * 8
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        zs = [v[2] for v in verts]
        lo = (min(xs), min(ys), min(zs))
        hi = (max(xs), max(ys), max(zs))
        return [
            Vector((x, y, z))
            for x in (lo[0], hi[0])
            for y in (lo[1], hi[1])
            for z in (lo[2], hi[2])
        ]

    def evaluated_get(self, _depsgraph):
        # Modifier evaluation is not implemented — the "evaluated"
        # object is the object itself (modifiers no-op).
        return self

    def to_mesh(self, **_kw):
        return self.data

    def to_mesh_clear(self) -> None:
        pass


class _LinkableObjects:
    """Collection membership — real storage so scripts can iterate it."""

    def __init__(self):
        self._items: list = []

    def link(self, obj) -> None:
        if obj not in self._items:
            self._items.append(obj)

    def unlink(self, obj) -> None:
        if obj in self._items:
            self._items.remove(obj)

    def __iter__(self):
        return iter(list(self._items))

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, item):
        return self._items[item]


class _FakeCollection:
    def __init__(self, name: str):
        self.name = name
        self.objects = _LinkableObjects()
        self.children = _LinkableObjects()

    @property
    def all_objects(self) -> _LinkableObjects:
        # Children are never populated by the shim — same as objects
        return self.objects


class _ObjectsContainer:
    def __init__(self, warnings: list[str]):
        self._items: list[_FakeObject] = []
        self._warnings = warnings

    def __iter__(self):
        return iter(list(self._items))

    def __len__(self) -> int:
        return len(self._items)

    def new(self, name: str, data=None) -> _FakeObject:
        obj = _FakeObject(name, data, self._warnings)
        self._items.append(obj)
        return obj

    def get(self, name: str, default=None):
        for obj in self._items:
            if obj.name == name:
                return obj
        return default

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

    def get(self, name: str, default=None):
        for mesh in self._items:
            if mesh.name == name:
                return mesh
        return default

    def remove(self, mesh, do_unlink: bool = False) -> None:
        if mesh in self._items:
            self._items.remove(mesh)

    def new_from_object(self, obj, preserve_all_data_layers=False, depsgraph=None):
        """Copy of the object's mesh (modifiers are not evaluated)."""
        src = getattr(obj, "data", None)
        mesh = _FakeMesh(f"{getattr(obj, 'name', 'object')}_eval")
        if src is not None and getattr(src, "verts", None):
            mesh._set_geometry(list(src.verts), [list(f) for f in src.faces])
        self._items.append(mesh)
        return mesh


class _CollectionsContainer:
    def __init__(self):
        self._items: dict[str, _FakeCollection] = {}

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._items[name]

    def get(self, name: str, default=None):
        return self._items.get(name, default)

    def new(self, name: str) -> _FakeCollection:
        col = _FakeCollection(name)
        self._items[name] = col
        return col


class _CollectionsContainer:
    def __init__(self):
        self._items: dict[str, _FakeCollection] = {}

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def __getitem__(self, name: str) -> _FakeCollection:
        # Auto-vivify: build scripts often reference collections
        # created by an earlier script in the same Blender session.
        if name not in self._items:
            self._items[name] = _FakeCollection(name)
        return self._items[name]

    def get(self, name: str, default=None):
        return self._items.get(name, default)

    def new(self, name: str) -> _FakeCollection:
        return self[name]


class _SocketsDict:
    """node.inputs / node.outputs — auto-vivifying socket access."""

    def __init__(self):
        self._items: dict = {}
        self._order: list = []

    def __getitem__(self, key):
        if isinstance(key, int):
            while len(self._order) <= key:
                name = f"Socket{len(self._order)}"
                self._order.append(name)
                self._items[name] = types.SimpleNamespace(
                    name=name, default_value=None, is_linked=False,
                )
            return self._items[self._order[key]]
        if key not in self._items:
            self._items[key] = types.SimpleNamespace(
                name=key, default_value=None, is_linked=False,
            )
            self._order.append(key)
        return self._items[key]

    def get(self, name: str, default=None):
        return self[name]

    def __iter__(self):
        return iter([self._items[k] for k in self._order])

    def __len__(self) -> int:
        return len(self._order)


class _NodesContainer:
    """material/world node_tree.nodes — iterable dict of stub nodes."""

    def __init__(self):
        self._items: dict[str, types.SimpleNamespace] = {}

    def __iter__(self):
        return iter(list(self._items.values()))

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def __getitem__(self, name: str) -> types.SimpleNamespace:
        return self._items[name]

    def get(self, name: str, default=None):
        if name not in self._items:
            return self.new(name)
        return self._items[name]

    def new(self, type_name: str = "", **k) -> types.SimpleNamespace:
        name = str(type_name)
        node = types.SimpleNamespace(
            name=name, bl_idname=name, inputs=_SocketsDict(),
            outputs=_SocketsDict(), label="", hide=False,
        )
        self._items[name] = node
        return node

    def clear(self) -> None:
        self._items.clear()

    def remove(self, node) -> None:
        self._items.pop(getattr(node, "name", str(node)), None)


class _FakeMaterial:
    def __init__(self, name: str):
        self.name = name
        self.use_nodes = False
        self.diffuse_color = (0.8, 0.8, 0.8, 1.0)
        self.node_tree = types.SimpleNamespace(
            nodes=_NodesContainer(),
            links=types.SimpleNamespace(
                new=lambda *_a, **_k: None,
                clear=lambda: None,
            ),
        )
        # Blender gives every new node material a Principled BSDF +
        # Material Output pair by default.
        self.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        self.node_tree.nodes.new("ShaderNodeOutputMaterial")


class _MaterialsContainer:
    def __init__(self):
        self._items: dict[str, _FakeMaterial] = {}

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def __getitem__(self, name: str) -> _FakeMaterial:
        return self._items[name]

    def __iter__(self):
        return iter(list(self._items.values()))

    def get(self, name: str, default=None):
        # Auto-vivify: build scripts guard on materials created by an
        # earlier assign_* script in the same Blender session; for
        # geometry conversion a placeholder is good enough.
        if name not in self._items:
            self._items[name] = _FakeMaterial(name)
        return self._items[name]

    def new(self, name: str) -> _FakeMaterial:
        mat = _FakeMaterial(name)
        self._items[name] = mat
        return mat

    def remove(self, mat, **k) -> None:
        self._items.pop(getattr(mat, "name", mat), None)


class _BpyData:
    def __init__(self, warnings: list[str], filepath: str = ""):
        self.objects = _ObjectsContainer(warnings)
        self.meshes = _MeshesContainer()
        self.collections = _CollectionsContainer()
        self.materials = _MaterialsContainer()
        self.filepath = filepath


def _noop(*a, **k) -> None:
    return None


def _make_ops(data: "_BpyData", context) -> types.SimpleNamespace:
    """bpy.ops.* — side-effecting stubs that keep scripts running.

    Most ops are no-ops; the add-ops actually create objects so later
    ``context.active_object`` reads and scene traversal keep working.
    """

    def set_active(obj) -> None:
        context.view_layer.objects.active = obj
        context.active_object = obj
        context.object = obj

    def camera_add(location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0), **_kw):
        data_ns = types.SimpleNamespace(type="CAMERA", lens=50.0, clip_end=1000.0)
        obj = data.objects.new("Camera", data_ns)
        obj.type = "CAMERA"
        obj.location = Vector(location)
        obj.rotation_euler = list(rotation)
        set_active(obj)

    def light_add(type: str = "POINT", location=(0.0, 0.0, 0.0), **_kw):
        data_ns = types.SimpleNamespace(type=type, energy=1000.0, color=(1.0, 1.0, 1.0))
        obj = data.objects.new("Light", data_ns)
        obj.type = "LIGHT"
        obj.location = Vector(location)
        set_active(obj)

    def _primitive(kind: str, builder_kw: dict, location, rotation, scale):
        mesh = data.meshes.new(kind)
        bm = _FakeBMesh()
        _PRIMITIVE_BUILDERS[kind](bm, **builder_kw)
        bm.to_mesh(mesh)
        obj = data.objects.new(kind, mesh)
        obj.location = Vector(location)
        obj.rotation_euler = list(rotation)
        obj.scale = Vector(scale)
        set_active(obj)

    def primitive_cube_add(size: float = 2.0, location=(0, 0, 0),
                           rotation=(0, 0, 0), scale=(1, 1, 1), **_kw):
        _primitive("cube", {"size": size}, location, rotation, scale)

    def primitive_cone_add(vertices: int = 32, radius1: float = 1.0, radius2: float = 0.0,
                           depth: float = 2.0, location=(0, 0, 0),
                           rotation=(0, 0, 0), scale=(1, 1, 1), **_kw):
        _primitive("cone", {
            "segments": vertices, "radius1": radius1,
            "radius2": radius2, "depth": depth, "cap_ends": True,
        }, location, rotation, scale)

    def primitive_cylinder_add(vertices: int = 32, radius: float = 1.0,
                               depth: float = 2.0, location=(0, 0, 0),
                               rotation=(0, 0, 0), scale=(1, 1, 1), **_kw):
        _primitive("cone", {
            "segments": vertices, "radius1": radius,
            "radius2": radius, "depth": depth, "cap_ends": True,
        }, location, rotation, scale)

    def primitive_uv_sphere_add(segments: int = 32, ring_count: int = 16,
                                radius: float = 1.0, location=(0, 0, 0),
                                rotation=(0, 0, 0), scale=(1, 1, 1), **_kw):
        _primitive("uv_sphere", {
            "u_segments": segments, "v_segments": ring_count, "radius": radius,
        }, location, rotation, scale)

    return types.SimpleNamespace(
        wm=types.SimpleNamespace(
            redraw_timer=_noop,
            save_mainfile=_noop,
            read_homefile=_noop,
            read_factory_settings=_noop,
        ),
        object=types.SimpleNamespace(
            mode_set=_noop,
            select_all=_noop,
            origin_set=_noop,
            transform_apply=_noop,
            light_add=light_add,
            camera_add=camera_add,
            modifier_apply=_noop,
        ),
        mesh=types.SimpleNamespace(
            primitive_cube_add=primitive_cube_add,
            primitive_cone_add=primitive_cone_add,
            primitive_cylinder_add=primitive_cylinder_add,
            primitive_uv_sphere_add=primitive_uv_sphere_add,
        ),
    )


_PRIMITIVE_BUILDERS = {
    "cube": _ops_create_cube,
    "cone": _ops_create_cone,
    "uv_sphere": _ops_create_uvsphere,
}


def _make_bpy_module(warnings: list[str], filepath: str = "") -> types.ModuleType:
    mod = types.ModuleType("bpy")
    mod.data = _BpyData(warnings, filepath)
    mod.context = types.SimpleNamespace(
        scene=types.SimpleNamespace(
            collection=_FakeCollection("__scene__"),
            update=_noop,
            unit_settings=types.SimpleNamespace(
                system="METRIC",
                length_unit="METERS",
                scale_length=1.0,
            ),
            world=types.SimpleNamespace(
                node_tree=types.SimpleNamespace(
                    nodes=_NodesContainer(),
                    links=types.SimpleNamespace(
                        new=lambda *_a, **_k: None,
                        clear=lambda: None,
                    ),
                ),
            ),
            render=types.SimpleNamespace(
                filepath="",
                resolution_x=1920,
                resolution_y=1080,
                image_settings=types.SimpleNamespace(file_format="PNG"),
            ),
        ),
        view_layer=types.SimpleNamespace(
            objects=types.SimpleNamespace(active=None),
            update=_noop,
        ),
        mode="OBJECT",
        evaluated_depsgraph_get=lambda: types.SimpleNamespace(),
        object=None,
        active_object=None,
    )
    mod.ops = _make_ops(mod.data, mod.context)
    return mod


# ── Public entry point ──────────────────────────────────────


def run_mesh_capture(code: str, script_path: str | None = None) -> CapturedMesh:
    """Execute *code* with stub bpy/bmesh/mathutils; return world-space mesh.

    Args:
        code: Blender Python source.
        script_path: Optional path of the source file.  When given,
            ``bpy.data.filepath`` is set and the file's directory is
            prepended to ``sys.path`` during execution so scripts can
            import sibling helper modules (restored afterwards).

    Raises:
        MeshCaptureError: the script raised, or produced no geometry.
    """
    warnings: list[str] = []
    bpy_mod = _make_bpy_module(
        warnings, filepath=str(script_path) if script_path else ""
    )
    bms: list[_FakeBMesh] = []
    stubs = {
        "bpy": bpy_mod,
        "bmesh": _BmeshModule(warnings, bms),
        "mathutils": make_mathutils_module(),
    }
    saved: dict[str, types.ModuleType | None] = {}
    for name, mod in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    import os
    script_dir = (
        os.path.dirname(os.path.abspath(script_path)) if script_path else None
    )
    path_added = False
    try:
        if script_dir and script_dir not in sys.path:
            sys.path.insert(0, script_dir)
            path_added = True
        namespace: dict = {"__name__": "__bs2g_mesh__"}
        exec(compile(code, "<blender_script>", "exec"), namespace)  # noqa: S102
    except MeshCaptureError:
        raise
    except SystemExit as exc:
        raise MeshCaptureError(
            f"脚本以 sys.exit({exc.code}) 退出（可能依赖同一会话中其他脚本先运行的场景状态）"
        ) from exc
    except Exception as exc:
        last_line = traceback.format_exc().strip().splitlines()[-1]
        raise MeshCaptureError(f"脚本执行失败: {last_line}") from exc
    finally:
        if path_added:
            try:
                sys.path.remove(script_dir)
            except ValueError:
                pass
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    # Merge all visible objects into one world-space mesh
    verts: list[tuple[float, float, float]] = []
    faces: list[list[int]] = []
    objects_with_geometry = 0
    for obj in bpy_mod.data.objects:
        mesh = obj.data
        if mesh is None or not getattr(mesh, "verts", None):
            continue
        if getattr(obj, "hide_viewport", False) or getattr(obj, "hide_render", False):
            continue
        objects_with_geometry += 1
        M = obj.world_matrix()
        base = len(verts)
        for v in mesh.verts:
            w = M @ v
            verts.append((w.x, w.y, w.z))
        faces.extend([[base + i for i in f] for f in mesh.faces])

    if objects_with_geometry == 0:
        # Fallback: scripts that only build mesh datablocks without
        # linking them to objects (invisible in Blender, but common in
        # quick scripts) — capture the datablocks with identity transform.
        for mesh in bpy_mod.data.meshes:
            if not getattr(mesh, "verts", None):
                continue
            base = len(verts)
            verts.extend(tuple(v) for v in mesh.verts)
            faces.extend([[base + i for i in f] for f in mesh.faces])

    if not verts:
        # Last resort: raw bmesh content that never reached a datablock
        # (minimal scripts that stop before to_mesh).
        for bm in bms:
            base = len(verts)
            verts.extend(tuple(v.co) for v in bm._verts)
            faces.extend([[base + v.index for v in f.verts] for f in bm._faces])

    if not verts or not faces:
        raise MeshCaptureError(
            "脚本未产生任何网格几何（未捕获到顶点/面）"
        )
    # Deduplicate boolean warnings
    seen = set()
    unique_warnings = [w for w in warnings if not (w in seen or seen.add(w))]
    return CapturedMesh(verts=verts, faces=faces, warnings=unique_warnings)
