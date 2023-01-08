from .debug import LOGGER

TWEAK_LAYERS = [
    4,  # Torso
    9,  # Arm.L
    12,  # Arm.R
    15,  # Leg.L
    18,  # Leg.R
]


def humanize(rig):
    # Disable tweak layers.
    for index in TWEAK_LAYERS:
        rig.data.layers[index] = False

    # Disable all stretching.
    for bone in rig.pose.bones:
        stretch_key = "IK_Stretch"
        if stretch_key in bone:
            LOGGER.info(f"disabling stretching for '{bone.name}'")
            bone[stretch_key] = 0.0
