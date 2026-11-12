#!/usr/bin/env python3
"""Fail packaging on incomplete, unlicensed or corrupt optional PHP extension payloads."""
import argparse
import hashlib
import importlib.util
import json
import pathlib
import re


def verify(root, required=()):
    root = root.resolve()
    found = set()
    for kind in root.iterdir():
        if kind.name not in ("xdebug", "spx") or not kind.is_dir() or kind.is_symlink():
            raise ValueError("Unexpected entry in PHP extension payload: " + kind.name)
        for payload in kind.iterdir():
            if not payload.is_dir() or payload.is_symlink():
                raise ValueError("Unexpected PHP extension version payload")
            try:
                manifest = json.loads((payload / "manifest.json").read_text())
                files = manifest["files"]
                assert manifest["extension"] == kind.name
                assert manifest["php_minor"] == payload.name and re.fullmatch(r"\d+\.\d+", payload.name)
                assert re.fullmatch(r"\d{8}", manifest["php_api"]) and manifest["architecture"] == "arm64"
                assert kind.name + ".so" in files and "LICENSE" in files
                if kind.name == "spx":
                    assert "assets/web-ui/report.html" in files
                    assert "SOURCE.tar.gz" in files
                else:
                    assert "LICENSE.PHP" in files
                for file in payload.rglob("*"):
                    assert not file.is_symlink()
                    if file.is_file() and file != payload / "manifest.json":
                        assert file.relative_to(payload).as_posix() in files
                for relative, sha in files.items():
                    file = payload / relative
                    assert not pathlib.PurePosixPath(relative).is_absolute() and ".." not in pathlib.PurePosixPath(relative).parts
                    assert file.resolve().is_relative_to(payload)
                    assert re.fullmatch(r"[a-f0-9]{64}", sha)
                    assert hashlib.sha256(file.read_bytes()).hexdigest() == sha
                found.add((kind.name, payload.name))
            except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
                raise ValueError("Invalid PHP extension manifest/license/checksum: " + str(payload)) from error
    missing = {(kind, minor) for minor in required for kind in ("xdebug", "spx")} - found
    if missing:
        raise ValueError("Missing modules for downloadable PHP versions: " + ", ".join(kind + "/" + minor for kind, minor in sorted(missing)))
    if "8.0" in required:
        spec = importlib.util.spec_from_file_location("bundled_php_verifier", pathlib.Path(__file__).with_name("verify-php-runtimes.py"))
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        if "8.0" not in verifier.verify(root.parent / "php-runtimes"):
            raise ValueError("PHP 8.0 modules require its repaired bundled runtime")
    return found


def downloadable_minors():
    here = pathlib.Path(__file__).resolve().parent
    catalog = here.parent.parent / "Sources/YardKit/Resources/downloads/V1/manifest.json"
    if not catalog.exists():
        catalog = here.parent.parent / "V1/manifest.json"
    inventory = json.loads(catalog.read_text())
    php = next(p for p in inventory["packages"] if p["id"] == "php")
    return {".".join(r["version"].split(".")[:2]) for r in php["releases"] if r["published"]
            and {"cli", "fpm"} <= {d["role"] for d in r["downloads"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=pathlib.Path)
    parser.add_argument("--require-all", action="store_true", help="Require Xdebug and SPX for every published PHP minor")
    args = parser.parse_args()
    try:
        found = verify(args.root.resolve(), downloadable_minors() if args.require_all else ())
        print(str(len(found)) + " PHP extension payload manifests, licenses and checksums verified")
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
