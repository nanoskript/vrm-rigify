import re

import bpy
from mathutils import Vector

bl_info = {
    "name": "VRM Rigify",
    "author": "Nanoskript",
    "description": "Generates Rigify armatures for VRM models",
    "version": (0, 3, 0),
    "blender": (3, 6, 0),
    "location": "Operator Search > VRM Rigify",
    "doc_url": "https://github.com/nanoskript/vrm-rigify",
    "tracker_url": "https://github.com/nanoskript/vrm-rigify/issues",
    "category": "Rigging",
}

# Version compatibility polyfills. Each polyfill is defined once at import
# time based on the running Blender version so that version differences are
# kept out of the rig generation code.

if bpy.app.version >= (4, 0, 0):
    # Blender 4.0 replaced bone layers with bone collections.
    def inherit_bone_groupings(bone: bpy.types.EditBone, parent: bpy.types.EditBone):
        for collection in parent.collections:
            collection.assign(bone)
else:
    def inherit_bone_groupings(bone: bpy.types.EditBone, parent: bpy.types.EditBone):
        bone.layers = list(parent.layers)


if bpy.app.version >= (5, 0, 0):
    # Blender 5.0 moved pose mode visibility to PoseBone.hide and
    # changed Bone.hide to only affect edit mode. Earlier versions
    # hide bones in pose mode through Bone.hide alone.
    def hide_pose_bone(bone: bpy.types.PoseBone):
        bone.hide = True
else:
    def hide_pose_bone(bone: bpy.types.PoseBone):
        bone.bone.hide = True


def assign_id_property(container, key: str, value):
    # Blender 5.0 disallows assigning over an existing
    # group property so remove any existing property first.
    if key in container:
        del container[key]
    container[key] = value


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


def assign_vrm1_human_bones_automatically(node: bpy.types.Object):
    try:
        bpy.ops.vrm.assign_vrm1_humanoid_human_bones_automatically(
            armature_object_name=node.name
        )
    except TypeError:
        # Versions of the VRM addon before 4.0 only
        # accept the `armature_name` argument alias.
        bpy.ops.vrm.assign_vrm1_humanoid_human_bones_automatically(
            armature_name=node.name
        )


def compute_metarig_and_vrm_model_bone_mapping(metarig: bpy.types.Object, vrm_object: bpy.types.Object):
    assign_vrm1_human_bones_automatically(metarig)
    assign_vrm1_human_bones_automatically(vrm_object)

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

        # Newer versions of the VRM addon include entries in `human_bones`
        # that are not bone references, so skip anything without a `node`.
        metarig_bone = getattr(metarig_human_bones, bone_type, None)
        vrm_bone = getattr(vrm_human_bones, bone_type, None)
        if not (hasattr(metarig_bone, "node") and hasattr(vrm_bone, "node")):
            continue

        if vrm_bone.node.bone_name:
            bone_mapping.append((metarig_bone.node.bone_name, vrm_bone.node.bone_name))
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
            # spine.005 (upper neck) can never be mapped because VRM models only
            # have a single neck bone. If it is kept, it collapses to zero length
            # and breaks the neck chain, so Rigify will not generate a deform
            # bone for the head.
            # FIXME: Add heuristics for mapping breast bones.
            if metarig_bone.name not in ["spine.003", "spine.005", "breast.L", "breast.R"]:
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


def fix_position_of_metarig_spine_bones(metarig: bpy.types.Object, bone_mapping):
    mapped_metarig_bone_names = set([metarig_bone for metarig_bone, vrm_bone in bone_mapping])
    armature_metarig: bpy.types.Armature = metarig.data
    with ModeContext.editing(metarig):
        # If spine.003 and spine.004 are present, ensure that they are connected
        # to each other, otherwise Rigify will fail to generate the rig.
        armature_metarig.edit_bones["spine.004"].use_connect = True
        armature_metarig.edit_bones["spine.004"].use_connect = False

        # Reconnect the head bone to the end of the neck chain (spine.005 has
        # been removed), otherwise Rigify will exclude the head bone from the
        # neck rig and will not generate a deform bone for it. Only reconnect
        # if the neck bone has been mapped: connecting to an unpositioned neck
        # bone would move the head bone away from the model's head position.
        if "spine.004" in mapped_metarig_bone_names:
            armature_metarig.edit_bones["spine.006"].use_connect = True


