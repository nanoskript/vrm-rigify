#!/usr/bin/env bash
# Runs the addon's end-to-end tests headlessly. Downloads the latest release
# of the VRM addon and a set of sample models on first run, then generates a
# rig for each model and checks the result. Usage:
#
#   ./run.sh [path-to-model.vrm ...]
#
# If no models are given, the downloaded sample models are tested. Blender is
# located automatically or can be specified with the BLENDER environment
# variable.
set -euo pipefail

# Resolve model paths to absolute paths before changing directory.
MODELS=()
for model in "$@"; do
  case "$model" in
    /*) MODELS+=("$model") ;;
    *) MODELS+=("$PWD/$model") ;;
  esac
done

cd "$(dirname "$0")"

BLENDER="${BLENDER:-$(command -v blender || true)}"
if [ -z "$BLENDER" ] && [ -x "/Applications/Blender.app/Contents/MacOS/Blender" ]; then
  BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
fi

if [ -z "$BLENDER" ]; then
  echo "error: could not find Blender. Set BLENDER=/path/to/blender." >&2
  exit 1
fi

# Blender 4.2 and later install addons through the extensions platform while
# earlier versions only support legacy addons. The VRM addon publishes a
# release asset in each format.
BLENDER_VERSION=$("$BLENDER" --version | head -n 1 | awk '{print $2}')
BLENDER_MAJOR=$(echo "$BLENDER_VERSION" | cut -d. -f1)
BLENDER_MINOR=$(echo "$BLENDER_VERSION" | cut -d. -f2)
if [ "$BLENDER_MAJOR" -gt 4 ] || { [ "$BLENDER_MAJOR" -eq 4 ] && [ "$BLENDER_MINOR" -ge 2 ]; }; then
  VRM_ADDON_FORMAT=Extension
else
  VRM_ADDON_FORMAT=Legacy
fi

# Isolate Blender's configuration so tests are reproducible and do not touch
# the user's own preferences and addons. Legacy installs are kept in a
# separate directory so a legacy copy of the VRM addon can never shadow the
# extension install when testing Blender 4.2+.
if [ "$VRM_ADDON_FORMAT" = Extension ]; then
  export BLENDER_USER_RESOURCES="$PWD/.blender"
else
  export BLENDER_USER_RESOURCES="$PWD/.blender-legacy"
fi
mkdir -p "$BLENDER_USER_RESOURCES"

ASSETS="$PWD/assets"
mkdir -p "$ASSETS"

download() {
  if [ ! -f "$2" ]; then
    echo "downloading $1"
    # Download to a temporary path first so an interrupted
    # transfer does not leave behind a truncated file.
    curl --fail --location --silent --show-error --output "$2.tmp" "$1"
    mv "$2.tmp" "$2"
  fi
}

# Fetch the latest release of the VRM addon so tests always run against the
# newest version of the addon. Authenticate when a token is available (CI)
# because anonymous GitHub API requests are heavily rate limited.
RELEASES_URL="https://api.github.com/repos/saturday06/VRM-Addon-for-Blender/releases/latest"
if [ -n "${GITHUB_TOKEN:-}" ]; then
  RELEASE=$(curl --fail --location --silent --show-error \
    --header "Authorization: Bearer $GITHUB_TOKEN" "$RELEASES_URL")
else
  RELEASE=$(curl --fail --location --silent --show-error "$RELEASES_URL")
fi

VRM_ADDON_URL=$(printf '%s' "$RELEASE" | python3 -c "import json, sys
release = json.load(sys.stdin)
want_extension = sys.argv[1] == 'Extension'
[url] = [asset['browser_download_url'] for asset in release['assets']
         if ('Extension' in asset['name']) == want_extension]
print(url)" "$VRM_ADDON_FORMAT")
VRM_ADDON_ZIP="$ASSETS/$(basename "$VRM_ADDON_URL")"
download "$VRM_ADDON_URL" "$VRM_ADDON_ZIP"

# Sample models in both the VRM 0.x format (VRoid
# Studio's sample avatars) and the VRM 1.0 format.
VROID_STABLE_MODELS=(AvatarSample_A AvatarSample_B AvatarSample_C)
VROID_BETA_MODELS=(
  Darkness_Shibu HairSample_Female HairSample_Male Sakurada_Fumiriya
  Sendagaya_Shibu Sendagaya_Shino Victoria_Rubin Vita Vivi
)

for name in "${VROID_STABLE_MODELS[@]}"; do
  download "https://github.com/madjin/vrm-samples/raw/master/vroid/stable/$name.vrm" \
    "$ASSETS/$name.vrm"
done

for name in "${VROID_BETA_MODELS[@]}"; do
  download "https://github.com/madjin/vrm-samples/raw/master/vroid/beta/$name.vrm" \
    "$ASSETS/$name.vrm"
done

# Note: the VRM specification's Seed-san sample is deliberately not tested.
# Its bones follow Blender's naming conventions (`f_index.01.L`, `chest`)
# which collide with the names of the control bones that Rigify generates,
# so its meshes cannot follow the generated rig. This addon only supports
# models with VRoid-style bone names (`J_Bip_C_Chest`).
download "https://github.com/vrm-c/vrm-specification/raw/master/samples/VRM1_Constraint_Twist_Sample/vrm/VRM1_Constraint_Twist_Sample.vrm" \
  "$ASSETS/VRM1_Constraint_Twist_Sample.vrm"

# Install the VRM addon into the isolated Blender configuration.
VRM_ADDON_MARKER="$BLENDER_USER_RESOURCES/.installed-$(basename "$VRM_ADDON_ZIP")"
if [ ! -f "$VRM_ADDON_MARKER" ]; then
  if [ "$VRM_ADDON_FORMAT" = Extension ]; then
    "$BLENDER" --command extension install-file --repo user_default --enable "$VRM_ADDON_ZIP"
  else
    # Remove other versions of the addon first so the test
    # harness can never enable a stale install.
    rm -rf "$BLENDER_USER_RESOURCES/scripts/addons/VRM_Addon_for_Blender"*
    "$BLENDER" --background --python-exit-code 1 --python-expr \
      "import bpy; bpy.ops.preferences.addon_install(filepath='$VRM_ADDON_ZIP', overwrite=True)"
  fi
  touch "$VRM_ADDON_MARKER"
fi

if [ "${#MODELS[@]}" -eq 0 ]; then
  MODELS=("$ASSETS"/*.vrm)
fi

FAILURES=()
for model in "${MODELS[@]}"; do
  echo "==== testing model: $model"
  VRM_TEST_MODEL_PATH="$model" \
  BLENDER_VRM_AUTOMATIC_LICENSE_CONFIRMATION=true \
    "$BLENDER" --background --python-exit-code 1 --python "./test_harness.py" ||
    FAILURES+=("$model")
done

if [ "${#FAILURES[@]}" -gt 0 ]; then
  echo "==== failed models:"
  printf '%s\n' "${FAILURES[@]}"
  exit 1
fi

echo "==== all tests passed"
