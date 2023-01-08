import re

# Bone name mappings from Rigify to VRoid
# after VRoid simplification.
BASE_VROID_BONE_MAP = [
    ('upper_arm', '^UpperArm_[LR]$'),
    ('forearm', '^LowerArm_[LR]$'),
    ('shoulder', '^Shoulder'),
    ('thigh', '^UpperLeg_[LR]$'),
    ('shin', '^LowerLeg_[LR]$'),
    ('breast', '^Bust1'),  # Ignore: Bust2 (extension of Bust1)
    ('foot', '^Foot'),
    ('toe', '^ToeBase_[LR]$'),  # Ignore: ToeBase_end
    ('hand', '^Hand'),
    ('eye', '^FaceEye_'),
    # Thumb finger.
    ('thumb.01', '^Thumb1'),
    ('thumb.02', '^Thumb2'),
    ('thumb.03', '^Thumb3_[LR]$'),  # Ignore if exists: Thumb3_end
    # Index finger.
    ('index.01', '^Index1'),
    ('index.02', '^Index2'),
    ('index.03', '^Index3_[LR]$'),  # Ignore if exists: Index3_end
    # Middle finger.
    ('middle.01', '^Middle1'),
    ('middle.02', '^Middle2'),
    ('middle.03', '^Middle3_[LR]$'),  # Ignore if exists: Middle3_end
    # Ring finger.
    ('ring.01', '^Ring1'),
    ('ring.02', '^Ring2'),
    ('ring.03', '^Ring3_[LR]$'),  # Ignore if exists: Ring3_end
    # Little finger.
    ('pinky.01', '^Little1'),
    ('pinky.02', '^Little2'),
    ('pinky.03', '^Little3_[LR]$'),  # Ignore if exists: Little3_end
    # Spine.
    ('spine$', '^Hips'),
    ('spine.001', '^Spine'),
    ('spine.002', '^Chest'),
    ('spine.003', '^UpperChest'),
    ('spine.004', '^Neck'),
    ('spine.006', '^Head'),
]

BASE_IGNORED = [
    'spine.005',
    # Unneeded facial features.
    # Expressions managed by shape keys.
    'forehead',
    'temple',
    'brow',
    'lid',
    r'ear\.',
    'tongue',
    'chin',
    'jaw',
    'cheek',
    'nose',
    'lip',
]


def objects_by_patterns(objects, patterns):
    """
    Gets all objects matching any pattern.
    """
    object_matches = []
    for node in objects:
        matches = False
        for pattern in patterns:
            matches |= bool(re.search(pattern, node.name))
        if matches:
            object_matches.append(node)
    return object_matches


def objects_by_pattern(objects, pattern):
    return objects_by_patterns(objects, [pattern])


def create_bone_mapping(base_bones, vroid_rig):
    conversions = []
    for base_bone in base_bones:
        # Extract bone direction.
        direction = None
        if base_bone.name.endswith('L'):
            direction = 'L'
        if base_bone.name.endswith('R'):
            direction = 'R'

        # Find matching VRoid bone pattern.
        vroid_pattern = None
        for base_pattern, pattern in BASE_VROID_BONE_MAP:
            if objects_by_pattern([base_bone], base_pattern):
                assert not vroid_pattern, f"ambiguous pattern: `{base_pattern}`"
                vroid_pattern = pattern
        assert vroid_pattern, f"no VRoid pattern found for `{base_bone}`"

        # Find matching VRoid rig bones.
        vroid_matches = objects_by_pattern(vroid_rig.data.bones, vroid_pattern)
        if direction:
            vroid_matches = objects_by_pattern(vroid_matches, f'_{direction}')

        # Verify single bone.
        assert vroid_matches, f"no VRoid bones found for `{vroid_pattern}`"
        assert len(vroid_matches) == 1, f"too many VRoid bones found for `{vroid_pattern}`"
        [vroid_bone] = vroid_matches

        # Add bone conversion.
        conversions.append((base_bone, vroid_bone))
    return conversions
