#!/bin/bash
# PostgreSQL 14–18 (server + psql/pg_dump/createdb…) with pgvector, native arm64, relocatable.
# Links only against macOS system libraries (libedit from the SDK) and our static OpenSSL.
source "$(dirname "$0")/common.sh"

VERSIONS=("18.0" "17.6" "16.10" "15.14" "14.19")
PGVECTOR_VERSION="0.8.1"

# Static OpenSSL shared with the MariaDB build.
"$ROOT/scripts/vendor/openssl.sh"

fetch "https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz" "pgvector-${PGVECTOR_VERSION}"

for VERSION in "${VERSIONS[@]}"; do
  DEST="$SERVICES_OUT/postgresql/$VERSION"
  if [ -x "$DEST/bin/postgres" ] && [ -x "$DEST/bin/psql" ]; then echo "PostgreSQL $VERSION already built"; continue; fi
  fetch "https://ftp.postgresql.org/pub/source/v${VERSION}/postgresql-${VERSION}.tar.gz" "postgresql-${VERSION}"
  log "Building PostgreSQL $VERSION"
  cd "$SRC/postgresql-${VERSION}"
  make distclean >/dev/null 2>&1 || true
  ./configure --prefix="$DEST" \
    --with-openssl --with-libedit-preferred \
    --without-icu --without-zstd --without-lz4 --without-llvm \
    --with-includes="$DEPS/openssl/include" --with-libraries="$DEPS/openssl/lib" \
    > "$WORK/postgresql-$VERSION-configure.log" 2>&1 || { tail -30 "$WORK/postgresql-$VERSION-configure.log"; exit 1; }
  # libpq's "must not call exit" check trips on OpenSSL's own atexit cleanup when OpenSSL is linked
  # statically (a known false positive) – skip that check.
  sed -i '' 's/^all: all-lib libpq-refs-stamp$/all: all-lib/' src/interfaces/libpq/Makefile
  make -j"$JOBS" world-bin > "$WORK/postgresql-$VERSION-make.log" 2>&1 || { grep -nE "error:|\*\*\*|Undefined" "$WORK/postgresql-$VERSION-make.log" | tail -20; exit 1; }
  make install-world-bin > "$WORK/postgresql-$VERSION-install.log" 2>&1 || { tail -20 "$WORK/postgresql-$VERSION-install.log"; exit 1; }

  log "Building pgvector $PGVECTOR_VERSION for PostgreSQL $VERSION"
  rm -rf "$SRC/pgvector-build" && cp -R "$SRC/pgvector-${PGVECTOR_VERSION}" "$SRC/pgvector-build"
  cd "$SRC/pgvector-build"
  make PG_CONFIG="$DEST/bin/pg_config" OPTFLAGS="" > "$WORK/pgvector-$VERSION.log" 2>&1
  make PG_CONFIG="$DEST/bin/pg_config" install >> "$WORK/pgvector-$VERSION.log" 2>&1

  # libpq & friends: relative install names so the directory can move (PostgreSQL itself is relocatable).
  for lib in "$DEST"/lib/*.dylib; do
    [ -f "$lib" ] && [ ! -L "$lib" ] || continue
    install_name_tool -id "@rpath/$(basename "$lib")" "$lib" 2>/dev/null || true
  done
  while IFS= read -r -d '' f; do
    if file "$f" | grep -q 'Mach-O'; then
      for dep in $(otool -L "$f" | tail -n +2 | awk '{print $1}' | grep "^$DEST/lib/" || true); do
        case "$f" in
          "$DEST"/bin/*) install_name_tool -change "$dep" "@executable_path/../lib/$(basename "$dep")" "$f" 2>/dev/null || true ;;
          *) install_name_tool -change "$dep" "@loader_path/../$(basename "$dep")" "$f" 2>/dev/null || true ;;
        esac
      done
    fi
  done < <(find "$DEST/bin" "$DEST/lib" -type f -print0)

  rm -rf "$DEST/include" "$DEST/share/doc" "$DEST/share/man" "$DEST"/lib/*.a "$DEST/lib/pgxs" "$DEST/lib/pkgconfig"
  finalize_binaries "$DEST"
  check_relocatable "$DEST"
  record_build "postgresql-$VERSION"
done
