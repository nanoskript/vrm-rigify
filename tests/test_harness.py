import os
import sys

import addon_utils
import bpy

# Make the addon under test importable from the repository.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import vrm_rigify  # noqa: E402


def enable_addon(module_name: str):
    # Addons must be enabled with `default_set=True` so they are registered
    # in `preferences.addons`, which Rigify reads during registration.
    module = addon_utils.enable(module_name, default_set=True)
    assert module is not None, f"failed to enable addon '{module_name}'"


def enable_vrm_addon():
    # The VRM addon's module name differs between Blender versions: it is
    # installed as an extension (`bl_ext.user_default.vrm`) on Blender 4.2
    # and later, and as a legacy addon on earlier versions. Prefer the exact
    # extension name so a stale legacy install can never shadow it.
    module_names = [module.__name__ for module in addon_utils.modules()]
    candidates = [name for name in module_names if name == "bl_ext.user_default.vrm"]
    candidates += [name for name in module_names if "vrm" in name.lower()]
    for module_name in candidates:
        print(f"enabling VRM addon '{module_name}'")
        enable_addon(module_name)
        return
    raise Exception("no VRM addon is installed")


def import_vrm_model(model_path: str) -> bpy.types.Object:
    objects_before = set(bpy.data.objects)
    bpy.ops.import_scene.vrm(filepath=model_path)
    objects_imported = set(bpy.data.objects) - objects_before
    [vrm_object] = [node for node in objects_imported if node.type == "ARMATURE"]
    return vrm_object


def vrm_model_vertex_group_names(vrm_object: bpy.types.Object) -> set[str]:
    # Only vertex groups that actually have weights assigned
    # need a matching bone in the generated rig.
    names = set()
    for mesh in vrm_object.children_recursive:
        if mesh.type != "MESH":
            continue

        group_names_by_index = {group.index: group.name for group in mesh.vertex_groups}
        for vertex in mesh.data.vertices:
            for group in vertex.groups:
                if group.weight > 0.0:
                    names.add(group_names_by_index[group.group])
    return names


def check_control_bones_exist(rig_object: bpy.types.Object):
    control_bone_names = [
        "torso",
        "head",
        "hand_ik.L",
        "hand_ik.R",
        "foot_ik.L",
        "foot_ik.R",
    ]

    for bone_name in control_bone_names:
        assert bone_name in rig_object.data.bones, \
            f"control bone '{bone_name}' is missing from the generated rig"


def check_deform_coverage(rig_object: bpy.types.Object, vertex_group_names: set[str]):
    # Every vertex group used by the model's meshes must exist as a deforming
    # bone in the generated rig or else parts of the model will not follow the
    # rig. A bone with a matching name is not enough: the armature modifier
    # ignores bones that have deformation disabled.
    deform_bone_names = {bone.name for bone in rig_object.data.bones if bone.use_deform}
    missing = sorted(vertex_group_names - deform_bone_names)
    assert not missing, f"vertex groups have no matching deform bone: {missing}"


def check_shape_key_controls(rig_object: bpy.types.Object, vrm_object: bpy.types.Object):
    rig_extension = rig_object.data.vrm_addon_extension
    vrm_extension = vrm_object.data.vrm_addon_extension
    # Note: `.keys()` views on ID properties do not implement
    # equality so they must be converted into sets to be compared.
    rig_presets = set(rig_extension.vrm1["expressions"]["preset"].keys())
    vrm_presets = set(vrm_extension.vrm1["expressions"]["preset"].keys())
    assert len(vrm_presets) > 0, "model has no preset expressions"
    assert rig_presets == vrm_presets, \
        f"rig expressions do not match the model: {rig_presets ^ vrm_presets}"


def main():
    model_path = os.environ["VRM_TEST_MODEL_PATH"]

    enable_addon("rigify")
    enable_vrm_addon()
    vrm_rigify.register()

    # Start from an empty scene.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    vrm_object = import_vrm_model(model_path)
    vertex_group_names = vrm_model_vertex_group_names(vrm_object)

    # Generate the rig.
    objects_before = set(bpy.data.objects)
    bpy.context.view_layer.objects.active = vrm_object
    result = bpy.ops.vrm_rigify.create_rig()
    assert result == {"FINISHED"}

    # Find the generated rig.
    objects_generated = set(bpy.data.objects) - objects_before
    [rig_object] = [
        node for node in objects_generated
        if node.type == "ARMATURE" and not node.name.endswith(".metarig")
    ]

    check_control_bones_exist(rig_object)
    check_deform_coverage(rig_object, vertex_group_names)
    check_shape_key_controls(rig_object, vrm_object)

    print(f"model '{os.path.basename(model_path)}' passed all checks")


main()
