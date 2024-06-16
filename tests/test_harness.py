import os
import bpy

# Import VRM model and generate rig.
bpy.ops.import_scene.vrm(filepath=os.environ["VRM_TEST_MODEL_PATH"])
bpy.context.view_layer.objects.active = bpy.data.objects["Armature"]
bpy.data.objects.remove(bpy.data.objects["Cube"])
bpy.ops.vrm_rigify.create_rig()

# Parent meshes to generated rig.
for mesh in bpy.data.objects["Armature"].children:
    if mesh.type == "MESH":
        mesh.modifiers["Armature"].object = bpy.data.objects["Armature.rig"]
