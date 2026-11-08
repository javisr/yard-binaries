#!/usr/bin/env python3
"""Prepare independent vendor releases. Never publishes or includes Yard app sources.

Python 3.9+, macOS build tools for binary inspection; no third-party Python packages.
"""

import argparse
import concurrent.futures
import csv
import gzip
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse


HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
LOCK = HERE / "sources.lock.json"
DEFAULT_WORK = REPO / "vendor-build"
LEGAL = re.compile(r"^(licen[cs]e(?:[._-].*)?|copying(?:[._-].*)?|copyright(?:[._-].*)?|notice(?:[._-].*)?|thirdparty(?:[._-].*)?|third[_-]party(?:[._-].*)?|authors|patents|edl-v10|epl-v20)$", re.I)
MAGICS = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}


class ReleaseError(Exception):
    pass


def sha(path):
    value = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read(path):
    return json.loads(pathlib.Path(path).read_text())


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run(arguments, **kwargs):
    return subprocess.run([str(x) for x in arguments], check=True, **kwargs)


def inventory(root):
    """Hash file bytes, executable permissions and confined links, including empty dirs."""
    root = pathlib.Path(root).resolve()
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if any(ord(character) < 32 for character in relative):
            raise ReleaseError("control character in package filename")
        if path.is_symlink():
            target = os.readlink(path)
            if pathlib.Path(target).is_absolute() or not path.resolve().is_relative_to(root):
                raise ReleaseError("unconfined link: " + relative)
            if not path.exists():
                raise ReleaseError("broken link: " + relative)
            result[relative] = {"symlink": target}
        elif path.is_file():
            result[relative] = {"sha256": sha(path), "mode": path.stat().st_mode & 0o777}
        elif path.is_dir():
            result[relative] = {"directory": True}
        else:
            raise ReleaseError("unsupported file: " + relative)
    return result


def download(component, cache):
    dest = cache / (component["id"] + ".tar.gz")
    cache.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        temporary = dest.with_suffix(".tmp-" + str(os.getpid()))
        try:
            run(["/usr/bin/curl", "--fail", "--location", "--silent", "--show-error",
                 "--proto", "=https", "--proto-redir", "=https", "--retry", "2",
                 "--max-time", "300", component["url"], "--output", temporary])
            if sha(temporary) != component["sha256"]:
                raise ReleaseError("source checksum mismatch: " + component["id"])
            temporary.replace(dest)
        finally:
            temporary.unlink(missing_ok=True)
    if sha(dest) != component["sha256"]:
        raise ReleaseError("cached source checksum mismatch: " + component["id"])
    return dest


def extract(archive, destination):
    """Extract a single-root upstream tar, rejecting escapes, devices and link ancestors."""
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        paths, roots, links = set(), set(), set()
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ReleaseError("unsafe source archive path: " + member.name)
            if member.name in paths:
                raise ReleaseError("duplicate source archive path: " + member.name)
            paths.add(member.name)
            roots.add(path.parts[0])
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ReleaseError("unsupported source archive entry: " + member.name)
            if member.issym() or member.islnk():
                target = pathlib.PurePosixPath(member.linkname)
                if target.is_absolute():
                    raise ReleaseError("absolute source link: " + member.name)
                resolved = pathlib.Path(os.path.normpath(str(path.parent / target) if member.issym() else str(target)))
                if not resolved.parts or resolved.parts[0] != path.parts[0] or ".." in resolved.parts:
                    raise ReleaseError("escaping source link: " + member.name)
                links.add(path)
        primary_roots = {pathlib.PurePosixPath(m.name).parts[0] for m in members
                         if m.isdir() or len(pathlib.PurePosixPath(m.name).parts) > 1}
        if len(roots) != 1:
            # PECL source releases carry package.xml next to the single source directory.
            if len(primary_roots) != 1 or not (roots - primary_roots) <= {"package.xml", "package2.xml"}:
                raise ReleaseError("source archive must have exactly one source root (plus optional PECL metadata)")
        for member in members:
            if any(parent in links for parent in pathlib.PurePosixPath(member.name).parents):
                raise ReleaseError("source archive writes through link: " + member.name)
        # All member types, paths and links have been checked above, including on Python 3.9.
        tar.extractall(destination)
    return destination / (next(iter(primary_roots)) if primary_roots else next(iter(roots)))


