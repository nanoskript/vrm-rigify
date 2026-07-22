import os
import re
import sys
from math import degrees

import addon_utils
import bpy
from mathutils import Vector

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


def check_finger_controls_locked(rig_object: bpy.types.Object):
    # Translating a finger control stretches the finger's deform chain, which
    # always looks broken on VRM models, so every finger control must have
    # its location locked. Locks only affect viewport transforms so this
    # checks the flags directly.
    finger_controls = [
        bone for bone in rig_object.pose.bones
        if re.match(r"^(f_index|f_middle|f_ring|f_pinky|thumb)\.", bone.name)
    ]

    assert finger_controls, "no finger control bones found in the generated rig"
    for bone in finger_controls:
        assert all(bone.lock_location), \
            f"finger control '{bone.name}' can be translated"


def check_finger_tip_controls_hidden(rig_object: bpy.types.Object):
    # The fingertip controls are inert once their translation is locked
    # (their only consumer is a stretch-to constraint reading their
    # location), so the addon hides them.
    tip_controls = [
        bone for bone in rig_object.pose.bones
        if re.match(r"^(f_index|f_middle|f_ring|f_pinky|thumb)\.\d+\.(L|R)\.001$", bone.name)
    ]

    assert tip_controls, "no fingertip control bones found in the generated rig"
    for bone in tip_controls:
        # Blender 5.0 moved pose mode visibility to PoseBone.hide while
        # earlier versions hide bones in pose mode through Bone.hide.
        hidden = bone.hide if hasattr(bone, "hide") else bone.bone.hide
        assert hidden, f"inert fingertip control '{bone.name}' is visible"


def weighted_vertex_bounds(vrm_object: bpy.types.Object, group_name: str):
    # Bounding box of the vertices that predominantly follow the given bone.
    points = []
    for mesh in vrm_object.children_recursive:
        if mesh.type != "MESH":
            continue

        group = mesh.vertex_groups.get(group_name)
        if group is None:
            continue

        for vertex in mesh.data.vertices:
            for entry in vertex.groups:
                if entry.group == group.index and entry.weight > 0.3:
                    points.append(mesh.matrix_world @ vertex.co)

    assert points, f"no vertices are weighted to bone '{group_name}'"
    minimum = Vector(map(min, zip(*points)))
    maximum = Vector(map(max, zip(*points)))
    return minimum, maximum


def check_control_widget_sizes(rig_object: bpy.types.Object, vrm_object: bpy.types.Object):
    # The head and IK hand widgets must be scaled to the model's proportions
    # or they render inside the meshes and cannot be selected in the
    # viewport. A widget is drawn at its bone's length multiplied by the
    # widget's display scale.
    human_bones = vrm_object.data.vrm_addon_extension.vrm1.humanoid.human_bones

    # The head circle's diameter is the head bone's length. It must clear
    # the head, measured as the widest horizontal extent of the vertices
    # weighted to the head bone.
    head_bone_name = human_bones.head.node.bone_name
    minimum, maximum = weighted_vertex_bounds(vrm_object, head_bone_name)
    head_extent = max(maximum.x - minimum.x, maximum.y - minimum.y)
    head_control = rig_object.pose.bones["head"]
    diameter = abs(head_control.custom_shape_scale_xyz[0]) * head_control.bone.length
    assert diameter >= head_extent, \
        f"head widget diameter {diameter:.3f} is inside the head ({head_extent:.3f} wide)"

    # The circle is drawn at the widget's Y scale in bone lengths along the
    # bone, so it must sit near the crown: above the top of the head, but
    # not floating far over it.
    crown = maximum.z - (rig_object.matrix_world @ head_control.bone.head_local).z
    height = abs(head_control.custom_shape_scale_xyz[1]) * head_control.bone.length
    assert crown <= height <= 1.25 * crown, \
        f"head widget sits {height:.3f} along the bone, crown is at {crown:.3f}"

    # The hand widget spans its bone's length so it must cover
    # most of the palm to wrap around the hand.
    hand_bones_by_side = {
        "L": (human_bones.left_hand, human_bones.left_middle_proximal),
        "R": (human_bones.right_hand, human_bones.right_middle_proximal),
    }

    for side, (hand, middle) in hand_bones_by_side.items():
        if not (hand.node.bone_name and middle.node.bone_name):
            continue

        hand_bone = vrm_object.data.bones[hand.node.bone_name]
        middle_bone = vrm_object.data.bones[middle.node.bone_name]
        palm_length = (middle_bone.head_local - hand_bone.head_local).length
        control = rig_object.pose.bones[f"hand_ik.{side}"]
        length = abs(control.custom_shape_scale_xyz[1]) * control.bone.length
        assert length >= 0.9 * palm_length, \
            f"hand widget '{control.name}' spans {length:.3f} of the {palm_length:.3f} palm"


