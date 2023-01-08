import logging

LOGGER = logging.getLogger("vrm_rigify")
LOGGER.setLevel(logging.DEBUG)
if not LOGGER.hasHandlers():
    LOGGER.addHandler(logging.StreamHandler())


def vroid_bone_types(vroid_rig):
    bones = []
    for bone in vroid_rig.data.bones:
        tokens = bone.name.split('_')
        if tokens[0] == 'J':
            # 1 is category, 2 is side, 3 is type.
            bones.append(f"J_{tokens[1]}_{tokens[3]}")
        elif bone.name.startswith('HairJoint'):
            bones.append("HairJoint")
        else:
            bones.append(bone.name)
    return sorted(set(bones))


def gen_bone_types(gen_rig):
    bones = []
    for bone in gen_rig.data.bones:
        if bone.name.startswith('DEF'):
            tokens = bone.name.split('.')
            if tokens[-1] in ['L', 'R']:
                tokens = tokens[:-1]
            bones.append('.'.join(tokens))
    return sorted(set(bones))
