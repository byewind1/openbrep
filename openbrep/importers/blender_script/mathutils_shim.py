"""
Pure-Python ``mathutils`` stand-in for BS2G script execution.

Blender build scripts do ``import mathutils`` for ``Vector`` /
``Matrix`` linear algebra.  This module implements the subset used by
real-world scripts (Vector arithmetic, Matrix construction/rotation/
translation/inversion) with mathutils-compatible semantics — no
geometry knowledge, just linear algebra.
"""

from __future__ import annotations

import math
import types

__all__ = ["Vector", "Matrix", "make_mathutils_module"]


class Vector:
    """Minimal mathutils.Vector (2D/3D/4D float vector, mutable)."""

    __slots__ = ("_v",)

    def __init__(self, seq=(0.0, 0.0, 0.0)):
        if isinstance(seq, Vector):
            seq = seq._v
        self._v = [float(c) for c in seq]

    # ── basics ──────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._v)

    def __iter__(self):
        return iter(self._v)

    def __getitem__(self, i):
        return self._v[i]

    def __setitem__(self, i, value) -> None:
        self._v[i] = float(value)

    def __repr__(self) -> str:
        return f"Vector({tuple(self._v)!r})"

    def _coerce(self, other) -> list[float]:
        if isinstance(other, Vector):
            return other._v
        return [float(c) for c in other]

    def _check_same(self, other: "Vector") -> None:
        if len(self) != len(other):
            raise ValueError("Vector: dimension mismatch")

    @property
    def x(self) -> float:
        return self._v[0]

    @x.setter
    def x(self, value: float) -> None:
        self._v[0] = float(value)

    @property
    def y(self) -> float:
        return self._v[1]

    @y.setter
    def y(self, value: float) -> None:
        self._v[1] = float(value)

    @property
    def z(self) -> float:
        return self._v[2]

    @z.setter
    def z(self, value: float) -> None:
        self._v[2] = float(value)

    @property
    def w(self) -> float:
        return self._v[3]

    @w.setter
    def w(self, value: float) -> None:
        self._v[3] = float(value)

    @property
    def xyz(self) -> "Vector":
        return Vector(self._v[:3])

    # ── arithmetic ──────────────────────────────────────────
    def __add__(self, other) -> "Vector":
        o = Vector(self._coerce(other))
        self._check_same(o)
        return Vector(a + b for a, b in zip(self._v, o._v))

    def __sub__(self, other) -> "Vector":
        o = Vector(self._coerce(other))
        self._check_same(o)
        return Vector(a - b for a, b in zip(self._v, o._v))

    def __neg__(self) -> "Vector":
        return Vector(-a for a in self._v)

    def __mul__(self, other):
        if isinstance(other, (int, float)):
            return Vector(a * other for a in self._v)
        o = Vector(self._coerce(other))
        self._check_same(o)
        return Vector(a * b for a, b in zip(self._v, o._v))

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        if isinstance(other, (int, float)):
            return Vector(a / other for a in self._v)
        o = Vector(self._coerce(other))
        self._check_same(o)
        return Vector(a / b for a, b in zip(self._v, o._v))

    def __matmul__(self, other) -> float:
        return self.dot(other)

    # ── vector ops ──────────────────────────────────────────
    def dot(self, other) -> float:
        o = Vector(self._coerce(other))
        self._check_same(o)
        return sum(a * b for a, b in zip(self._v, o._v))

    def cross(self, other) -> "Vector":
        o = Vector(self._coerce(other))
        if len(self) != 3 or len(o) != 3:
            raise ValueError("cross() needs 3D vectors")
        a, b = self._v, o._v
        return Vector((
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ))

    @property
    def length(self) -> float:
        return math.sqrt(self.dot(self))

    def normalize(self) -> None:
        """Normalize in place (mathutils semantics)."""
        n = self.length
        if n > 0.0:
            for i in range(len(self._v)):
                self._v[i] /= n
        return None

    def normalized(self) -> "Vector":
        n = self.length
        if n <= 0.0:
            return Vector(self._v)
        return self / n

    def copy(self) -> "Vector":
        return Vector(self._v)

    def lerp(self, other, t: float) -> "Vector":
        o = Vector(self._coerce(other))
        return self + (o - self) * t

    def to_tuple(self) -> tuple[float, ...]:
        return tuple(self._v)

    def to_track_quat(self, track: str = "Z", up: str = "Y") -> "Quaternion":
        """Rotation taking the *track* axis onto this vector (up-hinted)."""
        f = self.normalized()
        upv = _AXIS_VECTORS[up.upper()]
        t = f
        side = upv.cross(t)
        if side.length < 1e-12:
            # vector parallel to up — pick any orthogonal fallback
            alt = _AXIS_VECTORS["X"] if up.upper() != "X" else _AXIS_VECTORS["Y"]
            side = alt.cross(t)
        side = side.normalized()
        third = t.cross(side).normalized()
        axes = {"X": side, "Y": third, "Z": t}
        if track.upper() == "X":
            basis = (t, third, side)
        elif track.upper() == "Y":
            basis = (side, t, third)
        else:
            basis = (side, third, t)
        rows = [
            [basis[0].x, basis[1].x, basis[2].x],
            [basis[0].y, basis[1].y, basis[2].y],
            [basis[0].z, basis[1].z, basis[2].z],
        ]
        return Quaternion(Matrix(rows))