def fix_orientation_of_metarig_eye_bones(metarig: bpy.types.Object):
    # Rigify's eye rig assumes the eye bones point straight out of the head,
    # from the eyeball's center through the pupil: it aims each eye at a
    # target control placed in front of the face along the average of both
    # eyes' axes. VRM 1.0 models import with their original eye bone
    # orientations, which VRoid tilts outwards, so the aim constraint
    # rotates the eyes out of their sockets while the rig is still at rest.
    # Point the metarig's eye bones straight forward: only the eyeball's
    # center position affects skinning.
    armature_metarig: bpy.types.Armature = metarig.data
    with ModeContext.editing(metarig):
        for bone_name in ["eye.L", "eye.R"]:
            bone = armature_metarig.edit_bones.get(bone_name)
            if bone is None:
                continue

            print(f"pointing metarig eye bone '{bone.name}' forward")
            bone.tail = bone.head + Vector((0.0, -bone.length, 0.0))
            bone.roll = 0.0


def remove_metarig_palm_bones(metarig: bpy.types.Object):
    # There isn't a bone mapping for the palm bones so let's remove them.
    armature_metarig: bpy.types.Armature = metarig.data
    with ModeContext.editing(metarig):
        edit_bones = armature_metarig.edit_bones
        for bone in objects_by_name_patterns(edit_bones, [r"^palm.*$"]):
            print(f"deleting metarig palm bone '{bone.name}'")
            edit_bones.remove(bone)


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
                rig_bone_name = f"ORG-{metarig_bone_name}"
            else:
                rig_bone_name = f"DEF-{metarig_bone_name}"

            # Rigify may not generate a bone for every mapped metarig bone
            # depending on the model and the Rigify version.
            if rig_bone_name not in armature_rig.edit_bones:
                print(f"rig bone '{rig_bone_name}' not found so skipping rename")
                continue

            rig_bone = armature_rig.edit_bones[rig_bone_name]
            rig_bone.use_deform = True
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

            # Show the generated bone alongside its parent.
            inherit_bone_groupings(bone_in_rig, parent_bone_in_rig)


# Enables use of the blend shape proxy and expressions panel from the VRM addon.
def copy_shape_key_controls_from_vrm_armature(rig_object: bpy.types.Object, vrm_object: bpy.types.Object):
    armature_rig: bpy.types.Armature = rig_object.data
    armature_vrm: bpy.types.Armature = vrm_object.data
    blend_shape_master = armature_vrm.vrm_addon_extension.vrm0["blend_shape_master"]
    assign_id_property(armature_rig.vrm_addon_extension.vrm0, "blend_shape_master", blend_shape_master)
    expressions = armature_vrm.vrm_addon_extension.vrm1["expressions"]
    assign_id_property(armature_rig.vrm_addon_extension.vrm1, "expressions", expressions)


def lock_finger_control_translation(rig_object: bpy.types.Object):
    # Rigify leaves the finger FK, tip, and master controls free to
    # translate, which stretches the finger deform chain. VRM models have
    # rigid vertex weights so stretched fingers always deform badly. Curling
    # fingers by scaling the master control is unaffected by these locks.
    finger_control_bones = [
        r"^(f_index|f_middle|f_ring|f_pinky|thumb)\.\d+\.(L|R)(\.001)?$",
        r"^(f_index|f_middle|f_ring|f_pinky|thumb)\.\d+_master\.(L|R)$",
    ]

    for bone in objects_by_name_patterns(rig_object.pose.bones, finger_control_bones):
        print(f"locking translation of finger control '{bone.name}'")
        bone.lock_location = (True, True, True)


def hide_finger_tip_controls(rig_object: bpy.types.Object):
    # With translation locked, the fingertip controls have no function left:
    # the only consumer of each one is a stretch-to constraint on the last
    # finger segment, which reads nothing but its location. Hide them so the
    # rig only shows controls that do something.
    tip_control_bones = [
        r"^(f_index|f_middle|f_ring|f_pinky|thumb)\.\d+\.(L|R)\.001$",
    ]

    for bone in objects_by_name_patterns(rig_object.pose.bones, tip_control_bones):
        print(f"hiding inert finger tip control '{bone.name}'")
        hide_pose_bone(bone)


def scale_control_widget(bone: bpy.types.PoseBone, factor: float):
    # Widget display transforms are purely visual so
    # scaling them does not affect the rig's behavior.
    bone.custom_shape_scale_xyz = [component * factor for component in bone.custom_shape_scale_xyz]


