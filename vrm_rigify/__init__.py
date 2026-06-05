import re

import bpy

bl_info = {
    "name": "VRM Rigify",
    "author": "Nanoskript",
    "description": "Generates Rigify armatures for VRM models",
    "version": (0, 2, 0),
    "blender": (4, 0, 0),
    "location": "Operator Search > VRM Rigify",
    "doc_url": "https://github.com/nanoskript/vrm-rigify",
    "tracker_url": "https://github.com/nanoskript/vrm-rigify/issues",
    "category": "Rigging",
}


class ModeContext:
    def __init__(self, mode):
        self.mode = mode

    def __enter__(self):
        self.old_mode = bpy.context.object.mode
        bpy.ops.object.mode_set(mode=self.mode)

    def __exit__(self, _type, _value, _trace):
        bpy.ops.object.mode_set(mode=self.old_mode)

    @staticmethod
    def editing(node: bpy.types.Object):
        node.select_set(True)
        return ModeContext("EDIT")

def upgrade_vrm_metarig_hands(metarig):
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = metarig
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = metarig.data.edit_bones
    sides = ['L', 'R']
    fingers = ['f_index', 'f_middle', 'f_ring', 'f_pinky']

    for side in sides:
        hand_name = f'hand.{side}'
        if hand_name not in edit_bones:
            continue
        hand_bone = edit_bones[hand_name]

        for i, finger in enumerate(fingers):
            finger_base_name = f'{finger}.01.{side}'
            palm_name = f'palm.0{i+1}.{side}'

            if finger_base_name not in edit_bones:
                continue

            finger_bone = edit_bones[finger_base_name]

            # FIXED: reposition whether it already exists or not
            palm_bone = edit_bones.get(palm_name) or edit_bones.new(palm_name)
            palm_bone.head = hand_bone.head        # wrist pivot
            palm_bone.tail = finger_bone.head      # finger base
            palm_bone.roll = finger_bone.roll
            palm_bone.parent = hand_bone
            palm_bone.use_connect = False
            finger_bone.parent = palm_bone
            finger_bone.use_connect = False

    bpy.ops.object.mode_set(mode='POSE')

    for side in sides:
        for i, finger in enumerate(fingers):
            palm_name = f'palm.0{i+1}.{side}'
            finger_base_name = f'{finger}.01.{side}'

            if palm_name in metarig.pose.bones:
                pbone = metarig.pose.bones[palm_name]
                pbone.bone.use_deform = True
                pbone.rigify_type = 'limbs.super_palm' if i == 0 else ''

            if finger_base_name in metarig.pose.bones:
                metarig.pose.bones[finger_base_name].rigify_type = 'limbs.super_finger'

        thumb_base = f'thumb.01.{side}'
        if thumb_base in metarig.pose.bones:
            metarig.pose.bones[thumb_base].rigify_type = 'limbs.super_finger'

    bpy.ops.object.mode_set(mode='OBJECT')

def generate_palm_bone_weights(rig_object, vrm_object, bone_mapping):
    """
    Redistributes VRM hand-bone vertex weights to DEF-palm bones.
    Call after rename_rig_bones_to_match_vrm_model_vertex_groups.
    """
    meta_to_vrm = dict(bone_mapping)
    rig_arm = rig_object.data

    skinned_meshes = [
        obj for obj in bpy.data.objects
        if obj.type == 'MESH' and any(
            mod.type == 'ARMATURE' and mod.object in (vrm_object, rig_object)
            for mod in obj.modifiers
        )
    ]

    for obj in skinned_meshes:
        for side in ['L', 'R']:
            hand_vrm_name = meta_to_vrm.get(f'hand.{side}')
            if not hand_vrm_name or hand_vrm_name not in obj.vertex_groups:
                continue

            hand_vg = obj.vertex_groups[hand_vrm_name]

            # Wrist anchor: the renamed DEF-hand bone's head in world space
            wrist_world = None
            if hand_vrm_name in rig_arm.bones:
                b = rig_arm.bones[hand_vrm_name]
                wrist_world = rig_object.matrix_world @ b.head_local

            # Build palm entry list: (vertex_group, tail_world_position)
            palm_entries = []
            for i in range(1, 5):
                palm_name = f'DEF-palm.0{i}.{side}'
                if palm_name not in rig_arm.bones:
                    continue
                bone = rig_arm.bones[palm_name]
                tail_w = rig_object.matrix_world @ bone.tail_local
                vg = (obj.vertex_groups.get(palm_name)
                      or obj.vertex_groups.new(name=palm_name))
                palm_entries.append((vg, tail_w))

            if not palm_entries:
                continue

            # Wrist-exclusion threshold: 15% of average palm-bone length
            if wrist_world:
                avg_len = sum(
                    (tail - wrist_world).length for _, tail in palm_entries
                ) / len(palm_entries)
                wrist_radius = avg_len * 0.15
            else:
                wrist_radius = 0.0

            to_clear = []

            for v in obj.data.vertices:
                try:
                    w = hand_vg.weight(v.index)
                except RuntimeError:
                    continue
                if w < 0.001:
                    continue

                v_world = obj.matrix_world @ v.co

                # Leave true wrist vertices on the hand bone
                if wrist_world and (v_world - wrist_world).length < wrist_radius:
                    continue

                # Nearest palm bone by tail (finger-base) proximity
                nearest_vg = min(palm_entries,
                                 key=lambda e: (v_world - e[1]).length_squared)[0]
                nearest_vg.add([v.index], w, 'REPLACE')
                to_clear.append(v.index)

            if to_clear:
                hand_vg.remove(to_clear)
            
            print(f"[palm weights] {obj.name} {side}: redistributed {len(to_clear)} vertices")

