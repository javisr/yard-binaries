#!/bin/bash
# Redis and Valkey for the Yard services catalog.
source "$(dirname "$0")/common.sh"

build_kv() { # type version url prefix
  local type="$1" version="$2" url="$3" prefix="$4"
  local dest="$SERVICES_OUT/$type/$version/bin"
  if [ -x "$dest/$prefix-server" ]; then echo "$type $version already built"; return; fi
  fetch "$url" "$type-$version"
  log "Building $type $version"
  cd "$SRC/$type-$version"
  make -j"$JOBS" BUILD_TLS=no MALLOC=libc USE_SYSTEMD=no \
    CFLAGS="$CFLAGS" LDFLAGS="$LDFLAGS" >/dev/null
  mkdir -p "$dest"
  cp "src/$prefix-server" "src/$prefix-cli" "$dest/"
  finalize_binaries "$dest"
  check_relocatable "$dest"
  record_build "$type-$version"
}

build_kv redis 7.4.5 https://download.redis.io/releases/redis-7.4.5.tar.gz redis
build_kv redis 7.0.15 https://download.redis.io/releases/redis-7.0.15.tar.gz redis
build_kv valkey 8.1.3 https://github.com/valkey-io/valkey/archive/refs/tags/8.1.3.tar.gz valkey
build_kv valkey 8.0.4 https://github.com/valkey-io/valkey/archive/refs/tags/8.0.4.tar.gz valkey
build_kv valkey 7.2.10 https://github.com/valkey-io/valkey/archive/refs/tags/7.2.10.tar.gz valkey
