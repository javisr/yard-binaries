#!/bin/bash
# MinIO server built from its official source tag (MinIO no longer publishes binaries).
source "$(dirname "$0")/common.sh"

TAG="RELEASE.2025-09-07T16-13-09Z"
VERSION="RELEASE.2025-09-07"
DEST="$SERVICES_OUT/minio/$VERSION/bin"

if [ -x "$DEST/minio" ]; then echo "MinIO $VERSION already built"; exit 0; fi
fetch "https://github.com/minio/minio/archive/refs/tags/${TAG}.tar.gz" "minio-${VERSION}"
log "Building MinIO $VERSION"
cd "$SRC/minio-${VERSION}"
mkdir -p "$DEST"
export GOPATH="$WORK/gopath" GOCACHE="$WORK/gocache" GOTOOLCHAIN=local GOFLAGS=-mod=readonly
# Retain all module sources and notices, including weak copyleft components.
go mod vendor
GOBIN="$TOOLS/license-audit" go install github.com/google/go-licenses/v2@v2.0.1
python3 "$ROOT/scripts/vendor/release.py" --work "$WORK" audit-go \
  --go "$TOOLS/go/bin/go" --auditor "$TOOLS/license-audit/go-licenses"
export GOFLAGS=-mod=vendor
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -trimpath -tags kqueue \
  -ldflags "-s -w -X github.com/minio/minio/cmd.Version=${TAG#RELEASE.} -X github.com/minio/minio/cmd.ReleaseTag=${TAG}" \
  -o "$DEST/minio" .
finalize_binaries "$DEST"
check_relocatable "$DEST"
record_build "minio-$VERSION"
"$DEST/minio" --version | head -1
