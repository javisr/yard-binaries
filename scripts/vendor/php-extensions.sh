#!/bin/bash
# Maintainer/build-machine only. Produces a verified Xdebug module; users never compile PHP.
set -euo pipefail
BUILD_REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PHP_EXTENSION_WORK="${YARD_EXTENSION_BUILD_ROOT:-$BUILD_REPO/vendor-build/php-extension-build}"
PHP_EXTENSION_OUT="$BUILD_REPO/vendor-build/out/php-extensions/xdebug/8.4"
PHP_CLI_INPUT="${1:?Usage: php-extensions.sh /path/to/php84 /path/to/php-fpm84}"
PHP_FPM_INPUT="${2:?Supply the matching PHP-FPM executable}"
PHP_AUTOCONF="${PHP_AUTOCONF:-$(command -v autoconf || true)}"
PHP_AUTOHEADER="${PHP_AUTOHEADER:-$(command -v autoheader || true)}"
export PHP_AUTOCONF PHP_AUTOHEADER MACOSX_DEPLOYMENT_TARGET=14.0
export CFLAGS="-O2 -arch arm64 -mmacosx-version-min=14.0"
export LDFLAGS="-arch arm64 -mmacosx-version-min=14.0"
if [[ "$(uname -m)" != arm64 || -z "$PHP_AUTOCONF" || -z "$PHP_AUTOHEADER" ]]; then
  echo "Build on Apple Silicon with the Xcode command line tools and Autoconf/Autoheader installed on the build machine." >&2
  exit 1
fi
if [[ "$PHP_EXTENSION_WORK" == *' '* ]]; then
  echo "Choose YARD_EXTENSION_BUILD_ROOT without spaces (upstream phpize uses unquoted include flags). Installation paths may contain spaces." >&2
  exit 1
fi
for binary in "$PHP_CLI_INPUT" "$PHP_FPM_INPUT"; do
  "$binary" -n -i | /usr/bin/grep 'PHP Version => 8.4.23' >/dev/null || { echo "This SDK is pinned to PHP 8.4.23." >&2; exit 1; }
  "$binary" -n -i | /usr/bin/grep 'PHP Extension Build => API20240924,NTS' >/dev/null || exit 1
done
mkdir -p "$PHP_EXTENSION_WORK"
fetch_verified() {
  local name="$1" url="$2" sha="$3"
  if [ ! -f "$PHP_EXTENSION_WORK/$name" ]; then
    curl -fL --retry 2 --max-time 180 "$url" -o "$PHP_EXTENSION_WORK/$name.tmp"
    mv "$PHP_EXTENSION_WORK/$name.tmp" "$PHP_EXTENSION_WORK/$name"
  fi
  printf '%s  %s\n' "$sha" "$PHP_EXTENSION_WORK/$name" | shasum -a 256 -c -
}
fetch_verified php-8.4.23.tar.xz https://www.php.net/distributions/php-8.4.23.tar.xz 1ab9f52008414e43bb2427ffa288eff2a4de39e1a830f957e800ba368d887a72
fetch_verified xdebug-3.5.3.tgz https://xdebug.org/files/xdebug-3.5.3.tgz f073de91bea046106abf4d6071c963ea71e58571df6ce58948ceca89d121cb2d
if [ ! -d "$PHP_EXTENSION_WORK/php-8.4.23" ]; then tar -xf "$PHP_EXTENSION_WORK/php-8.4.23.tar.xz" -C "$PHP_EXTENSION_WORK"; fi
if [ ! -d "$PHP_EXTENSION_WORK/xdebug-3.5.3" ]; then tar -xf "$PHP_EXTENSION_WORK/xdebug-3.5.3.tgz" -C "$PHP_EXTENSION_WORK"; fi
SDK="$PHP_EXTENSION_WORK/sdk"
cd "$PHP_EXTENSION_WORK/php-8.4.23"
./configure --prefix="$SDK" --disable-all --enable-cli --enable-fpm > "$PHP_EXTENSION_WORK/php-configure.log" 2>&1
make install-headers install-build > "$PHP_EXTENSION_WORK/php-headers.log" 2>&1
mkdir -p "$SDK/bin"
cp scripts/phpize scripts/php-config "$SDK/bin/"
chmod +x "$SDK/bin/phpize" "$SDK/bin/php-config"
cd "$PHP_EXTENSION_WORK/xdebug-3.5.3"
"$SDK/bin/phpize" > "$PHP_EXTENSION_WORK/xdebug-phpize.log" 2>&1
./configure --with-php-config="$SDK/bin/php-config" --enable-xdebug > "$PHP_EXTENSION_WORK/xdebug-configure.log" 2>&1
make clean >/dev/null
make -j"${YARD_BUILD_JOBS:-6}" > "$PHP_EXTENSION_WORK/xdebug-make.log" 2>&1
MODULE="$PHP_EXTENSION_WORK/xdebug-3.5.3/modules/xdebug.so"
file "$MODULE" | grep arm64 >/dev/null
xcrun vtool -show-build "$MODULE" | grep 'minos 14.0' >/dev/null
otool -L "$MODULE" | tail -n +2 | awk '{print $1}' | grep -vE '^(/usr/lib/|/System/)' > "$PHP_EXTENSION_WORK/non-system-links.txt" || true
test ! -s "$PHP_EXTENSION_WORK/non-system-links.txt" || { echo "Extension has a non-system runtime dependency." >&2; exit 1; }
codesign --force --sign - "$MODULE" >/dev/null 2>&1
for binary in "$PHP_CLI_INPUT" "$PHP_FPM_INPUT"; do
  XDEBUG_MODE=off "$binary" -n -d "zend_extension=$MODULE" -i | grep 'xdebug.mode =>' >/dev/null
done
STAGING="$PHP_EXTENSION_OUT.staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp "$MODULE" "$STAGING/xdebug.so"
cp LICENSE "$STAGING/LICENSE"
python3 - "$STAGING" <<'PY'
import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1]);files={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in p.iterdir() if x.is_file()}
manifest={'extension':'xdebug','extension_version':'3.5.3','php_minor':'8.4','php_api':'20240924','architecture':'arm64','minimum_macos':'14.0','source_url':'https://xdebug.org/files/xdebug-3.5.3.tgz','source_sha256':'f073de91bea046106abf4d6071c963ea71e58571df6ce58948ceca89d121cb2d','php_source_sha256':'1ab9f52008414e43bb2427ffa288eff2a4de39e1a830f957e800ba368d887a72','files':files}
(p/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
PY
mkdir -p "$(dirname "$PHP_EXTENSION_OUT")"
if [ -e "$PHP_EXTENSION_OUT" ]; then mv "$PHP_EXTENSION_OUT" "$PHP_EXTENSION_OUT.previous"; fi
mv "$STAGING" "$PHP_EXTENSION_OUT"
rm -rf "$PHP_EXTENSION_OUT.previous"
echo "Verified Xdebug 3.5.3 for PHP 8.4.23 arm64 CLI/FPM: $PHP_EXTENSION_OUT"