def head_crown_offset(vrm_object: bpy.types.Object, head_bone_name: str) -> float:
    # Highest extent of the vertices weighted to the head bone,
    # measured along the head bone's axis from the bone's head.
    head_bone = vrm_object.data.bones[head_bone_name]
    origin = vrm_object.matrix_world @ head_bone.head_local
    axis = vrm_object.matrix_world.to_3x3() @ (head_bone.tail_local - head_bone.head_local)
    axis.normalize()

    offset = 0.0
    for mesh in vrm_object.children_recursive:
        if mesh.type != "MESH":
            continue

        group = mesh.vertex_groups.get(head_bone_name)
        if group is None:
            continue

        for vertex in mesh.data.vertices:
            for entry in vertex.groups:
                if entry.group == group.index and entry.weight > 0.3:
                    point = mesh.matrix_world @ vertex.co
                    offset = max(offset, (point - origin).dot(axis))
    return offset


def fit_head_control_widget_to_crown(
    rig_object: bpy.types.Object, vrm_object: bpy.types.Object, bone_mapping
):
    # Rigify draws the head control circle at the tail of the head bone
    # with a diameter of the bone's length, assuming the bone spans the
    # whole skull like the default metarig's. VRM head bones end around
    # ear level so the circle ends up buried inside the head mesh. Widen
    # the circle to clear the head: VRM head bones are roughly a third of
    # the head's size. The widget's Y scale sets how far along the bone the
    # circle is drawn, so place it just above the crown: the highest vertex
    # that follows the head bone. Head bone lengths vary a lot between
    # models so a fixed height either buries the circle or floats it.
    bone = rig_object.pose.bones["head"]
    head_bone_name = dict(bone_mapping).get("spine.006")
    crown = head_crown_offset(vrm_object, head_bone_name) if head_bone_name else 0.0
    height = max(1.05 * crown / bone.bone.length, 1.0) if crown else 4.0
    print(f"fitting control widget '{bone.name}' to height {height:.2f}")
    bone.custom_shape_scale_xyz = [
        component * factor
        for component, factor in zip(bone.custom_shape_scale_xyz, [4.0, height, 4.0])
    ]


def fit_hand_control_widgets_to_palms(
    rig_object: bpy.types.Object, vrm_object: bpy.types.Object, bone_mapping
):
    # Rigify draws the IK hand widget scaled by the hand bone's length,
    # assuming the bone spans the palm from the wrist to the knuckles.
    # VRM 0.x models import with a short stub of a hand bone so the
    # widget collapses into the palm. Rescale the widget to span the
    # model's actual palm: from the wrist to the middle finger's knuckle.
    mapping = dict(bone_mapping)
    armature_vrm: bpy.types.Armature = vrm_object.data
    for side in ["L", "R"]:
        hand_bone_name = mapping.get(f"hand.{side}")
        middle_bone_name = mapping.get(f"f_middle.01.{side}")
        if not (hand_bone_name and middle_bone_name):
            print(f"no palm mapping for side '{side}' so skipping hand widget")
            continue

        hand_bone = armature_vrm.bones[hand_bone_name]
        middle_bone = armature_vrm.bones[middle_bone_name]
        palm_length = (middle_bone.head_local - hand_bone.head_local).length

        bone = rig_object.pose.bones[f"hand_ik.{side}"]
        factor = palm_length / hand_bone.length
        print(f"scaling control widget '{bone.name}' by {factor:.2f}")
        scale_control_widget(bone, factor)


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
        remove_metarig_palm_bones(metarig)
        remove_or_log_unmapped_metarig_bones(metarig, bone_mapping)
        position_metarig_bones_to_vrm_model(metarig, vrm_object, bone_mapping)
        fix_position_of_metarig_spine_bones(metarig, bone_mapping)
        fix_orientation_of_metarig_eye_bones(metarig)
        fix_metarig_limb_rotation_axes(metarig)
        rig_object = invoke_rigify_generate(metarig)

        removed_generated_rig_facial_bones(rig_object)
        rename_rig_bones_to_match_vrm_model_vertex_groups(rig_object, bone_mapping)
        attach_unmapped_vrm_model_bones_to_rig(rig_object, vrm_object)
        copy_shape_key_controls_from_vrm_armature(rig_object, vrm_object)
        lock_finger_control_translation(rig_object)
        hide_finger_tip_controls(rig_object)
        fit_head_control_widget_to_crown(rig_object, vrm_object, bone_mapping)
        fit_hand_control_widgets_to_palms(rig_object, vrm_object, bone_mapping)
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
