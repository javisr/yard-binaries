#!/bin/bash
# Shared helpers for the vendor build scripts. These run ONLY on the maintainer's build Mac.
# End users never compile anything: the results are bundled inside Yard.app.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${YARD_VENDOR_BUILD_ROOT:-$ROOT/vendor-build}"
SRC="$WORK/src"
TOOLS="$WORK/tools"
DEPS="$WORK/deps"
OUT="$WORK/out"
SERVICES_OUT="$OUT/services"
JOBS="${YARD_BUILD_JOBS:-$(sysctl -n hw.ncpu)}"

export MACOSX_DEPLOYMENT_TARGET=14.0
export CFLAGS="${CFLAGS:-} -arch arm64 -mmacosx-version-min=14.0 -O2"
export CXXFLAGS="${CXXFLAGS:-} -arch arm64 -mmacosx-version-min=14.0 -O2"
export LDFLAGS="${LDFLAGS:-} -arch arm64 -mmacosx-version-min=14.0"
# Never pick up Homebrew libraries on the build Mac – bundled binaries must only need macOS.
export PKG_CONFIG_PATH="" PKG_CONFIG_LIBDIR="$DEPS/pkgconfig"
export PATH="$TOOLS/cmake/CMake.app/Contents/bin:$TOOLS/go/bin:$TOOLS/bison/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$SRC" "$TOOLS" "$DEPS" "$OUT/bin" "$SERVICES_OUT"

if ! xcode-select -p >/dev/null 2>&1; then
  echo "The Xcode Command Line Tools are required on the build Mac: xcode-select --install" >&2
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "Build on an Apple Silicon Mac." >&2
  exit 1
fi

log() { printf '\n\033[1;31m▸\033[0m \033[1m%s\033[0m\n' "$*"; }

# fetch URL DIRNAME [STRIP] – downloads and extracts a source tarball into $SRC/DIRNAME once.
fetch() {
  local url="$1" dir="$2"
  python3 "$ROOT/scripts/vendor/release.py" --work "$WORK" source "$dir" "$url" "$SRC/$dir"
}

record_build() {
  python3 "$ROOT/scripts/vendor/release.py" --work "$WORK" receipt "$1"
}

# Strips and ad-hoc signs every Mach-O file below a directory (strip invalidates signatures on arm64).
finalize_binaries() {
  local dir="$1"
  while IFS= read -r -d '' f; do
    if file "$f" | grep -q 'Mach-O'; then
      strip -x "$f" 2>/dev/null || true
      codesign --force --sign - "$f" >/dev/null
    fi
  done < <(find "$dir" -type f -perm -u+x -print0)
}

# Fails if any binary links against something outside the system or its own bundle.
check_relocatable() {
  local dir="$1" bad=0
  while IFS= read -r -d '' f; do
    if file "$f" | grep -q 'Mach-O'; then
      if otool -L "$f" | tail -n +2 | awk '{print $1}' | grep -vE '^(/usr/lib/|/System/|@rpath/|@executable_path/|@loader_path/)' | grep -q .; then
        echo "✗ $f links non-system libraries:" >&2
        otool -L "$f" | tail -n +2 >&2
        bad=1
      fi
    fi
  done < <(find "$dir" -type f -print0)
  return $bad
}