def check_eye_bones_at_rest(rig_object: bpy.types.Object, vrm_object: bpy.types.Object):
    # A constraint aims each eye bone at a target control. If the target
    # does not sit on the eye bone's axis, the constraint rotates the eyes
    # out of their sockets while the rig is still at rest, so the model's
    # eyes twist as soon as its meshes follow the rig.
    bpy.context.view_layer.update()
    human_bones = vrm_object.data.vrm_addon_extension.vrm1.humanoid.human_bones
    for key in ["left_eye", "right_eye"]:
        bone_name = getattr(human_bones, key).node.bone_name
        if not (bone_name and bone_name in rig_object.pose.bones):
            continue

        bone = rig_object.pose.bones[bone_name]
        delta = (bone.matrix @ bone.bone.matrix_local.inverted()).to_quaternion()
        angle = min(degrees(delta.angle), 360.0 - degrees(delta.angle))
        assert angle < 1.0, \
            f"eye bone '{bone_name}' is rotated {angle:.1f} degrees at rest"


def check_bone_orientation_matches_model(rig_object: bpy.types.Object, vrm_object: bpy.types.Object):
    # Deform bones must keep the model's bone roll or their local axes come out
    # twisted relative to the model. That twist is what makes finger bend axes
    # point out of the curl plane and secondary chains (hair, skirts) look wrong,
    # most visibly on VRM 1.0 imports where bones carry non-zero rolls. Two sets
    # are exempt: the arm and leg chains, which keep the metarig's roll because
    # Rigify's limb rigs bend around it, and the eye bones, which are pointed
    # forward for the eye rig. Roll is only exposed on edit bones.
    human_bones = vrm_object.data.vrm_addon_extension.vrm1.humanoid.human_bones
    exempt_keys = [
        "left_eye", "right_eye",
        "left_upper_arm", "left_lower_arm", "left_hand",
        "right_upper_arm", "right_lower_arm", "right_hand",
        "left_upper_leg", "left_lower_leg", "left_foot", "left_toes",
        "right_upper_leg", "right_lower_leg", "right_foot", "right_toes",
    ]
    exempt = set()
    for key in exempt_keys:
        human_bone = getattr(human_bones, key, None)
        if human_bone and human_bone.node.bone_name:
            exempt.add(human_bone.node.bone_name)

    def edit_bone_rolls(obj: bpy.types.Object) -> dict[str, float]:
        previous = bpy.context.view_layer.objects.active
        obj.hide_set(False)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        rolls = {edit_bone.name: edit_bone.roll for edit_bone in obj.data.edit_bones}
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = previous
        return rolls

    vrm_rolls = edit_bone_rolls(vrm_object)
    rig_rolls = edit_bone_rolls(rig_object)
    twisted = []
    for name, rig_roll in rig_rolls.items():
        if name in exempt or name not in vrm_rolls:
            continue
        if not rig_object.data.bones[name].use_deform:
            continue

        diff = abs(degrees(rig_roll - vrm_rolls[name])) % 360.0
        diff = min(diff, 360.0 - diff)
        if diff > 1.0:
            twisted.append(f"{name} ({diff:.0f} deg)")

    assert not twisted, \
        f"deform bones are twisted relative to the model: {sorted(twisted)}"