def recipes():
    return sorted([*HERE.glob("*.sh"), HERE / "release.py", HERE / "download-manifest.py",
                   HERE / "verify-php-extensions.py", LOCK, HERE / "minio-licenses.json", HERE / "upstream-downloads.json",
                   HERE / "LICENSE.build-recipes", HERE / "README.downloads.md"])


def recipe_hashes():
    return {p.name: sha(p) for p in recipes()}


def build_recipe_hashes(product):
    groups = {"nginx": ["nginx.sh"], "redis": ["redis.sh"], "valkey": ["redis.sh"],
              "postgresql": ["postgresql.sh", "openssl.sh"],
              "mariadb": ["mariadb.sh", "mariadb-tls.sh", "tools.sh"],
              "xdebug": ["php-extensions.sh", "verify-php-extensions.py"],
              "minio": ["minio.sh", "tools.sh", "minio-licenses.json"]}
    return {name: sha(HERE / name) for name in ["common.sh"] + groups[product["type"]]}


def inventory_hash(root):
    return hashlib.sha256(json.dumps(inventory(root), sort_keys=True).encode()).hexdigest()


def audit_go_command(args, lock):
    """Check the actual darwin/arm64 dependency graph against the reviewed license set."""
    tree = args.work / "src/minio-RELEASE.2025-09-07"
    reviewed = read(HERE / "minio-licenses.json")
    environment = dict(os.environ, CGO_ENABLED="0", GOOS="darwin", GOARCH="arm64",
                       GOFLAGS="-mod=vendor -tags=kqueue", GOTOOLCHAIN="local",
                       GOROOT=str(args.go.resolve().parent.parent))
    result = subprocess.run([str(args.auditor), "report", "--include_tests=false", "."],
                            cwd=tree, env=environment, capture_output=True, text=True)
    if result.returncode:
        raise ReleaseError("MinIO license audit failed: " + result.stderr[-3000:])
    detected = sorted((row[0], row[2]) for row in csv.reader(result.stdout.splitlines()))
    expected = sorted((row["package"], row["detected_license"]) for row in reviewed["packages"])
    if detected != expected:
        write(args.work / "minio-license-drift.json", {"expected": expected, "detected": detected})
        raise ReleaseError("MinIO dependency/license drift; review minio-license-drift.json")
    for row in reviewed["packages"]:
        evidence = tree / row["evidence"]
        if not evidence.is_file() or sha(evidence) != row["evidence_sha256"]:
            raise ReleaseError("MinIO license text changed: " + row["package"])
        for relative, digest in row.get("additional_evidence", {}).items():
            if sha(tree / relative) != digest:
                raise ReleaseError("MinIO additional license text changed: " + relative)
    if sha(tree / "go.mod") != reviewed["go_mod_sha256"] or sha(tree / "go.sum") != reviewed["go_sum_sha256"]:
        raise ReleaseError("MinIO module pins changed")
    goroot = pathlib.Path(subprocess.check_output([str(args.go), "env", "GOROOT"], text=True).strip())
    if (goroot / "VERSION").read_text().splitlines()[0] != "go1.24.6":
        raise ReleaseError("MinIO build requires the reviewed Go 1.24.6 runtime")
    write(args.work / "minio-go-audit.json", {
        "reviewed_inventory_sha256": sha(HERE / "minio-licenses.json"),
        "vendor_inventory_sha256": inventory_hash(tree / "vendor"),
        "go_source_inventory_sha256": inventory_hash(goroot / "src"),
        "go_license_sha256": sha(goroot / "LICENSE"),
        "go_patents_sha256": sha(goroot / "PATENTS"),
        "go_mod_sha256": sha(tree / "go.mod"), "go_sum_sha256": sha(tree / "go.sum"),
    })
    print(f"MinIO: {len(detected)} dependency license entries verified for darwin/arm64, CGO=0, kqueue")


def copy_recipes(destination, include_inventory=True):
    target = destination / "scripts/vendor"
    target.mkdir(parents=True, exist_ok=True)
    for path in recipes():
        shutil.copy2(path, target / path.name)
    shutil.copy2(HERE / "README.downloads.md", destination / "README.md")
    shutil.copy2(HERE / "LICENSE.build-recipes", destination / "LICENSE")
    source = REPO / "Sources/YardKit/Resources/downloads/V1"
    if not source.is_dir():
        source = REPO / "V1"
    if include_inventory and source.is_dir():
        shutil.copytree(source, destination / "V1", dirs_exist_ok=True)


