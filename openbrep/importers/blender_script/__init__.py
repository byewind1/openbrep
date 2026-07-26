"""
BS2G — Blender Script to GDL converter.

Converts Blender Python scripts (bpy.ops.mesh.primitive_*) into
GDL scripts + paramlist.xml via deterministic AST parsing and
rule-based mapping. No LLM in the core conversion path.
"""
