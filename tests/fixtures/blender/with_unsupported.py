def make_complex(size=1.0):
    bpy.ops.mesh.primitive_cube_add(size=size)
    bpy.ops.object.modifier_add(type='SUBSURF')
    bpy.ops.object.modifier_add(type='BOOLEAN')