def collect_notices(tree, destination):
    """Preserve legal files AND embedded source notices, not just root LICENSE files.

    The corpus deliberately includes upstream notices for disabled features. It is
    labelled as a source notice corpus, not claimed to be an exact linked-code SBOM.
    """
    count = 0
    headers = []
    for path in sorted(tree.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(tree)
        if LEGAL.fullmatch(path.name):
            dest = destination / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            count += 1
        elif path.suffix.lower() in {".c", ".h", ".cc", ".cpp", ".go", ".s", ".S", ".pl", ".py"}:
            text = path.read_bytes().decode("utf-8", errors="replace")
            comments = re.findall(r"/\*[\s\S]*?\*/|(?:(?://[^\n]*\n)+)|(?:(?:\#[^\n]*\n)+)", text)
            for comment in comments:
                if re.search(r"copyright|licen[cs]e|permission|redistribution|public domain", comment, re.I):
                    headers.append("\n===== " + relative.as_posix() + " =====\n" + comment)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "SOURCE-HEADER-NOTICES.txt").write_text("".join(headers))
    if count == 0:
        raise ReleaseError("no upstream license files found: " + tree.name)
    return count


def inspect_macho(root):
    """Inspect every executable and library without launching any service."""
    binaries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) not in MAGICS:
                continue
        architectures = subprocess.check_output(["/usr/bin/lipo", "-archs", str(path)], text=True).split()
        if "arm64" not in architectures:
            raise ReleaseError("missing arm64 slice: " + str(path))
        output = subprocess.check_output(["/usr/bin/otool", "-arch", "arm64", "-l", str(path)], text=True)
        minimum = None
        for command in re.split(r"Load command \d+", output):
            if re.search(r"\bcmd LC_BUILD_VERSION\b", command):
                if not re.search(r"\bplatform (?:1|MACOS)\b", command):
                    raise ReleaseError("non-macOS binary: " + str(path))
                match = re.search(r"\bminos (\S+)", command)
            elif "LC_VERSION_MIN_MACOSX" in command:
                match = re.search(r"\bversion (\S+)", command)
            else:
                continue
            if match:
                minimum = match[1]
                break
        if minimum is None or tuple((list(map(int, minimum.split("."))) + [0, 0])[:3]) > (14, 0, 0):
            raise ReleaseError("requires a newer macOS: " + str(path))
        links = subprocess.check_output(["/usr/bin/otool", "-L", str(path)], text=True).splitlines()[1:]
        libraries = [line.strip().split(" (", 1)[0] for line in links]
        for library in libraries:
            if not library.startswith(("/usr/lib/", "/System/", "@rpath/", "@loader_path/", "@executable_path/")):
                raise ReleaseError("external library: " + library)
        run(["/usr/bin/codesign", "--verify", "--strict", path], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        binaries.append({"path": path.relative_to(root).as_posix(), "minimum_macos": minimum,
                         "architectures": architectures, "libraries": libraries})
    if not binaries:
        raise ReleaseError("no Mach-O binaries in payload")
    return binaries


def archive_tree(root, destination):
    """Stable ordering/timestamps; no host usernames, extended attrs or absolute paths."""
    with destination.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as tar:
            for path in sorted(root.rglob("*")):
                info = tar.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                if info.isfile():
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)


def product_payload(product, work):
    return work / "out" / product["payload"]


def requires_source(product):
    """Use the reviewed policy for the whole binary, including linked dependencies."""
    required = product.get("source_required")
    if not isinstance(required, bool):
        raise ReleaseError("review source-distribution obligations for " + product["id"])
    licenses = [product["license"]] + [c["license"] for c in product["components"]]
    if not required and any(re.search(r"\b(?:AGPL|GPL|LGPL|SSPL|MPL|EPL|CDDL)-", license) for license in licenses):
        raise ReleaseError("source policy contradicts a declared component license: " + product["id"])
    return required


def source_command(args, lock):
    matches = [c for c in lock["sources"] if c["id"] == args.name and c["url"] == args.url]
    if len(matches) != 1:
        raise ReleaseError("source is not pinned in sources.lock.json: " + args.name)
    component = matches[0]
    archive = download(component, args.work / "downloads")
    stamp = args.destination / ".yard-source.json"
    if args.destination.exists():
        if not stamp.exists() or read(stamp) != {"id": args.name, "sha256": component["sha256"]}:
            raise ReleaseError("unverified/stale source directory; use a clean build root: " + str(args.destination))
        return
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.destination.parent) as tmp:
        extracted = extract(archive, pathlib.Path(tmp))
        extracted.rename(args.destination)
    write(stamp, {"id": args.name, "sha256": component["sha256"]})


