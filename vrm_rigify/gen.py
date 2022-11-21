from .base import objects_by_pattern, objects_by_patterns, create_bone_mapping, BASE_IGNORED
from .other import editing

GEN_IGNORED = BASE_IGNORED + [
    'teeth',
]


def remove_gen_rig_ignored_bones(gen_rig):
    with editing(gen_rig):
        edit_bones = gen_rig.data.edit_bones
        ignored = objects_by_patterns(edit_bones, GEN_IGNORED)
        for bone in ignored:
            edit_bones.remove(bone)


def gen_rig_base_bones(gen_rig):
    for bone in gen_rig.data.bones:
        # Rigify eyes are not deforming by default.
        deform = bone.name.startswith('DEF')
        eye = objects_by_pattern([bone], 'ORG-eye')
        if deform or eye:
            yield bone


# Renames the generated rig bones to match VRoid model vertex groups.
# Bone positions are already aligned in the meta-rig.
def map_bones(conversions):
    for gen_bone, vroid_bone in conversions:
        gen_bone.name = vroid_bone.name
        gen_bone.use_deform = True


# Attached bones include hair bones.
def attach_remaining_bones(vroid_rig, gen_rig):
    with editing(gen_rig):
        # Assume retrieved bones are in traversal order.
        edit_bones = gen_rig.data.edit_bones
        for vroid_bone in vroid_rig.data.bones:
            exists = vroid_bone.name in edit_bones
            has_parent = bool(vroid_bone.parent)
            if exists or not has_parent:
                continue

            can_parent = vroid_bone.parent.name in edit_bones
            if not can_parent:
                continue

            bone = edit_bones.new(vroid_bone.name)
            bone.head = vroid_bone.head_local
            bone.tail = vroid_bone.tail_local

            parent = edit_bones[vroid_bone.parent.name]
            bone.layers = parent.layers
            bone.parent = parent


# Note: need to apply transforms on VRoid model
# before parenting internal meshes.
def setup_bones(vroid_rig, gen_rig):
    remove_gen_rig_ignored_bones(gen_rig)
    base_bones = gen_rig_base_bones(gen_rig)
    map_bones(create_bone_mapping(base_bones, vroid_rig))
    attach_remaining_bones(vroid_rig, gen_rig)
