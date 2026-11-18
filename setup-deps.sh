#!/usr/bin/env bash
# Installs everything needed to run this project, on macOS (Homebrew) or Ubuntu/WSL 2 (apt).
# Safe to re-run.
#
# Linux also builds the photogrammetry backend from source: COLMAP with CUDA (geometry) and
# OpenMVS (textures). Override:
#   COLMAP_REF    git tag of COLMAP to build (default below)
#   COLMAP_SRC    where its source and build tree live (default ~/.local/src/colmap)
#   OPENMVS_REF   git tag of OpenMVS to build
#   OPENMVS_SRC   where its source and build tree live (default ~/.local/src/openMVS)
set -euo pipefail
cd "$(dirname "$0")"

COLMAP_REF="${COLMAP_REF:-4.2.1}"
COLMAP_SRC="${COLMAP_SRC:-$HOME/.local/src/colmap}"
OPENMVS_REF="${OPENMVS_REF:-v2.4.0}"
OPENMVS_SRC="${OPENMVS_SRC:-$HOME/.local/src/openMVS}"
MIN_CUDA="12.8" # first CUDA release that supports Blackwell (RTX 50 series, sm_120)

# Append a line to a shell rc file unless it is already there.
add_to_rc() {
  grep -qsF "$2" "$1" || echo "$2" >> "$1"
}

setup_macos() {
  # Homebrew
  if ! command -v brew > /dev/null; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi
  eval "$(/opt/homebrew/bin/brew shellenv 2> /dev/null || /usr/local/bin/brew shellenv)"

  # uv, fnm, pnpm, GStreamer, just
  brew bundle --file=Brewfile

  # Node (version from .node-version)
  eval "$(fnm env)"
  fnm install

  # Make new terminals pick the right Node automatically
  add_to_rc ~/.zshrc 'eval "$(fnm env --use-on-cd)"'
}

# -- Linux -------------------------------------------------------------------------------------