def receipt_command(args, lock):
    product = next((p for p in lock["products"] if p["id"] == args.product), None)
    if product is None:
        raise ReleaseError("unknown product: " + args.product)
    payload = product_payload(product, args.work)
    if not payload.exists():
        raise ReleaseError("missing payload: " + str(payload))
    sources = {s["id"]: s for s in lock["sources"]}
    for name in product["sources"]:
        download(sources[name], args.work / "downloads")
        stamp = args.work / "src" / name / ".yard-source.json"
        if product["type"] == "xdebug":
            # The existing extension builder already emits pinned source + module hash provenance.
            extension = read(payload / "manifest.json")
            key = "php_source_sha256" if name.startswith("php-") else "source_sha256"
            if extension.get(key) != sources[name]["sha256"] or any(sha(payload / n) != h for n, h in extension["files"].items()):
                raise ReleaseError("Xdebug extension build manifest is inconsistent")
        elif not stamp.exists() or read(stamp).get("sha256") != sources[name]["sha256"]:
            raise ReleaseError("source tree has no verified build provenance: " + name)
    files = {payload.name: {"sha256": sha(payload), "mode": payload.stat().st_mode & 0o777}} if payload.is_file() else inventory(payload)
    receipt = {
        "schema": 1, "product": args.product, "sources": {n: sources[n]["sha256"] for n in product["sources"]},
        "recipes": build_recipe_hashes(product), "files": files, "build_system": platform.platform(),
    }
    receipt["recipe_files"] = {name: (HERE / name).read_text() for name in receipt["recipes"]}
    if product["type"] == "minio":
        receipt["go_audit"] = read(args.work / "minio-go-audit.json")
    write(args.work / "receipts" / (args.product + ".json"), receipt)


