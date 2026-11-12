#!/usr/bin/env python3
"""Build Xdebug and stage SPX against actual Yard CLI/FPM runtimes (build machine only)."""
import argparse
import importlib.util
import os
import pathlib
import platform
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
spec = importlib.util.spec_from_file_location("vendor_release", HERE / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def runtime(binary):
    info = subprocess.check_output([str(binary), "-n", "-i"], text=True)
    fields = dict(re.findall(r"^(PHP Version|PHP API|PHP Extension Build|Zend Extension Build|Thread Safety|Debug Build) => (.+)$", info, re.M))
    patch, api = fields.get("PHP Version", ""), fields.get("PHP API", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", patch) or not re.fullmatch(r"\d{8}", api):
        raise ValueError("Cannot identify PHP version and ABI: " + str(binary))
    if (fields.get("PHP Extension Build") != "API" + api + ",NTS"
            or fields.get("Zend Extension Build") != "API4" + api + ",NTS"
            or fields.get("Thread Safety") != "disabled" or fields.get("Debug Build") != "no"):
        raise ValueError("Use Yard's NTS, non-debug PHP build: " + str(binary))
    if "arm64" not in subprocess.check_output(["/usr/bin/file", str(binary)], text=True):
        raise ValueError("PHP must contain an arm64 executable: " + str(binary))
    return patch, api


def source_tree(component, work):
    archive = release.download(component, work / "downloads")
    destination = work / "sources" / component["id"]
    stamp = destination / ".yard-source.json"
    if stamp.exists() and release.read(stamp).get("sha256") == component["sha256"]:
        return destination
    if destination.exists():
        raise ValueError("Unverified source directory; use a fresh build root: " + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        tree = release.extract(archive, pathlib.Path(temporary))
        tree.rename(destination)
    release.write(stamp, {"id": component["id"], "sha256": component["sha256"]})
    return destination


def logged(arguments, cwd, environment, log):
    with log.open("w") as stream:
        result = subprocess.run([str(x) for x in arguments], cwd=cwd, env=environment, stdout=stream, stderr=stream)
    if result.returncode:
        raise ValueError("Command failed; inspect " + str(log) + "\n" + "\n".join(log.read_text().splitlines()[-25:]))


def check_module(directory, kind, cli, fpm):
    module = directory / (kind + ".so")
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(module)], check=True, capture_output=True)
    for binary in release.inspect_macho(directory):
        if any(not library.startswith(("/usr/lib/", "/System/")) for library in binary["libraries"]):
            raise ValueError("PHP modules must link only macOS system libraries")
    flag = "zend_extension=" if kind == "xdebug" else "extension="
    marker = "xdebug.mode =>" if kind == "xdebug" else "SPX Support => enabled"
    environment = dict(os.environ, XDEBUG_MODE="off", SPX_ENABLED="0")
    for binary in [cli, fpm]:
        result = subprocess.run([str(binary), "-n", "-d", flag + str(module), "-i"], env=environment, capture_output=True, text=True, timeout=30)
        if result.returncode or result.stderr or marker not in result.stdout:
            raise ValueError(kind + " failed to load in " + str(binary) + ": " + result.stderr[-1000:])


def manifest(directory, kind, minor, api, version, source, php_source=None):
    files = {p.relative_to(directory).as_posix(): release.sha(p) for p in directory.rglob("*") if p.is_file()}
    value = {"extension": kind, "extension_version": version, "php_minor": minor, "php_api": api,
             "architecture": "arm64", "minimum_macos": "14.0", "source_url": source["url"],
             "source_sha256": source["sha256"], "files": files}
    if php_source:
        value["php_source_sha256"] = php_source["sha256"]
    release.write(directory / "manifest.json", value)


def publish(staging, destination):
    previous = destination.with_name(destination.name + ".previous-" + uuid.uuid4().hex)
    if destination.exists():
        destination.rename(previous)
    try:
        staging.rename(destination)
    except BaseException:
        if previous.exists():
            previous.rename(destination)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def build(cli, fpm, args, lock, inventory):
    patch, api = runtime(cli)
    if runtime(fpm) != (patch, api):
        raise ValueError("CLI and FPM must have the same PHP patch and ABI")
    minor = ".".join(patch.split(".")[:2])
    product = next((p for p in lock["products"] if p["type"] == "xdebug" and p["payload"] == "php-extensions/xdebug/" + minor), None)
    sources = {s["id"]: s for s in lock["sources"]}
    if not product or "php-" + patch not in product["sources"]:
        raise ValueError("Pin the matching PHP SDK and Xdebug product in sources.lock.json before building PHP " + patch)
    php_source = sources["php-" + patch]
    xdebug_source = next(sources[name] for name in product["sources"] if name.startswith("xdebug-"))
    php_tree = source_tree(php_source, args.work)
    extension_tree = source_tree(xdebug_source, args.work)
    version_work = args.work / patch
    version_work.mkdir(parents=True, exist_ok=True)
    sdk = version_work / "sdk"
    php_build, extension_build = version_work / "php", version_work / "xdebug"
    # Keep generated headers and objects separate for every PHP minor.
    for source, destination in [(php_tree, php_build), (extension_tree, extension_build)]:
        if not destination.exists():
            shutil.copytree(source, destination)
    environment = dict(os.environ, MACOSX_DEPLOYMENT_TARGET="14.0", CFLAGS="-O2 -arch arm64 -mmacosx-version-min=14.0",
                       LDFLAGS="-arch arm64 -mmacosx-version-min=14.0", PKG_CONFIG_PATH="", PKG_CONFIG_LIBDIR=str(args.work / "no-pkgconfig"))
    for variable, command in [("PHP_AUTOCONF", "autoconf"), ("PHP_AUTOHEADER", "autoheader")]:
        environment[variable] = os.environ.get(variable) or shutil.which(command) or ""
        if not environment[variable]:
            raise ValueError(command + " must already be installed on the build machine")
    print("Preparing PHP " + patch + " SDK and Xdebug " + product["version"], flush=True)
    logged(["./configure", "--prefix=" + str(sdk), "--disable-all", "--enable-cli", "--enable-fpm", "--without-pear"], php_build, environment, version_work / "php-configure.log")
    logged(["make", "install-headers", "install-build"], php_build, environment, version_work / "php-headers.log")
    (sdk / "bin").mkdir(parents=True, exist_ok=True)
    for name in ["phpize", "php-config"]:
        shutil.copy2(php_build / "scripts" / name, sdk / "bin" / name)
        (sdk / "bin" / name).chmod(0o755)
    logged([sdk / "bin/phpize"], extension_build, environment, version_work / "xdebug-phpize.log")
    logged(["./configure", "--with-php-config=" + str(sdk / "bin/php-config"), "--enable-xdebug"], extension_build, environment, version_work / "xdebug-configure.log")
    logged(["make", "clean"], extension_build, environment, version_work / "xdebug-clean.log")
    logged(["make", "-j" + os.environ.get("YARD_BUILD_JOBS", "6")], extension_build, environment, version_work / "xdebug-make.log")
    destination = args.output / "xdebug" / minor
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staging = pathlib.Path(temporary) / "payload"
        staging.mkdir()
        shutil.copy2(extension_build / "modules/xdebug.so", staging / "xdebug.so")
        shutil.copy2(extension_build / "LICENSE", staging / "LICENSE")
        shutil.copy2(php_tree / "LICENSE", staging / "LICENSE.PHP")
        check_module(staging, "xdebug", cli, fpm)
        manifest(staging, "xdebug", minor, api, product["version"], xdebug_source, php_source)
        publish(staging, destination)
    spx_package = next(p for p in inventory["packages"] if p["id"] == "spx")
    spx_release = next(r for r in spx_package["releases"] if r["published"] and any(d["role"] == "php-" + minor for d in r["downloads"]))
    artifact = next(d for d in spx_release["downloads"] if d["role"] == "php-" + minor)
    cache = args.work / "spx-archives"
    cache.mkdir(exist_ok=True)
    archive = cache / ("spx-php-" + minor + ".zip")
    if not archive.exists():
        downloaded = release.download({"id": "spx-php-" + minor, **artifact}, args.work / "downloads")
        shutil.copy2(downloaded, archive)
    if release.sha(archive) != artifact["sha256"]:
        raise ValueError("SPX archive checksum mismatch for PHP " + minor)
    destination = args.output / "spx" / minor
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staging = pathlib.Path(temporary) / "payload"
        staging.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            for entry in zipped.infolist():
                path = pathlib.PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Unsafe path in SPX archive")
            zipped.extractall(staging)
        if not (staging / "LICENSE").is_file() or not (staging / "assets/web-ui/report.html").is_file():
            raise ValueError("SPX archive is missing its license or web UI")
        corresponding_source = sources["spx-" + spx_release["version"]]
        source_archive = release.download(corresponding_source, args.work / "downloads")
        shutil.copy2(source_archive, staging / "SOURCE.tar.gz")
        check_module(staging, "spx", cli, fpm)
        manifest(staging, "spx", minor, api, spx_release["version"], artifact)
        publish(staging, destination)
    print("Verified Xdebug and SPX in PHP " + patch + " CLI/FPM (API " + api + ")", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binaries", nargs="*", type=pathlib.Path)
    parser.add_argument("--all", dest="runtimes", type=pathlib.Path)
    parser.add_argument("--work", type=pathlib.Path, default=pathlib.Path(os.environ.get("YARD_EXTENSION_BUILD_ROOT", REPO / "vendor-build/php-extension-build")))
    parser.add_argument("--output", type=pathlib.Path, default=REPO / "vendor-build/out/php-extensions")
    args = parser.parse_args()
    try:
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise ValueError("Build on an Apple Silicon Mac with Xcode command line tools")
        args.work, args.output = args.work.resolve(), args.output.resolve()
        if " " in str(args.work):
            raise ValueError("Choose a build root without spaces; installed runtime paths may contain spaces")
        lock = release.read(HERE / "sources.lock.json")
        inventory = release.read(REPO / "Sources/YardKit/Resources/downloads/V1/manifest.json")
        if args.runtimes and not args.binaries:
            php = next(p for p in inventory["packages"] if p["id"] == "php")
            minors = sorted({".".join(r["version"].split(".")[:2]) for r in php["releases"] if r["published"]
                             and {"cli", "fpm"} <= {d["role"] for d in r["downloads"]}})
            pairs = [(args.runtimes / minor / "php", args.runtimes / minor / "php-fpm") for minor in minors]
        elif len(args.binaries) == 2 and not args.runtimes:
            pairs = [args.binaries]
        else:
            parser.error("Supply CLI and FPM paths, or --all with <minor>/php and <minor>/php-fpm")
        for cli, fpm in pairs:
            build(cli.resolve(), fpm.resolve(), args, lock, inventory)
        arguments = ["python3", str(HERE / "verify-php-extensions.py"), str(args.output)]
        if args.runtimes:
            arguments.append("--require-all")
        subprocess.run(arguments, check=True)
    except (OSError, ValueError, KeyError, StopIteration, release.ReleaseError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