def objects_by_name_patterns(objects, patterns: list[str]):
    object_matches = []
    for node in objects:
        matches = False
        for pattern in patterns:
            matches |= bool(re.match(pattern, node.name))
        if matches:
            object_matches.append(node)
    return object_matches


def full_bone_path(bone: bpy.types.Bone | bpy.types.EditBone) -> str:
    bone_chain = list(reversed(bone.parent_recursive)) + [bone]
    return '/'.join([bone.name for bone in bone_chain])


def generate_template_metarig(metarig_name: str) -> bpy.types.Object:
    try:
        # Generate a humanoid metarig and automatically
        # assign VRM bone types to the metarig.
        bpy.ops.object.armature_human_metarig_add()
        metarig = bpy.context.view_layer.objects.active
        metarig.name = metarig_name
        metarig.data.name = metarig_name
        return metarig
    except AttributeError as e:
        raise Exception("Failed to spawn metarig. Is the Rigify addon enabled?") from e


def compute_metarig_and_vrm_model_bone_mapping(metarig: bpy.types.Object, vrm_object: bpy.types.Object):
    bpy.ops.vrm.assign_vrm1_humanoid_human_bones_automatically(
        armature_object_name=metarig.name
    )

    bpy.ops.vrm.assign_vrm1_humanoid_human_bones_automatically(
        armature_object_name=vrm_object.name
    )

    armature_metarig: bpy.types.Armature = metarig.data
    armature_vrm: bpy.types.Armature = vrm_object.data
    metarig_human_bones = armature_metarig.vrm_addon_extension.vrm1.humanoid.human_bones
    vrm_human_bones = armature_vrm.vrm_addon_extension.vrm1.humanoid.human_bones

    # Compute a bone mapping between the metarig
    # and VRM model based on VRM bone types.
    bone_mapping = []
    for bone_type in metarig_human_bones.keys():
        if bone_type in ["last_bone_names", "initial_automatic_bone_assignment"]:
            continue

        metarig_bone = getattr(metarig_human_bones, bone_type).node
        vrm_bone = getattr(vrm_human_bones, bone_type).node
        if vrm_bone.bone_name:
            bone_mapping.append((metarig_bone.bone_name, vrm_bone.bone_name))
    return bone_mapping


def remove_or_log_unmapped_metarig_bones(metarig: bpy.types.Object, bone_mapping):
    mapped_metarig_bone_names = set([metarig_bone for metarig_bone, vrm_bone in bone_mapping])
    armature_metarig: bpy.types.Armature = metarig.data
    with ModeContext.editing(metarig):
        for metarig_bone in armature_metarig.edit_bones:
            if metarig_bone.name in mapped_metarig_bone_names:
                continue

            # spine.003 (Upper Chest) is an optional VRM bone. Remove it if it
            # cannot be mapped or else Rigify will fail to generate the rig due to
            # a disconnection between spine.003 and spine.004.
            # FIXME: Add heuristics for mapping breast bones.
            if metarig_bone.name not in ["spine.003", "breast.L", "breast.R"]:
                print(f"metarig bone is not mapped '{full_bone_path(metarig_bone)}'")
                continue

            print(f"removing unmapped metarig bone '{full_bone_path(metarig_bone)}'")
            armature_metarig.edit_bones.remove(metarig_bone)


