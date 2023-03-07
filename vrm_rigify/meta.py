import bpy

from .base import BASE_IGNORED, create_bone_mapping, objects_by_patterns
from .debug import LOGGER
from .other import editing

BONES_DELETE = [
    'pelvis',
    'palm.01',
    'palm.02',
    'palm.03',
    'palm.04',
]

META_IGNORED = BASE_IGNORED + [
    # Assumed to be in correct position by default.
    'heel.02',
    # Unneeded facial features.
    # Expressions managed by shape keys.
    'face',
    'teeth',
]

LIMB_BONES = [
    'upper_arm',
    'thigh',
]

SUPER_FINGER_BONES = [
    'f_pinky.01',
    'f_ring.01',
    'f_middle.01',
    'f_index.01',
    'thumb.01',
]


def meta_rig_base_bones(meta_rig):
    for bone in meta_rig.data.edit_bones:
        if objects_by_patterns([bone], META_IGNORED):
            LOGGER.info(f"ignoring bone '{bone.name}'")
            continue
        yield bone


def position_meta_rig(meta_rig, vroid_rig):
    with editing(meta_rig):
        base_bones = meta_rig_base_bones(meta_rig)
        conversions = create_bone_mapping(base_bones, vroid_rig)

        # Position meta rig and move bones.
        meta_rig.matrix_world = vroid_rig.matrix_world
        for meta_bone, vroid_bone in conversions:
            LOGGER.info(f"positioning '{meta_bone.name}' to '{vroid_bone.name}'")
            meta_bone.select = True
            meta_bone.head = vroid_bone.head_local
            meta_bone.tail = vroid_bone.tail_local


# Note: transforms on the VRoid model must *not* be
# applied for the generated rig to be positioned correctly.
def generate_meta_rig(vroid_rig):
    # Simplify VRoid bone names.
    LOGGER.info("simplifying VRoid bone names")
    bpy.ops.vrm.bones_rename(armature_name=vroid_rig.name)

    # Spawn meta-rig.
    LOGGER.info("creating and positioning meta-rig")
    bpy.ops.object.armature_human_metarig_add()
    meta_rig = bpy.context.view_layer.objects.active
    meta_rig.name = f"{vroid_rig.name}.metarig"

    # Remove unneeded bones.
    with editing(meta_rig):
        edit_bones = meta_rig.data.edit_bones
        for bone in objects_by_patterns(edit_bones, BONES_DELETE):
            LOGGER.info(f"deleting meta-rig bone '{bone.name}'")
            edit_bones.remove(bone)

    # Align meta-rig bones with VRoid model.
    position_meta_rig(meta_rig, vroid_rig)

    # Amend armature limbs.
    pose_bones = meta_rig.pose.bones
    for bone in objects_by_patterns(pose_bones, LIMB_BONES):
        LOGGER.info(f"amending bone parameters for limb '{bone.name}'")
        # Amend resultant bone count.
        bone.rigify_parameters.segments = 1
        # Ensure local bend direction is correct.
        bone.rigify_parameters.rotation_axis = 'x'

    # Amend armature fingers.
    for bone in objects_by_patterns(pose_bones, SUPER_FINGER_BONES):
        LOGGER.info(f"amending bone parameters for finger '{bone.name}'")
        # Ensure primary bend direction is correct.
        bone.rigify_parameters.primary_rotation_axis = 'Z'

    # Return generated meta-rig object.
    LOGGER.info("meta-rig generated")
    return meta_rig
