#!/bin/bash
# nginx for Yard.app: static OpenSSL, PCRE2 and zlib, so it only depends on macOS system libraries.
source "$(dirname "$0")/common.sh"

NGINX_VERSION="1.28.0"
OPENSSL_VERSION="3.5.2"
PCRE2_VERSION="10.45"
ZLIB_VERSION="1.3.1"

if [ -x "$OUT/bin/nginx" ] && "$OUT/bin/nginx" -v 2>&1 | grep -q "$NGINX_VERSION"; then
  echo "nginx $NGINX_VERSION already built"; exit 0
fi

fetch "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" "nginx-${NGINX_VERSION}"
fetch "https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}/openssl-${OPENSSL_VERSION}.tar.gz" "openssl-${OPENSSL_VERSION}"
fetch "https://github.com/PCRE2Project/pcre2/releases/download/pcre2-${PCRE2_VERSION}/pcre2-${PCRE2_VERSION}.tar.gz" "pcre2-${PCRE2_VERSION}"
fetch "https://zlib.net/fossils/zlib-${ZLIB_VERSION}.tar.gz" "zlib-${ZLIB_VERSION}"

log "Building nginx $NGINX_VERSION"
cd "$SRC/nginx-${NGINX_VERSION}"
./configure \
  --prefix=/usr/local/yard-nginx \
  --with-cc-opt="-arch arm64 -mmacosx-version-min=14.0 -O2" \
  --with-ld-opt="-arch arm64 -mmacosx-version-min=14.0" \
  --with-openssl="$SRC/openssl-${OPENSSL_VERSION}" \
  --with-openssl-opt="no-shared no-tests darwin64-arm64-cc" \
  --with-pcre="$SRC/pcre2-${PCRE2_VERSION}" --with-pcre-jit \
  --with-zlib="$SRC/zlib-${ZLIB_VERSION}" \
  --with-http_ssl_module --with-http_v2_module --with-http_sub_module \
  --with-http_gzip_static_module --with-http_realip_module --with-http_stub_status_module \
  --with-threads \
  --without-http_uwsgi_module --without-http_scgi_module \
  --http-log-path=/dev/null --error-log-path=stderr >/dev/null
make -j"$JOBS" >/dev/null
cp objs/nginx "$OUT/bin/nginx"
finalize_binaries "$OUT/bin"
check_relocatable "$OUT/bin"
record_build "nginx-$NGINX_VERSION"
"$OUT/bin/nginx" -V 2>&1 | head -1