def position_metarig_bones_to_vrm_model(metarig: bpy.types.Object, vrm_object: bpy.types.Object, bone_mapping):
    armature_metarig: bpy.types.Armature = metarig.data
    armature_vrm: bpy.types.Armature = vrm_object.data
    with ModeContext.editing(metarig):
        metarig.matrix_world = vrm_object.matrix_world
        for metarig_bone_name, vrm_bone_name in bone_mapping:
            metarig_bone = armature_metarig.edit_bones[metarig_bone_name]
            vrm_bone = armature_vrm.bones[vrm_bone_name]

            print(f"positioning '{full_bone_path(metarig_bone)}' to '{full_bone_path(vrm_bone)}'")
            metarig_bone.select = True
            metarig_bone.head = vrm_bone.head_local
            metarig_bone.tail = vrm_bone.tail_local


def fix_position_of_metarig_spine_bones(metarig: bpy.types.Object):
    armature_metarig: bpy.types.Armature = metarig.data
    with ModeContext.editing(metarig):
        # If spine.003 and spine.004 are present, ensure that they are connected
        # to each other, otherwise Rigify will fail to generate the rig.
        armature_metarig.edit_bones["spine.004"].use_connect = True
        armature_metarig.edit_bones["spine.004"].use_connect = False

def fix_metarig_limb_rotation_axes(metarig: bpy.types.Object):
    limb_bones = [
        r"^upper_arm\.(L|R)$",
        r"^thigh\.(L|R)$",
    ]

    finger_bones = [
        r"^f_pinky\.01\.(L|R)$",
        r"^f_ring\.01\.(L|R)$",
        r"^f_middle\.01\.(L|R)$",
        r"^f_index\.01\.(L|R)$",
        r"^thumb\.01\.(L|R)$",
    ]

    pose_bones = metarig.pose.bones
    for bone in objects_by_name_patterns(pose_bones, limb_bones):
        print(f"amending bone parameters for limb '{bone.name}'")
        # Ensure local bend direction is correct.
        bone.rigify_parameters.rotation_axis = 'x'

    # Amend armature fingers.
    for bone in objects_by_name_patterns(pose_bones, finger_bones):
        print(f"amending bone parameters for finger '{bone.name}'")
        # Ensure primary bend direction is correct.
        axis = 'Z' if bone.name.endswith('L') else '-Z'
        bone.rigify_parameters.primary_rotation_axis = axis

def invoke_rigify_generate(metarig: bpy.types.Object) -> bpy.types.Object:
    bpy.context.view_layer.objects.active = metarig
    upgrade_vrm_metarig_hands(metarig)
    bpy.ops.pose.rigify_generate()
    return bpy.context.view_layer.objects.active


def removed_generated_rig_facial_bones(rig_object: bpy.types.Object):
    rig_bones_to_delete_by_name_pattern = [
        # Facial expressions and features are managed by shape keys,
        # so we remove all facial bones except for eyes.
        r"^(ORG|DEF)-forehead.*$",
        r"^(ORG|DEF)-temple.*$",
        r"^((ORG|DEF)-)?brow.*$",
        r"^((MCH|ORG|DEF)-)?lid\.(B|T).*$",
        r"^((ORG|DEF)-)?ear\.(L|R).*$",
        r"^((MCH|ORG|DEF)-)?tongue.*$",
        r"^((ORG|DEF)-)?chin.*$",
        r"^((ORG|DEF)-)?cheek\.(B|T).*$",
        r"^(ORG-)?teeth\.(B|T)$",
        r"^((ORG|DEF)-)?nose.*$",
        r"^((ORG|DEF)-)?lip.*$",
        r"^((MCH|ORG|DEF)-)?jaw.*$",
        r"^MCH-mouth_lock$",
    ]

    armature_rig: bpy.types.Armature = rig_object.data
    with ModeContext.editing(rig_object):
        bones_to_remove = []
        for bone_root in objects_by_name_patterns(armature_rig.edit_bones, rig_bones_to_delete_by_name_pattern):
            for bone in bone_root.children_recursive + [bone_root]:
                if bone not in bones_to_remove:
                    bones_to_remove.append(bone)

        for bone in bones_to_remove:
            print(f"deleting facial bone '{full_bone_path(bone)}'")
            armature_rig.edit_bones.remove(bone)


def rename_rig_bones_to_match_vrm_model_vertex_groups(rig_object: bpy.types.Object, bone_mapping):
    armature_rig: bpy.types.Armature = rig_object.data
    with ModeContext.editing(rig_object):
        for metarig_bone_name, vrm_bone_name in bone_mapping:
            if metarig_bone_name in ["eye.L", "eye.R"]:
                rig_bone = armature_rig.edit_bones[f"ORG-{metarig_bone_name}"]
                rig_bone.use_deform = True
            else:
                rig_bone = armature_rig.edit_bones[f"DEF-{metarig_bone_name}"]
                assert rig_bone.use_deform

            print(f"renaming bone '{full_bone_path(rig_bone)}' to '{vrm_bone_name}'")
            rig_bone.name = vrm_bone_name


