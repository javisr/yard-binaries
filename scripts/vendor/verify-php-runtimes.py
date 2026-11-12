#!/usr/bin/env python3
"""Verify bundled PHP runtime bytes, notices, architecture and deployment target."""
import argparse
import importlib.util
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("runtime_release", HERE / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def verify(root):
    root = root.resolve()
    found = set()
    for payload in root.iterdir():
        if payload.is_symlink() or not payload.is_dir():
            raise ValueError("Unexpected bundled PHP runtime entry")
        value = json.loads((payload / "manifest.json").read_text())
        if (value.get("schema") != 1 or value.get("phpMinor") != payload.name
                or not re.fullmatch(r"\d+\.\d+", payload.name) or value.get("architecture") != "arm64"
                or value.get("version", "").split("-")[0] != value.get("phpVersion")):
            raise ValueError("Invalid bundled PHP runtime identity: " + str(payload))
        files = value["files"]
        if not {"php", "php-fpm", "LICENSE.PHP", "SOURCE-PROVENANCE.json"} <= files.keys():
            raise ValueError("Bundled PHP runtime is missing binaries, license or provenance")
        for path in payload.rglob("*"):
            if path.is_symlink() or (path.is_file() and path != payload / "manifest.json" and path.relative_to(payload).as_posix() not in files):
                raise ValueError("Unverified entry in bundled PHP runtime: " + str(path))
        for relative, checksum in files.items():
            path = payload / relative
            if (pathlib.PurePosixPath(relative).is_absolute() or ".." in pathlib.PurePosixPath(relative).parts
                    or not path.resolve().is_relative_to(payload) or not re.fullmatch(r"[a-f0-9]{64}", checksum)
                    or release.sha(path) != checksum):
                raise ValueError("Bundled PHP runtime checksum mismatch: " + str(path))
        for binary in release.inspect_macho(payload):
            if any(not link.startswith(("/usr/lib/", "/System/")) for link in binary["libraries"]):
                raise ValueError("Bundled PHP runtime is not self-contained")
        found.add(payload.name)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=pathlib.Path)
    args = parser.parse_args()
    try:
        print("Verified bundled PHP runtimes: " + ", ".join(sorted(verify(args.root))))
    except (OSError, ValueError, KeyError, release.ReleaseError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
