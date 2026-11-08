#!/bin/bash
# Build-time tools for the maintainer Mac (never shipped): CMake, Go and GNU Bison.
# Official binary releases are unpacked into vendor-build/tools – no Homebrew.
source "$(dirname "$0")/common.sh"

CMAKE_VERSION="3.31.8"
GO_VERSION="1.24.6"
BISON_VERSION="3.8.2"

if [ ! -x "$TOOLS/cmake/CMake.app/Contents/bin/cmake" ]; then
  log "Installing CMake $CMAKE_VERSION (build tool)"
  mkdir -p "$TOOLS/cmake"
  curl -fsSL "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/cmake-${CMAKE_VERSION}-macos-universal.tar.gz" \
    | tar xz -C "$TOOLS/cmake" --strip-components=1
fi

if [ ! -x "$TOOLS/go/bin/go" ]; then
  log "Installing Go $GO_VERSION (build tool)"
  curl -fsSL "https://go.dev/dl/go${GO_VERSION}.darwin-arm64.tar.gz" | tar xz -C "$TOOLS"
fi

# macOS ships Bison 2.3; MariaDB needs a newer one.
if [ ! -x "$TOOLS/bison/bin/bison" ]; then
  fetch "https://ftp.gnu.org/gnu/bison/bison-${BISON_VERSION}.tar.gz" "bison-${BISON_VERSION}"
  log "Building GNU Bison $BISON_VERSION (build tool)"
  cd "$SRC/bison-${BISON_VERSION}"
  ./configure --prefix="$TOOLS/bison" --disable-nls >/dev/null
  make -j"$JOBS" >/dev/null
  make install >/dev/null
fi

cmake --version | head -1
go version
bison --version | head -1
