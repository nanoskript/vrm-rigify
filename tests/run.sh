VRM_TEST_MODEL_PATH="$1" \
BLENDER_VRM_AUTOMATIC_LICENSE_CONFIRMATION=true \
  blender --python "../vrm_rigify/__init__.py" --python "./test_harness.py"