_AXIS_VECTORS = {
    "X": Vector((1.0, 0.0, 0.0)),
    "Y": Vector((0.0, 1.0, 0.0)),
    "Z": Vector((0.0, 0.0, 1.0)),
}


class Quaternion:
    """Minimal mathutils.Quaternion — matrix-backed."""

    __slots__ = ("_m",)

    def __init__(self, matrix: "Matrix | None" = None):
        self._m = matrix.to_3x3() if isinstance(matrix, Matrix) else Matrix.Identity(3)

    def to_matrix(self) -> "Matrix":
        return Matrix(self._m)

    def to_euler(self) -> tuple[float, float, float]:
        m = self._m._rows
        import math as _math

        sy = -m[2][0]
        cy = _math.sqrt(max(0.0, 1.0 - sy * sy))
        if cy > 1e-9:
            rx = _math.atan2(m[2][1], m[2][2])
            ry = _math.asin(sy)
            rz = _math.atan2(m[1][0], m[0][0])
        else:
            rx = _math.atan2(-m[1][2], m[1][1])
            ry = _math.asin(sy)
            rz = 0.0
        return (rx, ry, rz)

    def to_4d(self) -> "Vector":
        if len(self) == 4:
            return Vector(self._v)
        return Vector(self._v + (1.0,))

    def to_3d(self) -> "Vector":
        return Vector(self._v[:3])