def attach_unmapped_vrm_model_bones_to_rig(rig_object: bpy.types.Object, vrm_object: bpy.types.Object):
    armature_rig: bpy.types.Armature = rig_object.data
    armature_vrm: bpy.types.Armature = vrm_object.data
    with ModeContext.editing(rig_object):
        # Assume retrieved bones are in traversal order.
        for vrm_bone in armature_vrm.bones:
            bone_already_in_rig = vrm_bone.name in armature_rig.edit_bones
            vrm_bone_has_parent = bool(vrm_bone.parent)
            if bone_already_in_rig or not vrm_bone_has_parent:
                continue

            vrm_bone_parent_name = vrm_bone.parent.name
            parent_exists_in_rig = vrm_bone_parent_name in armature_rig.edit_bones
            if not parent_exists_in_rig:
                continue

            parent_bone_in_rig = armature_rig.edit_bones[vrm_bone_parent_name]
            print(f"generating bone '{full_bone_path(parent_bone_in_rig)}/{vrm_bone.name}'")

            bone_in_rig = armature_rig.edit_bones.new(vrm_bone.name)
            bone_in_rig.head = vrm_bone.head_local
            bone_in_rig.tail = vrm_bone.tail_local
            bone_in_rig.parent = parent_bone_in_rig

            # Inherit the parent bone collections for the generated bone.
            for collection in parent_bone_in_rig.collections:
                collection.assign(bone_in_rig)


# Enables use of the blend shape proxy and expressions panel from the VRM addon.
def copy_shape_key_controls_from_vrm_armature(rig_object: bpy.types.Object, vrm_object: bpy.types.Object):
    armature_rig: bpy.types.Armature = rig_object.data
    armature_vrm: bpy.types.Armature = vrm_object.data
    blend_shape_master = armature_vrm.vrm_addon_extension.vrm0["blend_shape_master"]
    armature_rig.vrm_addon_extension.vrm0["blend_shape_master"] = blend_shape_master
    expressions = armature_vrm.vrm_addon_extension.vrm1["expressions"]
    # Attempt to copy expressions dynamically to bypass VRM 4.0.0 strict typing
    try:
        for key, value in expressions.items():
            try:
                armature_rig.vrm_addon_extension.vrm1.expressions[key] = value
            except Exception:
                pass # Skip any individual property that refuses to copy
    except AttributeError:
        # Fallback if the expressions object has no items() method
        pass


def disable_ik_stretching(rig_object: bpy.types.Object):
    for bone in rig_object.pose.bones:
        stretch_key = "IK_Stretch"
        if stretch_key in bone:
            bone[stretch_key] = 0.0


class GenerateVRMRig(bpy.types.Operator):
    bl_idname = "vrm_rigify.create_rig"
    bl_label = "Generate Rigify armature for VRM model"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        vrm_object = bpy.context.active_object
        assert vrm_object.type == "ARMATURE"

        metarig = generate_template_metarig(f"{vrm_object.name}.metarig")
        bone_mapping = compute_metarig_and_vrm_model_bone_mapping(metarig, vrm_object)

        remove_or_log_unmapped_metarig_bones(metarig, bone_mapping)
        position_metarig_bones_to_vrm_model(metarig, vrm_object, bone_mapping)
        fix_position_of_metarig_spine_bones(metarig)
        fix_metarig_limb_rotation_axes(metarig)
        rig_object = invoke_rigify_generate(metarig)

        removed_generated_rig_facial_bones(rig_object)
        rename_rig_bones_to_match_vrm_model_vertex_groups(rig_object, bone_mapping)
        attach_unmapped_vrm_model_bones_to_rig(rig_object, vrm_object)

        generate_palm_bone_weights(rig_object, vrm_object, bone_mapping)

        copy_shape_key_controls_from_vrm_armature(rig_object, vrm_object)
        disable_ik_stretching(rig_object)

        metarig.hide_set(True)
        vrm_object.hide_set(True)
        return {"FINISHED"}


CLASSES = [
    GenerateVRMRig,
]


def register():
    for clazz in CLASSES:
        bpy.utils.register_class(clazz)


def unregister():
    for clazz in CLASSES:
        bpy.utils.unregister_class(clazz)


if __name__ == "__main__":
    register()
