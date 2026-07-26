def make_bookshelf(width=0.9, depth=0.35, height=2.0, shelf_count=4, panel_thickness=0.018):
    # Side panels
    for side in [-1, 1]:
        bpy.ops.mesh.primitive_cube_add(size=1)
        panel = bpy.context.object
        panel.scale = (panel_thickness, depth, height)
        panel.location = (side * width / 2, 0, height / 2)

    # Shelves
    spacing = height / (shelf_count + 1)
    for i in range(shelf_count):
        bpy.ops.mesh.primitive_cube_add(size=1)
        shelf = bpy.context.object
        shelf.scale = (width, depth, panel_thickness)
        shelf.location = (0, 0, spacing * (i + 1))
