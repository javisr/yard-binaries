#!/bin/bash
# Compatible static TLS dependencies for MariaDB's separately distributed clients.
# Nettle and GMP use their GPLv2-or-later options; no LGPLv3-only combination with GPLv2.
source "$(dirname "$0")/common.sh"
PREFIX="$DEPS/mariadb-tls"
mkdir -p "$PREFIX"
fetch "https://distfiles.ariadne.space/pkgconf/pkgconf-2.4.3.tar.xz" "pkgconf-2.4.3"
if [ ! -x "$TOOLS/pkgconf/bin/pkg-config" ]; then
  cd "$SRC/pkgconf-2.4.3"
  ./configure --prefix="$TOOLS/pkgconf" --with-pkg-config-dir="$PREFIX/lib/pkgconfig" > "$WORK/pkgconf-configure.log" 2>&1
  make -j"$JOBS" > "$WORK/pkgconf-build.log" 2>&1
  make install > "$WORK/pkgconf-install.log" 2>&1
  ln -sf pkgconf "$TOOLS/pkgconf/bin/pkg-config"
fi
export PATH="$TOOLS/pkgconf/bin:$PATH"
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"
fetch "https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz" "gmp-6.3.0"
fetch "https://ftp.gnu.org/gnu/nettle/nettle-3.10.2.tar.gz" "nettle-3.10.2"
fetch "https://www.gnupg.org/ftp/gcrypt/gnutls/v3.8/gnutls-3.8.10.tar.xz" "gnutls-3.8.10"
if [ ! -f "$PREFIX/lib/libgmp.a" ]; then
  cd "$SRC/gmp-6.3.0"
  ./configure --prefix="$PREFIX" --disable-shared --enable-static --with-pic --disable-assembly > "$WORK/gmp-configure.log" 2>&1
  make -j"$JOBS" > "$WORK/gmp-build.log" 2>&1
  make install > "$WORK/gmp-install.log" 2>&1
fi
export CPPFLAGS="-I$PREFIX/include"
export LDFLAGS="$LDFLAGS -L$PREFIX/lib"
if [ ! -f "$PREFIX/lib/libhogweed.a" ]; then
  cd "$SRC/nettle-3.10.2"
  ./configure --prefix="$PREFIX" --disable-shared --enable-static --disable-documentation --disable-assembler > "$WORK/nettle-configure.log" 2>&1
  make -j"$JOBS" > "$WORK/nettle-build.log" 2>&1
  make install > "$WORK/nettle-install.log" 2>&1
fi
if [ ! -f "$PREFIX/lib/libgnutls.a" ] || ! /usr/bin/nm -g "$PREFIX/lib/libgnutls.a" 2>/dev/null | /usr/bin/grep ' T _gnutls_init$' >/dev/null; then
  cd "$SRC/gnutls-3.8.10"
  rm -f lib/libgnutls.la lib/.libs/libgnutls.a
  NETTLE_CFLAGS="-I$PREFIX/include" NETTLE_LIBS="-L$PREFIX/lib -lnettle" \
  HOGWEED_CFLAGS="-I$PREFIX/include" HOGWEED_LIBS="-L$PREFIX/lib -lhogweed -lnettle -lgmp" \
  GMP_CFLAGS="-I$PREFIX/include" GMP_LIBS="-L$PREFIX/lib -lgmp" \
  ./configure --prefix="$PREFIX" --disable-shared --enable-static --with-pic \
    --disable-doc --disable-tools --disable-tests --disable-cxx --disable-nls --disable-guile \
    --disable-dane --without-p11-kit --without-idn --without-tpm --without-tpm2 \
    --without-zlib --without-brotli --without-zstd --with-included-libtasn1 \
    --with-included-unistring > "$WORK/gnutls-configure.log" 2>&1
  make -j"$JOBS" > "$WORK/gnutls-build.log" 2>&1
  make install > "$WORK/gnutls-install.log" 2>&1
  /usr/bin/nm -g "$PREFIX/lib/libgnutls.a" | /usr/bin/grep ' T _gnutls_init$' >/dev/null
fi
