bl_info = {
    "name": "VRM Rigify",
    "author": "Nanoskript",
    "description": "Generates Rigify armatures for VRM models",
    "version": (0, 1, 1),
    "blender": (2, 80, 0),
    "location": "Operator Search > VRM Rigify",
    "doc_url": "https://github.com/Nanoskript/vrm-rigify",
    "tracker_url": "https://github.com/Nanoskript/vrm-rigify/issues",
    "category": "Rigging",
}

if "bpy" not in locals():
    import bpy
    from . import debug
    from . import base
    from . import gen
    from . import meta
    from . import other
    from . import humanize
else:
    import importlib

    importlib.reload(debug)
    importlib.reload(base)
    importlib.reload(gen)
    importlib.reload(meta)
    importlib.reload(other)
    importlib.reload(humanize)


class GenerateVRMRig(bpy.types.Operator):
    bl_idname = "vrm_rigify.create_rig"
    bl_label = "Generate Humanized VRM Rig"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Generate meta-rig.
        vroid_rig = context.view_layer.objects.active
        assert vroid_rig, "VRoid armature not selected"
        assert vroid_rig.type == 'ARMATURE', "Selected object not an armature"
        meta_rig = meta.generate_meta_rig(vroid_rig)
        meta_rig.select_set(True)

        # Generate rig.
        bpy.ops.pose.rigify_generate()
        gen_rig = bpy.context.view_layer.objects.active
        gen_rig.matrix_world = vroid_rig.matrix_world
        gen.setup_bones(vroid_rig, gen_rig)
        humanize.humanize(gen_rig)

        # Organize view.
        meta_rig.hide_set(True)
        vroid_rig.hide_set(True)
        return {'FINISHED'}


class GenerateVRMMetaRig(bpy.types.Operator):
    bl_idname = "vrm_rigify.create_meta"
    bl_label = "Generate VRM Meta-Rig"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vroid_rig = context.view_layer.objects.active
        assert vroid_rig, "VRoid armature not selected"
        assert vroid_rig.type == 'ARMATURE', "Selected object not an armature"
        meta.generate_meta_rig(vroid_rig)
        return {'FINISHED'}


class AmendVRMGeneratedRig(bpy.types.Operator):
    bl_idname = "vrm_rigify.amend_gen"
    bl_label = "Amend Generated VRM Rigify Rig"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = list(context.selected_objects)
        [gen_rig] = base.objects_by_pattern(selected, '.rig')
        [vroid_rig] = [node for node in selected if node != gen_rig]
        gen.setup_bones(vroid_rig, gen_rig)
        return {'FINISHED'}


class HumanizeGeneratedRig(bpy.types.Operator):
    bl_idname = "vrm_rigify.humanize_rig"
    bl_label = "Humanize Generated Rigify Rig"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        rig = context.view_layer.objects.active
        humanize.humanize(rig)
        return {'FINISHED'}


CLASSES = [
    GenerateVRMRig,
    GenerateVRMMetaRig,
    AmendVRMGeneratedRig,
    HumanizeGeneratedRig,
]


def register():
    for clazz in CLASSES:
        bpy.utils.register_class(clazz)


def unregister():
    for clazz in CLASSES:
        bpy.utils.unregister_class(clazz)
