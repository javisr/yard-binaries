#!/bin/bash
# Maintainer/build-machine only. Users install precompiled modules, never a compiler or PHP SDK.
set -euo pipefail
BUILD_REPO="$(cd "$(dirname "$0")/../.." && pwd)"
exec python3 "$BUILD_REPO/scripts/vendor/build-php-extensions.py" "$@"
