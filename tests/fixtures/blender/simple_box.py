def make_box(width=1.0, depth=0.5, height=2.0):
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.scale = (width, depth, height)
    obj.location = (0, 0, height / 2)
