#!/bin/bash
# Builds every binary Yard bundles. Run once on the build Mac (≈45 minutes, mostly MariaDB):
#   scripts/vendor/build-all.sh            # everything
#   scripts/vendor/build-all.sh nginx redis  # only some parts
# Results: vendor-build/out/{bin,services,manifest.json} → copied into Yard.app by scripts/build-app.sh.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PARTS=("$@")
[ ${#PARTS[@]} -eq 0 ] && PARTS=(tools nginx redis minio mariadb postgresql)

for part in "${PARTS[@]}"; do
  case "$part" in
    tools) "$DIR/tools.sh" ;;
    nginx) "$DIR/nginx.sh" ;;
    redis|valkey) "$DIR/redis.sh" ;;
    minio) "$DIR/tools.sh" >/dev/null; "$DIR/minio.sh" ;;
    mariadb) "$DIR/tools.sh" >/dev/null; "$DIR/mariadb.sh" ;;
    openssl) "$DIR/openssl.sh" ;;
    postgresql|postgres) "$DIR/postgresql.sh" ;;
    *) echo "Unknown part: $part" >&2; exit 1 ;;
  esac
done
"$DIR/manifest.sh"
echo "✓ Vendor binaries ready in vendor-build/out"
