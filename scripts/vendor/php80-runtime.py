#!/usr/bin/env python3
"""Build/stage PHP 8.0 with extension exports; never installs into a user's Yard home."""
import argparse
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
spec = importlib.util.spec_from_file_location("php_extension_builder", HERE / "build-php-extensions.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
release = builder.release
BASE_MODULES = "Core,date,libxml,openssl,pcre,sqlite3,zlib,apcu,bcmath,bz2,calendar,ctype,curl,dba,dom,sockets,event,hash,fileinfo,filter,ftp,gd,gmp,SPL,iconv,session,standard,intl,json,mbstring,mysqlnd,mysqli,pcntl,imap,PDO,pdo_mysql,pgsql,Phar,posix,protobuf,readline,redis,Reflection,imagick,shmop,SimpleXML,soap,sodium,exif,swoole,sysvmsg,sysvsem,sysvshm,tokenizer,xml,xmlreader,xmlwriter,xsl,zip,Zend OPcache".split(",")


def replace_once(path, old, new):
    text = path.read_text()
    if new in text:
        return
    if text.count(old) != 1:
        raise ValueError("Pinned build recipe changed: " + str(path))
    path.write_text(text.replace(old, new))


def patch_spc(workspace):
    mac = workspace / "src/SPC/builder/macos/MacOSBuilder.php"
    replace_once(mac, "'EXTRA_LDFLAGS_PROGRAM' => '-L' . BUILD_LIB_PATH,",
                 "'EXTRA_LDFLAGS_PROGRAM' => '-L' . BUILD_LIB_PATH . ' -Wl,-export_dynamic',")
    replace_once(mac, "'LDFLAGS' => '-L' . BUILD_LIB_PATH,", "'LDFLAGS' => '-L' . BUILD_LIB_PATH,\n            'LIBS' => '-lpgcommon -lpgport',\n            'CC' => 'cc -std=gnu11',\n            'ac_cv_prog_cc_c23' => 'no',\n            'ac_cv_func_explicit_bzero' => 'no',")
    patcher = workspace / "src/SPC/store/SourcePatcher.php"
    start = "public static function patchBeforeBuildconf(BuilderBase $builder): void\n    {"
    block = '''
        // Yard PHP 8.0: current libxml headers, ICU's C++17 requirement and macOS PTY declarations.
        if (PHP_OS_FAMILY === 'Darwin' && $builder->getPHPVersionID() < 80100) {
            FileSystem::replaceFileStr(SOURCE_PATH . '/php-src/ext/libxml/libxml.c', 'int compression ATTRIBUTE_UNUSED)', 'int compression)');
            FileSystem::replaceFileStr(SOURCE_PATH . '/php-src/ext/intl/config.m4', '-std=c++11', '-std=c++17');
            FileSystem::replaceFileStr(SOURCE_PATH . '/php-src/ext/standard/proc_open.c', '#  include <util.h>', "#  include <util.h>\\n#  if defined(__APPLE__)\\nextern int openpty(int *, int *, char *, struct termios *, struct winsize *);\\n#  endif");
        }
'''
    replace_once(patcher, start, start + block)
    swoole = workspace / "src/SPC/builder/extension/swoole.php"
    start = "public function getUnixConfigureArg(bool $shared = false): string\n    {"
    block = '''
        // Preserve the features in Yard's existing PHP 8.0 Swoole build.
        if ($this->builder->getPHPVersionID() < 80100) {
            return '--enable-swoole --enable-swoole-coro-time --disable-thread-context --disable-swoole-curl --enable-swoole-pgsql --enable-swoole-sqlite --enable-openssl --enable-cares --with-brotli-dir=' . BUILD_ROOT_PATH . ' --with-nghttp2-dir=' . BUILD_ROOT_PATH;
        }
'''
    replace_once(swoole, start, start + block)


def stage(workspace, output, sources):
    source = workspace / "source/php-src"
    cli, fpm = source / "sapi/cli/php", source / "sapi/fpm/php-fpm"
    patch, api = builder.runtime(cli)
    if patch != "8.0.30" or builder.runtime(fpm) != (patch, api):
        raise ValueError("Expected matching PHP 8.0.30 CLI/FPM builds")
    for binary in [cli, fpm]:
        symbols = subprocess.check_output(["/usr/bin/nm", "-gU", str(binary)], text=True)
        if not all(name in symbols for name in ["_OnUpdateBool", "_executor_globals", "_zend_execute_ex", "_zend_hash_find"]):
            raise ValueError("PHP runtime does not export its extension API: " + str(binary))
    modules = json.loads(subprocess.check_output([str(cli), "-n", "-r", "echo json_encode(get_loaded_extensions());"], text=True))
    if not set(BASE_MODULES) <= set(modules):
        raise ValueError("The rebuilt runtime lost extensions: " + ", ".join(sorted(set(BASE_MODULES) - set(modules))))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        payload = pathlib.Path(temporary) / "payload"
        payload.mkdir()
        for binary, name in [(cli, "php"), (fpm, "php-fpm")]:
            shutil.copy2(binary, payload / name)
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(payload / name)], check=True, capture_output=True)
        for binary in release.inspect_macho(payload):
            if any(not link.startswith(("/usr/lib/", "/System/")) for link in binary["libraries"]):
                raise ValueError("Runtime must be self-contained: " + binary["path"])
        shutil.copy2(source / "LICENSE", payload / "LICENSE.PHP")
        release.collect_notices(workspace / "source", payload / "LICENSES")
        # Prebuilt dependency archives carry their own notice directories as well.
        for name in ["license", "licenses", "LICENSES"]:
            notices = workspace / "buildroot" / name
            if notices.is_dir():
                shutil.copytree(notices, payload / "LICENSES" / ("prebuilt-" + name), dirs_exist_ok=True)
        inputs = {}
        for path in (workspace / "downloads").iterdir():
            if path.is_file() and not path.name.startswith("."):
                inputs[path.name] = release.sha(path)
            elif path.is_dir() and (path / ".git").exists():
                inputs[path.name] = {"git": subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()}
        release.write(payload / "SOURCE-PROVENANCE.json", {"php": sources["php-8.0.30"], "builder": sources["static-php-cli-956fa13e"],
                      "protobuf": sources["protobuf-4.29.0RC2"], "swoole": sources["swoole-5.1.5"], "inputs": inputs,
                      "recipeSHA256": release.sha(pathlib.Path(__file__)), "modules": modules})
        files = {path.relative_to(payload).as_posix(): release.sha(path) for path in payload.rglob("*") if path.is_file()}
        release.write(payload / "manifest.json", {"schema": 1, "phpMinor": "8.0", "phpVersion": patch, "version": patch + "-yard1",
                      "phpAPI": api, "architecture": "arm64", "minimumMacOS": "14.0", "files": files})
        builder.publish(payload, output)
    print("Staged PHP 8.0.30-yard1 with all original extensions and exported CLI/FPM APIs: " + str(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-php", type=pathlib.Path)
    parser.add_argument("--composer", type=pathlib.Path)
    parser.add_argument("--work", type=pathlib.Path, default=REPO / "vendor-build/php80-runtime")
    parser.add_argument("--stage-from", type=pathlib.Path, help="Stage an already compiled SPC workspace after checking both binaries")
    parser.add_argument("--output", type=pathlib.Path, default=REPO / "vendor-build/out/php-runtimes/8.0")
    args = parser.parse_args()
    try:
        sources = {s["id"]: s for s in release.read(HERE / "sources.lock.json")["sources"]}
        if args.stage_from:
            workspace = args.stage_from.resolve()
        else:
            if not args.bootstrap_php or not args.composer:
                parser.error("Supply --bootstrap-php and --composer, or --stage-from an existing build workspace")
            if " " in str(args.work.resolve()):
                raise ValueError("Choose a build workspace without spaces; installed Yard paths can contain spaces")
            for tool in ["autoconf", "autoheader", "automake", "re2c", "bison", "cmake"]:
                if not shutil.which(tool):
                    raise ValueError("Required build-machine tool is missing: " + tool)
            workspace = builder.source_tree(sources["static-php-cli-956fa13e"], args.work.resolve())
            environment = dict(os.environ, MACOSX_DEPLOYMENT_TARGET="14.0", SPC_CONCURRENCY=os.environ.get("YARD_BUILD_JOBS", "6"))
            php = str(args.bootstrap_php.resolve())
            subprocess.run([php, "-n", str(args.composer.resolve()), "install", "--no-dev", "--no-scripts", "--no-plugins", "--prefer-dist", "--no-interaction"], cwd=workspace, env=environment, check=True)
            patch_spc(workspace)
            excluded = {"Core", "date", "libxml", "pcre", "hash", "SPL", "standard", "json", "Reflection"}
            extensions = ["opcache" if name == "Zend OPcache" else name.lower() for name in BASE_MODULES if name not in excluded] + ["mbregex"]
            arguments = [php, "-n", "bin/spc", "download", "--with-php=8.0", "--for-extensions=" + ",".join(extensions), "--prefer-pre-built", "--no-interaction", "--no-motd"]
            for name, component in [("php-src", "php-8.0.30"), ("protobuf", "protobuf-4.29.0RC2"), ("swoole", "swoole-5.1.5")]:
                # Verify the pinned inputs before SPC stages the same upstream bytes.
                release.download(sources[component], args.work.resolve() / "downloads")
                arguments.append("--custom-url=" + name + ":" + sources[component]["url"])
            subprocess.run(arguments, cwd=workspace, env=environment, check=True)
            locks = release.read(workspace / "downloads/.lock.json")
            for name, component in [("php-src", "php-8.0.30"), ("protobuf", "protobuf-4.29.0RC2"), ("swoole", "swoole-5.1.5")]:
                artifact = workspace / "downloads" / locks[name]["filename"]
                if release.sha(artifact) != sources[component]["sha256"]:
                    raise ValueError("Pinned runtime source checksum mismatch: " + name)
            subprocess.run([php, "-n", "bin/spc", "build", ",".join(extensions), "--build-cli", "--build-fpm", "--no-strip", "--with-config-file-path=", "--with-config-file-scan-dir=", "--no-interaction", "--no-motd"], cwd=workspace, env=environment, check=True)
        stage(workspace, args.output.resolve(), sources)
    except (OSError, ValueError, KeyError, builder.release.ReleaseError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
