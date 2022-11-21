import bpy

from .base import BASE_IGNORED, create_bone_mapping, objects_by_patterns
from .other import editing

META_IGNORED = BASE_IGNORED + [
    # Assumed to be in correct position by default.
    'heel.02',
    # Unneeded facial features.
    # Expressions managed by shape keys.
    'face',
    'teeth',
]

BONES_DELETE = [
    'pelvis',
]

LIMB_BONES = [
    'upper_arm',
    'thigh',
]


def meta_rig_base_bones(meta_rig):
    for bone in meta_rig.data.edit_bones:
        if objects_by_patterns([bone], META_IGNORED):
            continue
        yield bone


def position_meta_rig(meta_rig, vroid_rig):
    with editing(meta_rig):
        base_bones = meta_rig_base_bones(meta_rig)
        conversions = create_bone_mapping(base_bones, vroid_rig)

        # Position meta rig and move bones.
        meta_rig.matrix_world = vroid_rig.matrix_world
        for meta_bone, vroid_bone in conversions:
            meta_bone.select = True
            meta_bone.head = vroid_bone.head_local
            meta_bone.tail = vroid_bone.tail_local


# Note: transforms on the VRoid model must *not* be
# applied for the generated rig to be positioned correctly.
def generate_meta_rig(vroid_rig):
    # Simplify VRoid bone names.
    bpy.ops.vrm.bones_rename(armature_name=vroid_rig.name)

    # Spawn meta-rig.
    bpy.ops.object.armature_human_metarig_add()
    meta_rig = bpy.context.view_layer.objects.active
    meta_rig.name = f"{vroid_rig.name}.metarig"
    position_meta_rig(meta_rig, vroid_rig)

    # Remove unneeded bones.
    with editing(meta_rig):
        edit_bones = meta_rig.data.edit_bones
        for bone in objects_by_patterns(edit_bones, BONES_DELETE):
            edit_bones.remove(bone)

    # Amend armature limbs.
    pose_bones = meta_rig.pose.bones
    for bone in objects_by_patterns(pose_bones, LIMB_BONES):
        # Amend resultant bone count.
        bone.rigify_parameters.segments = 1
        # Ensure local bend direction is correct.
        bone.rigify_parameters.rotation_axis = 'x'

    # Return generated meta-rig object.
    return meta_rig
