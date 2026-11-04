#!/bin/bash
# Unity Avatar Build Script
# Builds the Serein Avatar as a Linux standalone

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."
AVATAR_DIR="${PROJECT_ROOT}/serein-avatar"
BUILD_DIR="${AVATAR_DIR}/Build"
UNITY_VERSION="2022.3.20f1"

log_info() { echo -e "\033[0;34m[INFO]\033[0m $*"; }
log_success() { echo -e "\033[0;32m[SUCCESS]\033[0m $*"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $*"; }

# Check Unity Hub/Editor
find_unity() {
    local unity_paths=(
        "/opt/unity/Editor/Unity"
        "/Applications/Unity/Hub/Editor/${UNITY_VERSION}/Unity.app/Contents/MacOS/Unity"
        "/c/Program Files/Unity/Hub/Editor/${UNITY_VERSION}/Editor/Unity.exe"
        "$(which unity)"
        "$(which unity-editor)"
    )
    
    for path in "${unity_paths[@]}"; do
        if [[ -x "$path" ]]; then
            echo "$path"
            return 0
        fi
    done
    
    return 1
}

UNITY_EXECUTABLE=$(find_unity)
if [[ -z "$UNITY_EXECUTABLE" ]]; then
    log_error "Unity not found. Install Unity ${UNITY_VERSION} via Unity Hub."
    exit 1
fi

log_info "Using Unity: $UNITY_EXECUTABLE"

# Create build directory
mkdir -p "$BUILD_DIR"

# Build command
log_info "Building avatar for Linux..."
"$UNITY_EXECUTABLE" \
    -batchmode \
    -quit \
    -projectPath "$AVATAR_DIR" \
    -buildTarget Linux64 \
    -executeMethod Serein.Avatar.BuildScript.BuildAvatar \
    -buildPath "$BUILD_DIR/serein-avatar" \
    -logFile - \
    -username "$UNITY_LICENSE_USERNAME" \
    -password "$UNITY_LICENSE_PASSWORD" \
    -serial "$UNITY_LICENSE_SERIAL"

if [[ $? -eq 0 ]]; then
    log_success "Avatar built successfully at $BUILD_DIR/serein-avatar"
    
    # Make executable
    chmod +x "$BUILD_DIR/serein-avatar"
    
    # Copy VRM model if exists
    if [[ -f "$AVATAR_DIR/Assets/Models/avatar.vrm" ]]; then
        mkdir -p "$BUILD_DIR/Models"
        cp "$AVATAR_DIR/Assets/Models/avatar.vrm" "$BUILD_DIR/Models/"
        log_info "VRM model copied to build"
    fi
else
    log_error "Build failed"
    exit 1
fi