def prepare_command(args, lock):
    if args.base_url:
        url = urllib.parse.urlsplit(args.base_url)
        if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ReleaseError("base URL must be an HTTPS release directory without credentials, query or fragment")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ReleaseError("output must be empty; previous releases are never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    wanted = set(args.products or [p["id"] for p in lock["products"]])
    products = [p for p in lock["products"] if p["id"] in wanted]
    if wanted != {p["id"] for p in products}:
        raise ReleaseError("unknown products: " + ", ".join(wanted - {p["id"] for p in products}))
    sources = {s["id"]: s for s in lock["sources"]}
    required = sorted({n for p in products for n in p["sources"]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        archives = dict(pool.map(lambda n: (n, download(sources[n], args.work / "downloads")), required))
    export = output / "repository"
    copy_recipes(export)
    reports, catalog = [], {"schema": 1, "artifacts": []}
    for product in products:
        with tempfile.TemporaryDirectory(prefix="yard-vendor-release-") as temporary:
            scratch = pathlib.Path(temporary)
            trees = {name: extract(archives[name], scratch / name) for name in product["sources"]}
            report = prepare_product(product, args, output, scratch, trees, archives, sources)
            reports.append(report)
            if report["status"] == "ready":
                catalog["artifacts"].append(report["artifact"])
    write(output / "audit.json", {"schema": 1, "products": reports})
    write(output / "catalog.json", catalog)
    sums = []
    for path in sorted(output.glob("*.tar.gz")):
        sums.append(sha(path) + "  " + path.name)
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    (export / "AUDIT.json").write_text((output / "audit.json").read_text())
    ready = sum(r["status"] == "ready" for r in reports)
    print(f"Prepared {len(reports)} products: {ready} ready; {len(reports) - ready} blocked. See {output / 'audit.json'}")
    if ready != len(reports):
        raise ReleaseError("publication blocked for one or more products; source archives and audit were still prepared")


def prepare_product(product, args, output, scratch, trees, archives, sources):
    identity = product["id"]
    source_required = requires_source(product)
    source_root = scratch / (identity + "-sources")
    if source_required:
        # The public inventory is exported separately. Embedding it here creates stale source links.
        copy_recipes(source_root, include_inventory=False)
    notices = source_root / "LICENSES"
    for name in product["sources"]:
        if source_required:
            dest = source_root / "upstream" / archives[name].name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archives[name], dest)
        collect_notices(trees[name], notices / name)
    report = {"product": identity, "license": product["license"], "components": product["components"],
              "source_required": source_required,
              "sources": {n: {"url": sources[n]["url"], "sha256": sources[n]["sha256"]} for n in product["sources"]},
              "status": "blocked", "blockers": list(product.get("blockers", []))}
    # Original upstream archives plus these recipes describe every local patch.
    write(source_root / "SOURCE-PROVENANCE.json", {k: report[k] for k in ["product", "license", "components", "sources"]})
    payload = product_payload(product, args.work)
    receipt_path = args.work / "receipts" / (identity + ".json")
    if not receipt_path.exists():
        report["blockers"].append("No verified build receipt. Rebuild with the pinned recipes in a clean build root.")
    else:
        receipt = read(receipt_path)
        expected_sources = {n: sources[n]["sha256"] for n in product["sources"]}
        matches_recipes = True
        for name, digest in build_recipe_hashes(product).items():
            recorded = receipt.get("recipes", {}).get(name)
            snapshot = receipt.get("recipe_files", {}).get(name)
            if snapshot is not None and hashlib.sha256(snapshot.encode()).hexdigest() == recorded:
                # Preserve historical build recipes verbatim, even if packaging later changes.
                if source_required:
                    (source_root / "scripts/vendor" / name).write_text(snapshot)
            elif recorded != digest:
                matches_recipes = False
        if receipt.get("sources") != expected_sources or not matches_recipes:
            report["blockers"].append("Build receipt does not match the pinned sources and current recipes.")
        elif not payload.exists():
            report["blockers"].append("Build payload is missing.")
        else:
            actual = {payload.name: {"sha256": sha(payload), "mode": payload.stat().st_mode & 0o777}} if payload.is_file() else inventory(payload)
            if receipt.get("files") != actual:
                report["blockers"].append("Build payload changed after the build receipt was recorded.")
        if source_required:
            shutil.copy2(receipt_path, source_root / "BUILD-RECEIPT.json")
    if product["type"] == "minio":
        prepare_go_sources(product, args, source_root, report, receipt_path)
    source_name = None
    if source_required:
        source_name = identity + "-sources.tar.gz"
        archive_tree(source_root, output / source_name)
        report["source_archive"] = {"name": source_name, "sha256": sha(output / source_name)}
    if report["blockers"]:
        return report
    package = scratch / (identity + "-binary")
    package.mkdir()
    if payload.is_file():
        (package / "bin").mkdir()
        shutil.copy2(payload, package / "bin" / payload.name)
    else:
        shutil.copytree(payload, package, dirs_exist_ok=True, symlinks=True)
    shutil.copytree(notices, package / "LICENSES", dirs_exist_ok=True)
    # Build recipe bodies belong in required source archives, not in every binary download.
    write(package / "BUILD-RECEIPT.json", {k: v for k, v in receipt.items() if k != "recipe_files"})
    source_notice = ""
    if source_required:
        source_notice = f"Corresponding source, dependencies and rebuild recipes: {source_name}\n"
        if args.base_url:
            source_notice += f"Source download: {args.base_url.rstrip('/')}/{source_name}\n"
    (package / "THIRD_PARTY_NOTICES.md").write_text(
        f"# {identity}\n\nThis independent program is distributed under {product['license']}.\n"
        "Yard is a separate proprietary application. Its terms do not restrict rights granted by these upstream licenses.\n"
        "LICENSES preserves upstream legal files and source header notices, including notices for upstream features not compiled here.\n"
        + source_notice +
        "See SOURCE-PROVENANCE.json for the reviewed components. macOS system libraries are provided by macOS.\n")
    if product["type"] == "xdebug":
        with (package / "THIRD_PARTY_NOTICES.md").open("a") as stream:
            stream.write("\nThis product includes Xdebug, freely available from https://xdebug.org/.\n"
                         "This product includes PHP software, freely available from https://www.php.net/software/.\n")
    write(package / "SOURCE-PROVENANCE.json", report)
    try:
        binaries = inspect_macho(package)
    except (ReleaseError, subprocess.CalledProcessError) as error:
        report["blockers"].append("Binary verification failed: " + str(error))
        return report
    manifest = {"schema": 1, "product": identity, "files": inventory(package)}
    write(package / "payload-manifest.json", manifest)
    binary_name = identity + "-macos-arm64.tar.gz"
    archive_tree(package, output / binary_name)
    report["status"] = "ready"
    report["binaries"] = binaries
    report["artifact"] = {"type": product["type"], "version": product["version"], "revision": 1,
                          "architecture": "arm64", "minimum_macos": "14.0", "license": product["license"],
                          "filename": binary_name, "sha256": sha(output / binary_name),
                          "bytes": (output / binary_name).stat().st_size,
                          "source_required": source_required}
    if source_required:
        report["artifact"]["source_filename"] = source_name
        report["artifact"]["source_sha256"] = sha(output / source_name)
    if args.base_url:
        report["artifact"]["url"] = args.base_url.rstrip("/") + "/" + binary_name
        if source_required:
            report["artifact"]["source_url"] = args.base_url.rstrip("/") + "/" + source_name
    report["artifact"]["executables"] = {b["path"]: sha(package / b["path"]) for b in binaries}
    if (output / binary_name).stat().st_size >= 2 * 1024 ** 3 or (source_required and (output / source_name).stat().st_size >= 2 * 1024 ** 3):
        (output / binary_name).unlink()
        report["status"] = "blocked"
        report["blockers"].append("Archive exceeds GitHub Releases' per-file size limit.")
        report.pop("artifact", None)
    return report


def prepare_go_sources(product, args, source_root, report, receipt_path):
    tree = args.work / "src" / product["id"]
    goroot = args.work / "tools/go"
    reviewed = read(HERE / "minio-licenses.json")
    if not (tree / "vendor").is_dir() or not (goroot / "src").is_dir() or not receipt_path.exists():
        report["blockers"].append("MinIO needs its audited vendor tree and Go standard-library sources; rebuild with minio.sh.")
        return
    audit = read(receipt_path).get("go_audit", {})
    actual = {"reviewed_inventory_sha256": sha(HERE / "minio-licenses.json"),
              "vendor_inventory_sha256": inventory_hash(tree / "vendor"),
              "go_source_inventory_sha256": inventory_hash(goroot / "src"),
              "go_license_sha256": sha(goroot / "LICENSE"), "go_patents_sha256": sha(goroot / "PATENTS"),
              "go_mod_sha256": sha(tree / "go.mod"), "go_sum_sha256": sha(tree / "go.sum")}
    if actual != audit:
        report["blockers"].append("MinIO vendored sources, Go runtime or license audit changed after the build.")
        return
    shutil.copytree(tree / "vendor", source_root / "minio-dependencies/vendor", symlinks=True)
    for name in ["go.mod", "go.sum"]:
        shutil.copy2(tree / name, source_root / "minio-dependencies" / name)
    shutil.copytree(goroot / "src", source_root / "go-standard-library/src", symlinks=True)
    for name in ["LICENSE", "PATENTS", "VERSION"]:
        shutil.copy2(goroot / name, source_root / "go-standard-library" / name)
    collect_notices(tree / "vendor", source_root / "LICENSES/minio-go-dependencies")
    collect_notices(goroot / "src", source_root / "LICENSES/go-standard-library")
    for name in ["LICENSE", "PATENTS"]:
        shutil.copy2(goroot / name, source_root / "LICENSES/go-standard-library" / name)
    report["components"].extend(reviewed["packages"])
    report["components"].append({"name": "Go standard library/runtime 1.24.6", "license": "BSD-3-Clause AND LicenseRef-Go-Third-Party"})
    write(source_root / "MINIO-GO-LICENSES.json", reviewed)
    write(source_root / "SOURCE-PROVENANCE.json", {k: report[k] for k in ["product", "license", "components", "sources"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=pathlib.Path, default=pathlib.Path(os.environ.get("YARD_VENDOR_BUILD_ROOT", DEFAULT_WORK)))
    sub = parser.add_subparsers(dest="command", required=True)
    source = sub.add_parser("source")
    source.add_argument("name")
    source.add_argument("url")
    source.add_argument("destination", type=pathlib.Path)
    receipt = sub.add_parser("receipt")
    receipt.add_argument("product")
    go = sub.add_parser("audit-go")
    go.add_argument("--go", type=pathlib.Path, required=True)
    go.add_argument("--auditor", type=pathlib.Path, required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=pathlib.Path, required=True)
    prepare.add_argument("--products", nargs="+")
    prepare.add_argument("--base-url", help="Immutable HTTPS release directory; embedded with source-download instructions")
    args = parser.parse_args()
    args.work = args.work.resolve()
    try:
        lock = read(LOCK)
        {"source": source_command, "receipt": receipt_command, "prepare": prepare_command,
         "audit-go": audit_go_command}[args.command](args, lock)
    except (ReleaseError, OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
