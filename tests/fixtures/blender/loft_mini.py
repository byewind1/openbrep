import bpy, bmesh, math

# 3 rings x 8 pts loft with triangle-fan caps (same idioms as build_BLADE_01)
N = 8
rings = []
for k in range(3):
    z = k * 0.5
    r = 1.0 - k * 0.2
    rings.append([
        (r * math.cos(2 * math.pi * i / N),
         r * math.sin(2 * math.pi * i / N), z)
        for i in range(N)
    ])

mesh = bpy.data.meshes.new("LOFT_MINI")
bm = bmesh.new()

v_rings = [[bm.verts.new(p) for p in ring] for ring in rings]
bm.verts.ensure_lookup_table()

for k in range(2):
    ra, rb = v_rings[k], v_rings[k + 1]
    for i in range(N):
        j = (i + 1) % N
        bm.faces.new([ra[i], ra[j], rb[j], rb[i]])

c0 = bm.verts.new((0.0, 0.0, 0.0))
for i in range(N):
    j = (i + 1) % N
    bm.faces.new([v_rings[0][j], v_rings[0][i], c0])

c1 = bm.verts.new((0.0, 0.0, 1.0))
for i in range(N):
    j = (i + 1) % N
    bm.faces.new([v_rings[-1][i], v_rings[-1][j], c1])

bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
bm.normal_update()
bm.to_mesh(mesh)
bm.free()
mesh.update()
