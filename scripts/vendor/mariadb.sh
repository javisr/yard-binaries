#!/bin/bash
# MariaDB server + clients: bundled wolfSSL and static GnuTLS, relocatable (run with --basedir).
source "$(dirname "$0")/common.sh"

VERSIONS=("11.8.3" "10.11.14")

# Keep the server/client TLS libraries compatible with the server's GPLv2-only license.
"$ROOT/scripts/vendor/mariadb-tls.sh"
TLS_PREFIX="$DEPS/mariadb-tls"

# Keep only what Yard uses (server, install-db, client, dump, admin, upgrade, check) – each extra
# tool is ~9 MB because OpenSSL is linked statically.
prune_mariadb() {
  local dest="$1"
  local keep="mariadbd mariadb mariadb-dump mariadb-admin mariadb-upgrade mariadb-check mariadb-install-db \
    mariadb-tzinfo-to-sql my_print_defaults resolveip resolve_stack_dump mariadb-secure-installation"
  for f in "$dest"/bin/*; do
    name="$(basename "$f")"
    target="$name"
    if [ -L "$f" ]; then target="$(basename "$(readlink "$f")")"; fi
    case " $keep " in *" $target "*) ;; *) rm -f "$f" ;; esac
  done
  rm -rf "$dest"/lib/*.a "$dest/lib/pkgconfig" "$dest/mysql-test" "$dest/man" "$dest/include"
}

if [ "${1:-}" = "--prune-only" ]; then
  for d in "$SERVICES_OUT"/mariadb/*; do prune_mariadb "$d"; done
  exit 0
fi

for VERSION in "${VERSIONS[@]}"; do
  DEST="$SERVICES_OUT/mariadb/$VERSION"
  if [ -x "$DEST/bin/mariadbd" ]; then echo "MariaDB $VERSION already built"; continue; fi
  fetch "https://archive.mariadb.org/mariadb-${VERSION}/source/mariadb-${VERSION}.tar.gz" "mariadb-${VERSION}"
  # Preserve the exact PCRE2 source in the source release; avoid CMake's untracked ZIP download.
  fetch "https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.45/pcre2-10.45.tar.gz" "pcre2-10.45"
  FMT_VERSION="11.1.4"
  [ "$VERSION" = "10.11.14" ] && FMT_VERSION="11.0.2"
  fetch "https://github.com/fmtlib/fmt/archive/refs/tags/${FMT_VERSION}.tar.gz" "fmt-$FMT_VERSION"
  python3 - "$SRC/mariadb-$VERSION/cmake" "$WORK/downloads" "$ROOT/scripts/vendor/sources.lock.json" "$FMT_VERSION" <<'PY'
import json, pathlib, re, sys
sources = {c['id']: c for c in json.loads(pathlib.Path(sys.argv[3]).read_text())['sources']}
for filename, name in [('pcre.cmake', 'pcre2-10.45'), ('libfmt.cmake', 'fmt-' + sys.argv[4])]:
    p = pathlib.Path(sys.argv[1]) / filename
    source = sources[name]
    text = p.read_text()
    text = re.sub(r'^    URL\s+"[^"]+"', '    URL "' + source['url'] + '"', text, flags=re.M)
    text = re.sub(r'^    URL_(?:MD5|HASH) [^\n]+', '    URL_HASH SHA256=' + source['sha256'], text, flags=re.M)
    if 'DOWNLOAD_DIR' not in text:
        text = text.replace('    URL_HASH ', '    DOWNLOAD_DIR "' + sys.argv[2] + '"\n    DOWNLOAD_NAME "' + name + '.tar.gz"\n    URL_HASH ')
    p.write_text(text)
PY
  log "Building MariaDB $VERSION (takes 20–30 minutes)"
  BUILD="$SRC/mariadb-${VERSION}-build"
  mkdir -p "$BUILD" && cd "$BUILD"   # object files are reused between runs
  rm -f CMakeCache.txt               # …but never a cache that may contain paths outside the system
  cmake "$SRC/mariadb-${VERSION}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$DEST" \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=14.0 \
    -DINSTALL_LAYOUT=STANDALONE \
    -DWITH_SSL=bundled -DCONC_WITH_SSL=GNUTLS \
    -DGNUTLS_INCLUDE_DIR="$TLS_PREFIX/include" \
    -DGNUTLS_LIBRARY="$TLS_PREFIX/lib/libgnutls.a" \
    -DCMAKE_C_STANDARD_LIBRARIES="$TLS_PREFIX/lib/libgnutls.a $TLS_PREFIX/lib/libhogweed.a $TLS_PREFIX/lib/libnettle.a $TLS_PREFIX/lib/libgmp.a -framework Security -framework CoreFoundation" \
    -DCMAKE_CXX_STANDARD_LIBRARIES="$TLS_PREFIX/lib/libgnutls.a $TLS_PREFIX/lib/libhogweed.a $TLS_PREFIX/lib/libnettle.a $TLS_PREFIX/lib/libgmp.a -framework Security -framework CoreFoundation" \
    -DWITH_ZLIB=bundled -DWITH_PCRE=bundled \
    -DWITH_UNIT_TESTS=OFF -DWITH_EMBEDDED_SERVER=OFF -DWITH_WSREP=OFF \
    -DINSTALL_MYSQLTESTDIR= -DINSTALL_SQLBENCHDIR= \
    -DPLUGIN_AUTH_PAM=NO -DPLUGIN_AUTH_PAM_V1=NO \
    -DPLUGIN_MROONGA=NO -DPLUGIN_ROCKSDB=NO -DPLUGIN_SPIDER=NO -DPLUGIN_TOKUDB=NO \
    -DPLUGIN_CONNECT=NO -DPLUGIN_OQGRAPH=NO -DPLUGIN_S3=NO -DPLUGIN_COLUMNSTORE=NO \
    -DPLUGIN_AUTH_GSSAPI=NO -DPLUGIN_AUTH_GSSAPI_CLIENT=OFF -DPLUGIN_HASHICORP_KEY_MANAGEMENT=NO \
    -DPLUGIN_PROVIDER_LZ4=NO -DPLUGIN_PROVIDER_LZO=NO -DPLUGIN_PROVIDER_SNAPPY=NO \
    -DPLUGIN_PROVIDER_BZIP2=NO -DPLUGIN_PROVIDER_LZMA=NO -DPLUGIN_PROVIDER_ZSTD=NO \
    -DCLIENT_PLUGIN_ZSTD=OFF -DWITH_ROCKSDB_ZSTD=OFF \
    -DCMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local" -DCMAKE_SYSTEM_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local" \
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF \
    -DCONNECT_WITH_MONGO=OFF -DWITH_MARIABACKUP=OFF -DWITH_JEMALLOC=no \
    -DBISON_EXECUTABLE="$TOOLS/bison/bin/bison" > "$WORK/mariadb-$VERSION-cmake.log" 2>&1 \
    || { tail -40 "$WORK/mariadb-$VERSION-cmake.log"; exit 1; }
  make -j"$JOBS" > "$WORK/mariadb-$VERSION-make.log" 2>&1 || { grep -E "error" "$WORK/mariadb-$VERSION-make.log" | tail -20; exit 1; }
  make install > "$WORK/mariadb-$VERSION-install.log" 2>&1 || { tail -20 "$WORK/mariadb-$VERSION-install.log"; exit 1; }

  # Shared client library: make it relative so the install directory can move.
  for lib in "$DEST"/lib/*.dylib; do
    [ -f "$lib" ] || continue
    install_name_tool -id "@rpath/$(basename "$lib")" "$lib" 2>/dev/null || true
  done
  while IFS= read -r -d '' f; do
    if file "$f" | grep -q 'Mach-O'; then
      for dep in $(otool -L "$f" | tail -n +2 | awk '{print $1}' | grep "^$DEST/lib/" || true); do
        install_name_tool -change "$dep" "@executable_path/../lib/$(basename "$dep")" "$f" 2>/dev/null || true
      done
    fi
  done < <(find "$DEST/bin" -type f -print0)

  prune_mariadb "$DEST"
  rm -rf "$DEST/mysql-test" "$DEST/sql-bench" "$DEST/man" "$DEST/include" "$DEST/support-files/policy" "$DEST/data"
  finalize_binaries "$DEST"
  check_relocatable "$DEST"
  record_build "mariadb-$VERSION"
done
