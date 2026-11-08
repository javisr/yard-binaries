#!/bin/bash
# Writes vendor-build/out/manifest.json: SHA-256 of every bundled file, grouped per service version.
# Yard verifies these checksums before installing a bundled service on the user's Mac.
source "$(dirname "$0")/common.sh"

cd "$OUT"
python3 - "$OUT" <<'PY'
import hashlib, json, os, sys
out = sys.argv[1]

def files(root):
    result = {}
    for dirpath, _, names in os.walk(root):
        for name in sorted(names):
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            rel = os.path.relpath(path, root)
            with open(path, "rb") as f:
                result[rel] = hashlib.sha256(f.read()).hexdigest()
    return result

manifest = {"binaries": {}, "services": {}}
bin_dir = os.path.join(out, "bin")
if os.path.isdir(bin_dir):
    manifest["binaries"] = files(bin_dir)
services = os.path.join(out, "services")
if os.path.isdir(services):
    for service in sorted(os.listdir(services)):
        sdir = os.path.join(services, service)
        if not os.path.isdir(sdir):
            continue
        for version in sorted(os.listdir(sdir)):
            vdir = os.path.join(sdir, version)
            if os.path.isdir(vdir):
                manifest["services"].setdefault(service, {})[version] = files(vdir)
with open(os.path.join(out, "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2, sort_keys=True)
print("manifest:", sum(len(v) for s in manifest["services"].values() for v in s.values()) + len(manifest["binaries"]), "files")
PY
du -sh "$OUT/bin" "$OUT"/services/* 2>/dev/null || true