def check_finger_curl_flexes_toward_palm(rig_object: bpy.types.Object, vrm_object: bpy.types.Object):
    # Scaling a finger master control curls the finger around its bend axis
    # (primary_rotation_axis). The curl must flex each finger toward the palm,
    # not splay it sideways or bend it backward, which happens when the bend
    # axis does not match the model's bone roll. Every finger, including the
    # thumb, is checked on both hands.
    human_bones = vrm_object.data.vrm_addon_extension.vrm1.humanoid.human_bones

    def bone_name(key: str) -> str:
        human_bone = getattr(human_bones, key, None)
        return human_bone.node.bone_name if human_bone else ""

    def head_world(name: str):
        return rig_object.matrix_world @ rig_object.pose.bones[name].bone.head_local

    def curl_toward_palm(master: str, proximal: str, distal: str, back_normal) -> float:
        # Runs in pose mode with a clean pose. Curls one finger by scaling its
        # master control, then leaves the pose clean again for the next finger.
        tip = rig_object.pose.bones[distal]
        finger_axis = ((rig_object.matrix_world @ tip.tail)
                       - (rig_object.matrix_world @ rig_object.pose.bones[proximal].head)).normalized()
        rest_tip = rig_object.matrix_world @ tip.tail
        master_bone = rig_object.pose.bones[master]
        master_bone.scale.y = 0.82  # a gentle curl
        bpy.context.view_layer.update()
        displacement = (rig_object.matrix_world @ tip.tail) - rest_tip
        master_bone.matrix_basis.identity()
        bpy.context.view_layer.update()

        # Ignore the component along the finger (it folds back as it curls) and
        # check the remaining motion heads to the palm side, not the back.
        across = displacement - displacement.dot(finger_axis) * finger_axis
        return -across.dot(back_normal)

    # (control master base, VRM humanoid finger name). The thumb's control base
    # lacks the 'f_' prefix and the pinky's VRM name is "little".
    fingers = [
        ("index", "f_index.01", "index"),
        ("middle", "f_middle.01", "middle"),
        ("ring", "f_ring.01", "ring"),
        ("pinky", "f_pinky.01", "little"),
        ("thumb", "thumb.01", "thumb"),
    ]

    # The "back of the hand" side is derived from the body's own up axis
    # (hips to head) rather than world up, so this check does not share the
    # addon's palm-down assumption and can catch a curl axis that the addon
    # signed the wrong way.
    hips, head = bone_name("hips"), bone_name("head")
    body_up = Vector((0.0, 0.0, 1.0))
    if hips in rig_object.pose.bones and head in rig_object.pose.bones:
        spine = head_world(head) - head_world(hips)
        if spine.length > 0.0:
            body_up = spine.normalized()

    tested = 0
    bpy.context.view_layer.objects.active = rig_object
    bpy.ops.object.mode_set(mode="POSE")
    try:
        for bone in rig_object.pose.bones:
            bone.matrix_basis.identity()
        bpy.context.view_layer.update()

        for side_word, side in [("left", "L"), ("right", "R")]:
            proximal = bone_name(f"{side_word}_index_proximal")
            middle = bone_name(f"{side_word}_middle_proximal")
            little = bone_name(f"{side_word}_little_proximal")
            if not all(n and n in rig_object.pose.bones for n in [proximal, middle, little]):
                continue

            # The three knuckles define the palm plane; sign its normal to point
            # to the back of the hand.
            back_normal = (head_world(middle) - head_world(proximal)).cross(
                head_world(little) - head_world(proximal)).normalized()
            if back_normal.dot(body_up) < 0.0:
                back_normal = -back_normal

            for label, master_base, vrm_name in fingers:
                master = f"{master_base}_master.{side}"
                finger_proximal = bone_name(f"{side_word}_{vrm_name}_proximal")
                finger_distal = bone_name(f"{side_word}_{vrm_name}_distal")
                names = [finger_proximal, finger_distal]
                if master not in rig_object.pose.bones or not all(n and n in rig_object.pose.bones for n in names):
                    continue

                toward_palm = curl_toward_palm(master, finger_proximal, finger_distal, back_normal)
                assert toward_palm > 0.005, \
                    f"{side_word} {label} finger curls away from the palm (toward_palm={toward_palm:.4f})"
                tested += 1
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    assert tested > 0, "no finger master controls found to test finger curl"


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
    check_finger_controls_locked(rig_object)
    check_finger_tip_controls_hidden(rig_object)
    check_control_widget_sizes(rig_object, vrm_object)
    check_eye_bones_at_rest(rig_object, vrm_object)
    check_shape_key_controls(rig_object, vrm_object)
    check_bone_orientation_matches_model(rig_object, vrm_object)
    check_finger_curl_flexes_toward_palm(rig_object, vrm_object)

    print(f"model '{os.path.basename(model_path)}' passed all checks")


main()