# Version a is at least version b (both dotted numbers).
version_at_least() {
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

cuda_version() {
  nvcc --version 2> /dev/null | sed -n 's/.*release \([0-9]*\.[0-9]*\).*/\1/p'
}

install_linux_tools() {
  export PATH="$HOME/.local/bin:$PATH"

  # System packages: GStreamer (camera and video-file sources), build tools for the tools below.
  sudo apt-get update
  sudo apt-get install -y \
    curl git unzip build-essential \
    gstreamer1.0-tools gstreamer1.0-libav \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly

  # uv installs the Python version in .python-version itself.
  command -v uv > /dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

  # just: apt if this Ubuntu has it, otherwise the official installer.
  if ! command -v just > /dev/null; then
    if apt-cache show just > /dev/null 2>&1; then
      sudo apt-get install -y just
    else
      mkdir -p "$HOME/.local/bin"
      curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh \
        | bash -s -- --to "$HOME/.local/bin"
    fi
  fi

  # fnm, then Node at the major version in .node-version.
  if ! command -v fnm > /dev/null; then
    curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell --install-dir "$HOME/.local/bin"
  fi
  eval "$(fnm env)"
  fnm install
  add_to_rc ~/.bashrc 'eval "$(fnm env --use-on-cd)"'

  # pnpm: the standalone build, so it does not depend on the Node version.
  if ! command -v pnpm > /dev/null; then
    curl -fsSL https://get.pnpm.io/install.sh | sh -
  fi
}

check_gpu() {
  if ! nvidia-smi > /dev/null 2>&1; then
    cat >&2 << 'EOF'
error: nvidia-smi does not work, so there is no GPU for CUDA.
On WSL 2, install the current NVIDIA driver on Windows (not inside WSL) and run `wsl --shutdown`.
EOF
    exit 1
  fi
}

install_cuda_toolkit() {
  local version
  version="$(cuda_version)"
  if [[ -z "$version" ]]; then
    # Only the toolkit: never install a Linux NVIDIA driver on WSL, Windows provides it.
    sudo apt-get install -y nvidia-cuda-toolkit nvidia-cuda-toolkit-gcc
    version="$(cuda_version)"
  fi
  if [[ -z "$version" ]] || ! version_at_least "$version" "$MIN_CUDA"; then
    cat >&2 << EOF
error: CUDA ${version:-not found}; the RTX 50 series needs $MIN_CUDA or newer.
Install a newer toolkit (not the driver) from NVIDIA's WSL-Ubuntu repository:
  https://developer.nvidia.com/cuda-downloads  (Linux > x86_64 > WSL-Ubuntu > deb (network))
then make sure \`nvcc --version\` works and re-run this script.
EOF
    exit 1
  fi
}

build_colmap() {
  local marker="$COLMAP_SRC/.installed-$COLMAP_REF"
  if [[ -e "$marker" ]] && command -v colmap > /dev/null; then
    echo "COLMAP $COLMAP_REF is already built and installed."
    return
  fi

  check_gpu
  install_cuda_toolkit

  # Dependencies as listed at https://colmap.github.io/install.html (Ubuntu, with CUDA).
  sudo apt-get install -y \
    cmake ninja-build libboost-program-options-dev libboost-graph-dev libboost-system-dev \
    libeigen3-dev libopenimageio-dev openimageio-tools libmetis-dev libgoogle-glog-dev \
    libgtest-dev libgmock-dev libsqlite3-dev libglew-dev qt6-base-dev libqt6opengl6-dev \
    libqt6openglwidgets6 qt6-svg-dev libcgal-dev libceres-dev libsuitesparse-dev \
    libcurl4-openssl-dev libssl-dev libmkl-full-dev
  sudo mkdir -p /usr/include/opencv4 # workaround for Ubuntu's OpenImageIO CMake config

  mkdir -p "$(dirname "$COLMAP_SRC")"
  if [[ ! -d "$COLMAP_SRC/.git" ]]; then
    git clone https://github.com/colmap/colmap.git "$COLMAP_SRC"
  fi
  git -C "$COLMAP_SRC" fetch --tags --force
  git -C "$COLMAP_SRC" checkout --force "$COLMAP_REF"

  # `native` builds for the GPU in this machine, so the binary is not portable to other GPUs.
  cmake -S "$COLMAP_SRC" -B "$COLMAP_SRC/build" -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DBLA_VENDOR=Intel10_64lp \
    -DCMAKE_CUDA_ARCHITECTURES=native
  cmake --build "$COLMAP_SRC/build"
  sudo cmake --install "$COLMAP_SRC/build"

  rm -f "$COLMAP_SRC"/.installed-*
  touch "$marker"
}

# OpenMVS paints the photos onto COLMAP's mesh (TextureMesh). Its tools install into
# /usr/local/bin/OpenMVS, which the photogrammetry module looks in. No CUDA: texturing does not
# use it. It builds its own libraries through vcpkg (from the git submodule), so the first build
# is slow and needs the network.
build_openmvs() {
  local marker="$OPENMVS_SRC/.installed-$OPENMVS_REF"
  if [[ -e "$marker" && -x /usr/local/bin/OpenMVS/TextureMesh ]]; then
    echo "OpenMVS $OPENMVS_REF is already built and installed."
    return
  fi

  # Dependencies as listed at https://github.com/cdcseacave/openMVS/wiki/Building (Linux).
  sudo apt-get install -y \
    git cmake ninja-build autoconf autoconf-archive automake libtool bison gfortran pkg-config \
    libxi-dev libx11-dev libxft-dev libxtst-dev libxext-dev libxrandr-dev libxinerama-dev \
    libxcursor-dev xorg-dev libgl-dev libglu1-mesa-dev nasm libavcodec-dev libavformat-dev \
    libavutil-dev libswscale-dev libswresample-dev

  mkdir -p "$(dirname "$OPENMVS_SRC")"
  if [[ ! -d "$OPENMVS_SRC/.git" ]]; then
    git clone --recurse-submodules https://github.com/cdcseacave/openMVS.git "$OPENMVS_SRC"
  fi
  git -C "$OPENMVS_SRC" fetch --tags --force
  git -C "$OPENMVS_SRC" checkout --force "$OPENMVS_REF"
  git -C "$OPENMVS_SRC" submodule update --init --recursive

  cmake -S "$OPENMVS_SRC" -B "$OPENMVS_SRC/make" -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DOpenMVS_USE_CUDA=OFF
  cmake --build "$OPENMVS_SRC/make"
  sudo cmake --install "$OPENMVS_SRC/make"

  rm -f "$OPENMVS_SRC"/.installed-*
  touch "$marker"
}

setup_linux() {
  command -v apt-get > /dev/null || { echo "error: needs apt (Ubuntu)" >&2; exit 1; }
  install_linux_tools
  build_colmap
  build_openmvs
  colmap -h | head -n 3
  [[ -x /usr/local/bin/OpenMVS/TextureMesh ]] \
    || { echo "error: OpenMVS did not install TextureMesh" >&2; exit 1; }

  # The camera feed is UDP on port 5600; WSL 2's default NAT mode does not forward it from the LAN.
  if grep -qi microsoft /proc/version && [[ "$(wslinfo --networking-type 2> /dev/null)" != mirrored ]]; then
    cat << 'EOF'

Warning: WSL is not using mirrored networking, so the bot's UDP video will not reach it.
In Windows, add to %UserProfile%\.wslconfig, then run `wsl --shutdown`:
  [wsl2]
  networkingMode=mirrored
EOF
  fi
}

case "$(uname -s)" in
  Darwin) setup_macos ;;
  Linux) setup_linux ;;
  *) echo "error: macOS and Ubuntu only" >&2; exit 1 ;;
esac

echo "Done. Open a new terminal, then run: just setup"
