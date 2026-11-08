#!/bin/bash
# Static OpenSSL for the maintainer's PostgreSQL/MariaDB builds; never a user installer.
source "$(dirname "$0")/common.sh"
OPENSSL_VERSION="3.5.2"
fetch "https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}/openssl-${OPENSSL_VERSION}.tar.gz" "openssl-${OPENSSL_VERSION}"
if [ -f "$DEPS/openssl/lib/libssl.a" ]; then exit 0; fi
log "Building static OpenSSL $OPENSSL_VERSION"
cd "$SRC/openssl-${OPENSSL_VERSION}"
./Configure darwin64-arm64-cc no-shared no-tests --prefix="$DEPS/openssl" --libdir=lib >/dev/null
make -j"$JOBS" > "$WORK/openssl-build.log" 2>&1
make install_sw > "$WORK/openssl-install.log" 2>&1