class Matrix:
    """Minimal mathutils.Matrix (3x3 / 4x4, row-major)."""

    __slots__ = ("_rows",)

    def __init__(self, rows=None):
        if rows is None:
            self._rows = [list(r) for r in _identity_rows(4)]
        elif isinstance(rows, Matrix):
            self._rows = [list(r) for r in rows._rows]
        else:
            self._rows = [[float(c) for c in row] for row in rows]
        n = len(self._rows)
        if n not in (2, 3, 4) or any(len(r) != n for r in self._rows):
            raise ValueError("Matrix: rows must form a square 2/3/4 matrix")

    # ── basics ──────────────────────────────────────────────
    def __getitem__(self, i):
        return Vector(self._rows[i])

    def __repr__(self) -> str:
        return f"Matrix({self._rows!r})"

    @property
    def _n(self) -> int:
        return len(self._rows)

    @property
    def translation(self) -> Vector:
        return Vector(row[3] for row in self._rows[:3])

    # ── class constructors ──────────────────────────────────
    @staticmethod
    def Identity(n: int = 4) -> "Matrix":
        return Matrix(_identity_rows(n))

    @staticmethod
    def Rotation(radians: float, size: int = 4, axis="Z") -> "Matrix":
        if isinstance(axis, str):
            idx = "XYZ".index(axis.upper())
            vec = [0.0, 0.0, 0.0]
            vec[idx] = 1.0
            axis = vec
        ax = Vector(axis).normalized()
        x, y, z = ax.x, ax.y, ax.z
        c = math.cos(radians)
        s = math.sin(radians)
        t = 1.0 - c
        r3 = [
            [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
            [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
        ]
        if size == 3:
            return Matrix(r3)
        if size == 2:
            return Matrix([[c, -s], [s, c]])
        return Matrix(_embed4(r3))

    @staticmethod
    def Translation(vec) -> "Matrix":
        v = Vector(vec)
        rows = _identity_rows(4)
        for i in range(3):
            rows[i][3] = v[i]
        return Matrix(rows)

    @staticmethod
    def LocRotScale(loc, rot, scale) -> "Matrix":
        t = Matrix.Translation(loc if loc is not None else (0, 0, 0))
        r = rot if isinstance(rot, Matrix) else Matrix.Identity(4)
        if r._n == 3:
            r = Matrix(_embed4(r._rows))
        if scale is None:
            s = Matrix.Identity(4)
        else:
            v = Vector(scale)
            rows = _identity_rows(4)
            for i in range(3):
                rows[i][i] = v[i]
            s = Matrix(rows)
        return t @ r @ s

    # ── algebra ─────────────────────────────────────────────
    def __matmul__(self, other):
        if isinstance(other, Matrix):
            n = self._n
            if other._n != n:
                raise ValueError("Matrix @ Matrix: dimension mismatch")
            a, b = self._rows, other._rows
            cols = list(zip(*b))  # cols[j] = j-th column of b
            return Matrix(
                [
                    [sum(a[i][k] * cols[j][k] for k in range(n)) for j in range(n)]
                    for i in range(n)
                ]
            )
        # Matrix @ Vector
        v = Vector(other)
        n = self._n
        if len(v) == n:
            return Vector(sum(self._rows[i][k] * v[k] for k in range(n)) for i in range(n))
        if n == 4 and len(v) == 3:
            # point transform with implicit w=1 and w-divide
            out = []
            w = self._rows[3][3] + sum(self._rows[3][k] * v[k] for k in range(3))
            for i in range(3):
                out.append(self._rows[i][3] + sum(self._rows[i][k] * v[k] for k in range(3)))
            if abs(w) > 1e-15 and abs(w - 1.0) > 1e-15:
                out = [c / w for c in out]
            return Vector(out)
        raise ValueError("Matrix @ Vector: dimension mismatch")

    def transposed(self) -> "Matrix":
        return Matrix(list(map(list, zip(*self._rows))))

    def inverted(self) -> "Matrix":
        n = self._n
        aug = [row[:] + ident for row, ident in zip(self._rows, _identity_rows(n))]
        for col in range(n):
            piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
            if abs(aug[piv][col]) < 1e-15:
                raise ValueError("Matrix.inverted(): singular matrix")
            aug[col], aug[piv] = aug[piv], aug[col]
            d = aug[col][col]
            aug[col] = [c / d for c in aug[col]]
            for r in range(n):
                if r != col and aug[r][col] != 0.0:
                    f = aug[r][col]
                    aug[r] = [c - f * p for c, p in zip(aug[r], aug[col])]
        return Matrix([row[n:] for row in aug])

    def to_4x4(self) -> "Matrix":
        if self._n == 4:
            return Matrix(self)
        return Matrix(_embed4(self._rows))

    def to_3x3(self) -> "Matrix":
        return Matrix([row[:3] for row in self._rows[:3]])


def _identity_rows(n: int) -> list[list[float]]:
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _embed4(rows3) -> list[list[float]]:
    rows = [list(r) + [0.0] for r in rows3]
    rows.append([0.0, 0.0, 0.0, 1.0])
    return rows


def make_mathutils_module() -> types.ModuleType:
    """Build the stub ``mathutils`` module for exec injection."""
    mod = types.ModuleType("mathutils")
    mod.Vector = Vector
    mod.Matrix = Matrix
    mod.Quaternion = Quaternion
    return mod
