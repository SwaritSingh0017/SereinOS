#!/bin/bash
# VRM Model Download/Setup Script for SereinOS Avatar
# Downloads a free VRM model for the avatar

set -euo pipefail

log_info() { echo -e "\033[0;34m[INFO]\033[0m $*"; }
log_success() { echo -e "\033[0;32m[SUCCESS]\033[0m $*"; }
log_warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $*"; }

MODELS_DIR="/usr/local/share/serein/models"
AVATAR_MODELS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../serein-avatar/Assets/Models"

create_dirs() {
    sudo mkdir -p "$MODELS_DIR"
    mkdir -p "$AVATAR_MODELS_DIR"
}

# Download a free VRM model from GitHub
download_vrm_model() {
    local model_name="${1:-alicia}"
    local output_path="${2:-$MODELS_DIR/avatar.vrm}"
    
    log_info "Downloading VRM model: $model_name"
    
    case "$model_name" in
        alicia)
            # Alicia Solid - Free VRM model
            local url="https://github.com/vrm-c/UniVRM/releases/download/v0.114.0/AliciaSolid.vrm"
            ;;
        alicia_toon)
            local url="https://github.com/vrm-c/UniVRM/releases/download/v0.114.0/AliciaToon.vrm"
            ;;
        *)
            log_error "Unknown model: $model_name. Options: alicia, alicia_toon"
            return 1
            ;;
    esac
    
    wget -q --show-progress "$url" -O "$output_path" || {
        log_error "Failed to download VRM model"
        return 1
    }
    
    # Copy to avatar project
    cp "$output_path" "$AVATAR_MODELS_DIR/avatar.vrm"
    
    log_success "VRM model downloaded to $output_path"
    log_info "Copied to avatar project: $AVATAR_MODELS_DIR/avatar.vrm"
}

# Create a simple placeholder VRM if download fails
create_placeholder_vrm() {
    local output_path="${1:-$MODELS_DIR/avatar.vrm}"
    
    log_warn "Creating placeholder VRM model..."
    
    # VRM is a GLB (binary glTF) with VRM extension
    # This creates a minimal valid GLB structure
    cat > /tmp/create_placeholder.py << 'PYEOF'
import struct
import json
import base64

# Minimal glTF JSON
gltf_json = {
    "asset": {"version": "2.0", "generator": "SereinOS"},
    "scenes": [{"nodes": [0]}],
    "nodes": [{"mesh": 0}],
    "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "max": [0.5, 0.5, 0.5], "min": [-0.5, -0.5, -0.5]}],
    "bufferViews": [{"buffer": 0, "byteLength": 36, "target": 34962}],
    "buffers": [{"byteLength": 36}],
    "extensionsUsed": ["VRM"],
    "extensions": {
        "VRM": {
            "specVersion": "0.0",
            "meta": {
                "title": "Serein Avatar",
                "version": "0.1.0",
                "author": "SereinOS",
                "allowedUserName": "Everyone"
            }
        }
    }
}

json_bytes = json.dumps(gltf_json, separators=(',', ':')).encode('utf-8')
json_padded = json_bytes + b' ' * ((4 - len(json_bytes) % 4) % 4)

# Binary data: triangle vertices (3 vertices * 3 floats * 4 bytes = 36 bytes)
# Simple triangle
binary_data = struct.pack('<9f', 
    -0.5, -0.5, 0.0,
     0.5, -0.5, 0.0,
     0.0,  0.5, 0.0
)

# GLB header
header = struct.pack('<4sII', b'gltf', 2, 12 + 8 + len(json_padded) + 8 + len(binary_data))
json_chunk = struct.pack('<II', len(json_padded), 0x4E4F534A) + json_padded  # JSON
bin_chunk = struct.pack('<II', len(binary_data), 0x004E4942) + binary_data   # BIN

with open('/tmp/placeholder.vrm', 'wb') as f:
    f.write(header)
    f.write(json_chunk)
    f.write(bin_chunk)

print("Created /tmp/placeholder.vrm")
PYEOF

    python3 /tmp/create_placeholder.py
    sudo cp /tmp/placeholder.vrm "$output_path"
    cp "$output_path" "$AVATAR_MODELS_DIR/avatar.vrm"
    
    log_success "Placeholder VRM created at $output_path"
}

# Main
main() {
    log_info "Setting up VRM model for Serein Avatar..."
    
    create_dirs
    
    # Try to download a real model first
    if download_vrm_model "alicia"; then
        log_success "Using AliciaSolid VRM model"
    elif download_vrm_model "alicia_toon"; then
        log_success "Using AliciaToon VRM model"
    else
        log_warn "Download failed, creating placeholder"
        create_placeholder_vrm
    fi
    
    # Also copy to build directory for ISO
    sudo mkdir -p /usr/local/share/serein/models
    sudo cp "$MODELS_DIR/avatar.vrm" /usr/local/share/serein/models/ || true
    
    log_info "VRM model setup complete"
    log_info "Model location: $MODELS_DIR/avatar.vrm"
}

case "${1:-setup}" in
    setup) main ;;
    download) download_vrm_model "${2:-alicia}" "${3:-$MODELS_DIR/avatar.vrm}" ;;
    placeholder) create_placeholder_vrm "${2:-$MODELS_DIR/avatar.vrm}" ;;
    *) log_error "Usage: $0 [setup|download|placeholder]"; exit 1 ;;
esac