def make_rotated_box(size=1.0, angle_deg=30.0):
    import math
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.rotation_euler = (0, 0, math.radians(angle_deg))
    obj.location = (0, 0, size / 2)
