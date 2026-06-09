#!/bin/env bash
# Based on https://gist.github.com/supechicken/e6bb13e2db86a74e831f907805aed078

set -e

# 1. Detect Kernel Version from uname
KERNEL_VERSION=$(uname -r | cut -d. -f1,2)
BRANCH="chromeos-${KERNEL_VERSION}"

echo "Detected Kernel Version: $KERNEL_VERSION"
echo "Targeting Branch: $BRANCH"

BUILD_DIR="$(pwd)/kernel_local"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

git clone https://chromium.googlesource.com/chromiumos/third_party/kernel \
    cros-kernel -b "$BRANCH" --depth=1 || {
        echo "Error: Branch $BRANCH not found."
        exit 1
    }

cd cros-kernel

# Initial Config
CHROMEOS_KERNEL_FAMILY=termina ./chromeos/scripts/prepareconfig container-vm-x86_64

# --- AUTOMATION START ---
echo "Applying Modern BinderFS configurations..."
./scripts/config --enable CONFIG_ANDROID
./scripts/config --enable CONFIG_ASHMEM
./scripts/config --enable CONFIG_ANDROID_BINDER_IPC
./scripts/config --enable CONFIG_ANDROID_BINDERFS
./scripts/config --set-str CONFIG_ANDROID_BINDER_DEVICES ""

# Refresh config
make LLVM=1 LLVM_IAS=1 olddefconfig

echo "--- Verifying Config ---"
grep -E "CONFIG_ANDROID_BINDER|CONFIG_ASHMEM" .config
echo "------------------------"
# --- AUTOMATION END ---

# Build
make LLVM=1 LLVM_IAS=1 bzImage -j$(nproc)

# Copy out
mkdir -p /mnt/chromeos/MyFiles/Kernels
cp arch/x86/boot/bzImage /mnt/chromeos/MyFiles/Kernels/bzImage

echo "----------------------------------------------------------"
echo "Kernel moved to MyFiles/Kernels/bzImage"
echo "Run this in crosh (Ctrl+Alt+T):"
echo "vmc stop termina"
echo "vmc start termina --enable-gpu --kernel /home/chronos/user/MyFiles/Kernels/bzImage"
echo "----------------------------------------------------------"
