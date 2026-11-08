#!/usr/bin/env python3
"""Fail packaging on incomplete, unlicensed or corrupt optional PHP extension payloads."""
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
for kind in root.iterdir():
    if not kind.is_dir() or kind.is_symlink():
        raise SystemExit("Unexpected entry in PHP extension payload: " + kind.name)
    for payload in kind.iterdir():
        if not payload.is_dir() or payload.is_symlink():
            raise SystemExit("Unexpected PHP extension version payload")
        try:
            manifest = json.loads((payload / "manifest.json").read_text())
            files = manifest["files"]
            assert manifest["extension"] == kind.name
            assert manifest["php_minor"] == payload.name == "8.4"
            assert manifest["php_api"] == "20240924" and manifest["architecture"] == "arm64"
            assert kind.name + ".so" in files and "LICENSE" in files
            if kind.name == "spx":
                assert "assets/web-ui/report.html" in files
            for file in payload.rglob("*"):
                assert not file.is_symlink()
                if file.is_file() and file.name != "manifest.json":
                    assert file.relative_to(payload).as_posix() in files
            for relative, sha in files.items():
                file = payload / relative
                assert not pathlib.PurePosixPath(relative).is_absolute() and ".." not in pathlib.PurePosixPath(relative).parts
                assert file.resolve().is_relative_to(payload)
                assert hashlib.sha256(file.read_bytes()).hexdigest() == sha
        except (AssertionError, KeyError, OSError, ValueError) as error:
            raise SystemExit("Invalid PHP extension manifest/license/checksum: " + str(payload)) from error
print("PHP extension payload manifests, licenses and checksums verified")
