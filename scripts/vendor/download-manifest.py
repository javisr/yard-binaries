#!/usr/bin/env python3
"""Generate the public V1 inventory from reviewed provider links and prepared vendor artifacts.

This writes metadata only. --published requires the publisher to have uploaded immutable assets.
It never creates a repository, uploads a release, runs installers, or modifies user data.
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
RESOURCE = HERE.parent.parent / "V1"
if not RESOURCE.is_dir():
    RESOURCE = HERE.parent.parent / "Sources/YardKit/Resources/downloads/V1"


def validate(manifest):
    if manifest.get("schema") != 1 or not isinstance(manifest.get("revision"), int) or manifest["revision"] < 1:
        raise ValueError("invalid manifest schema or revision")
    seen = set()
    for package in manifest["packages"]:
        if package["id"] in seen:
            raise ValueError("duplicate package: " + package["id"])
        seen.add(package["id"])
        versions = set()
        for release in package["releases"]:
            if release["version"] in versions or not release["downloads"]:
                raise ValueError("duplicate or empty release: " + package["id"])
            versions.add(release["version"])
            for download in release["downloads"]:
                if not download["url"].startswith("https://"):
                    raise ValueError("download URLs must use HTTPS")


def generate(args):
    packages = json.loads((HERE / "upstream-downloads.json").read_text())["packages"]
    products = json.loads((HERE / "sources.lock.json").read_text())["products"]
    prepared = {}
    if args.release_dir:
        prepared = {p["product"]: p for p in json.loads((args.release_dir / "audit.json").read_text())["products"]}
    by_id = {package["id"]: package for package in packages}
    extension_releases = {}
    for product in products:
        package = by_id.setdefault(product["type"], {"id": product["type"], "name": product["type"], "category": "service", "releases": []})
        identity = product["id"]
        report = prepared.get(identity, {})
        artifact = report.get("artifact", {})
        source_required = product.get("source_required")
        if not isinstance(source_required, bool):
            raise ValueError("Review source-distribution policy for " + identity)
        published = args.published and report.get("status") == "ready"
        binary = artifact.get("filename", identity + "-macos-arm64.tar.gz")
        source = artifact.get("source_filename", identity + "-sources.tar.gz")
        release = {"version": product["version"], "published": bool(published), "minimumMacOS": "14.0",
                   "license": product["license"],
                   "downloads": [{"role": "server", "url": args.base_url.rstrip("/") + "/" + binary,
                                  "format": "tar-gz", "architecture": "arm64", "stripComponents": 0}]}
        if source_required:
            release["sourceURL"] = args.base_url.rstrip("/") + "/" + source
        if product["type"] == "xdebug":
            package["category"] = "extension"
            package["name"] = "Xdebug"
            release["downloads"][0]["role"] = "php-" + pathlib.PurePosixPath(product["payload"]).name
        if artifact.get("sha256"):
            release["downloads"][0]["sha256"] = artifact["sha256"]
        if published:
            # Confirm availability before advertising a prepared build as downloadable.
            checks = [(release["downloads"][0]["url"], artifact["sha256"])]
            if source_required:
                if not artifact.get("source_filename") or not artifact.get("source_sha256"):
                    raise ValueError("Missing required corresponding source for " + identity)
                checks.append((release["sourceURL"], artifact["source_sha256"]))
            for url, expected in checks:
                request = urllib.request.Request(url, headers={"User-Agent": "Yard release preparation"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.status != 200:
                        raise ValueError("published asset is unavailable: " + url)
                    checksum = hashlib.sha256()
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        checksum.update(chunk)
                    if checksum.hexdigest() != expected:
                        raise ValueError("published asset does not match the prepared bytes: " + url)
        if product["type"] == "xdebug":
            key = (product["type"], release["version"])
            if key in extension_releases:
                combined = extension_releases[key]
                combined["downloads"] += release["downloads"]
                combined["published"] = combined["published"] and release["published"]
                release = combined
            else:
                extension_releases[key] = release
        package["releases"] = [r for r in package["releases"] if r["version"] != release["version"]] + [release]
    manifest = {"schema": 1, "revision": args.revision,
                "generatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "packages": sorted(by_id.values(), key=lambda p: p["id"])}
    validate(manifest)
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / "manifest.json"
    if output.exists():
        previous = json.loads(output.read_text())
        content = lambda value: {key: v for key, v in value.items() if key != "generatedAt"}
        if previous.get("revision", 0) > args.revision or (previous.get("revision") == args.revision and content(previous) != content(manifest)):
            raise ValueError("Manifest contents changed; choose a revision higher than " + str(previous["revision"]))
        if content(previous) == content(manifest):
            manifest = previous  # A no-op does not create a different body under the same revision.
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    schema = RESOURCE / "manifest.schema.json"
    if schema.exists() and schema.parent.resolve() != args.output.resolve():
        (args.output / schema.name).write_bytes(schema.read_bytes())
    print(f"V1 manifest: {len(manifest['packages'])} packages, revision {args.revision}; {args.output / 'manifest.json'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path, default=RESOURCE)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--base-url", default="https://github.com/javisr/yard-binaries/releases/download/vendor-2026-11-12-r1")
    parser.add_argument("--published", action="store_true", help="Verify already published assets before marking them available")
    parser.add_argument("--validate-installable", action="store_true", help="Require all compiled optional service versions to have published verified downloads")
    args = parser.parse_args()
    try:
        if args.validate_installable:
            manifest = json.loads((RESOURCE / "manifest.json").read_text())
            validate(manifest)
            by_id = {p["id"]: p for p in manifest["packages"]}
            lock = json.loads((HERE / "sources.lock.json").read_text())
            for product in lock["products"]:
                if not product["payload"].startswith("services/"):
                    continue  # Privileged nginx still follows the existing bundled helper/update contract.
                matches = [r for r in by_id.get(product["type"], {}).get("releases", []) if r["version"] == product["version"]]
                if not matches or not matches[0]["published"] or not any(d.get("sha256") and d.get("format") == "tar-gz" for d in matches[0]["downloads"]):
                    raise ValueError("Publish a verified download before omitting bundled service: " + product["id"])
            print("All optional bundled services have published, verified downloads")
        else:
            generate(args)
    except (OSError, ValueError, urllib.error.URLError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
