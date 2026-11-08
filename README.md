# Yard service binaries

This repository distributes independent upstream service programs for macOS 14 / Apple Silicon.
Yard is a separate paid, proprietary application. Its source code is not included here.
Each upstream program and dependency retains its own license. The MIT license at the root
applies only to the enumerated build and packaging recipes, never to third-party programs or Yard.

## Current downloads

[Download the service packages](https://github.com/javisr/yard-binaries/releases/tag/vendor-2026-11-08-r1).
The release includes 15 binary packages, four required corresponding-source packages, checksums
and dependency audit/catalog metadata. Other managed downloads remain at their providers' URLs
in [V1/manifest.json](V1/manifest.json).

These initial binaries use ad hoc code signatures. They are not Developer ID signed or notarized.
Their arm64 and macOS 14 minimum-version metadata has been checked; execution on a real macOS 14
machine has not yet been verified.

## Release files

Every approved version has a `*-macos-arm64.tar.gz` binary, `SHA256SUMS`, and a catalog with
size, SHA-256, version and architecture. A `*-sources.tar.gz` is published only when the reviewed
licenses of the program or its included dependencies require corresponding source.
Each binary package includes `LICENSES/`, complete upstream license/notice files, source-header
notices, `SOURCE-PROVENANCE.json`, a build receipt, and a file/mode/link manifest.
The notice corpus also preserves notices for upstream features not enabled in these builds;
the reviewed component inventory explains which dependencies the recipe uses.

Required corresponding source is available next to the binary, at no additional charge. It contains
the original pinned upstream archives, included dependency sources, local recipes/patches, license
texts, and build instructions. Source archives are useful even when a product is blocked; they
do not authorize distributing that product's binary. For this inventory, source archives are required
for Redis 7.4.5, MariaDB 11.8.3/10.11.14 and MinIO. nginx, Redis 7.0, Valkey, PostgreSQL and Xdebug
retain all license/attribution texts in their binary packages without publishing optional source archives.
`sources.lock.json` explicitly records this policy for every product; included copyleft components
cannot silently be classified as not requiring sources.

## Rebuild on a maintainer's Mac

Use an Apple Silicon build Mac with Xcode Command Line Tools and Python 3.9 or newer.
Users of Yard do not perform these steps and do not need build tools or Homebrew.

Extract the source package into an empty directory. Put `upstream/*.tar.gz` in
`vendor-build/downloads/` (keep their filenames), then run from that directory:

```sh
bash scripts/vendor/build-all.sh mariadb
python3 scripts/vendor/release.py prepare --output dist/vendor-release
```

Select the service matching the source archive. PostgreSQL prepares its own static OpenSSL;
MariaDB uses bundled wolfSSL plus static GnuTLS, Nettle and GMP. `redis` builds Redis and Valkey.
The compiler and general-purpose build
tools are not distributed as runtime dependencies; the recipes acquire their pinned versions on
the build machine. `sources.lock.json` pins all service source archives by SHA-256.

For an isolated build set `YARD_VENDOR_BUILD_ROOT` to an empty absolute directory. Existing
unverified source trees are refused. A binary without a matching verified build receipt cannot
be released. Receipts bind the pinned source archives, recipes, binary bytes, permissions and links.

MinIO builds with Go 1.24.6, CGO disabled, and tag `kqueue`. Its source package additionally contains
the complete vendored module tree, `go.mod`, `go.sum`, the Go standard-library sources and license,
and a reviewed license inventory. Use `-mod=vendor` to rebuild without fetching module sources.
The upstream source tag and `go.sum` pin dependencies; the recipe rejects graph/license drift.
To reuse the accompanying module sources, extract the pinned upstream MinIO archive first,
copy `minio-dependencies/vendor` into its source root, and run `go build -mod=vendor` with the
flags in `minio.sh`. The full Go standard-library source used for the binary is included separately.

## Central inventory

`V1/manifest.json` lists Yard-managed downloads, including provider-hosted services, PHP, Node,
extensions and agent tools. It is not limited to the binaries built in this repository.
`V1/manifest.schema.json` documents the format. V2 will use a different directory; do not change
V1's schema in place. Increment `revision` for every content change, including source/hash changes.

Yard checks this file on launch when due and every 24 hours while open. It keeps a persistent
last-attempt timestamp, conditional HTTP ETag, and the last valid manifest for offline use.
Failures, incompatible schemas and older/reused revisions do not replace the usable catalog.
Checks report available versions; they never migrate database data or replace running services.

Prepared-but-unpublished binaries have `published: false`. After uploading the immutable assets,
generate the published inventory with a new revision and verify the remote files:

```sh
python3 scripts/vendor/download-manifest.py --release-dir dist/vendor-release \
  --revision 6 --output V1 --base-url https://github.com/javisr/yard-binaries/releases/download/vendor-2026-11-08-r1 --published
```

Provider files remain hosted by their original publishers. npm/Composer/Playwright resolve their
own transitive or project-specific packages using their existing integrity checks and lockfiles;
those dependencies are not rehosted or relicensed here. Agent tool installations continue to use
the versions approved by the Yard release, independently of this availability inventory.

## Rights and availability

Keep the licenses and notices when redistributing. GPL, LGPL, AGPL and SSPL source obligations
apply to the covered components. The original texts in `LICENSES/` define those obligations.
Redis 7.4 is distributed under the SSPLv1 option, with both upstream license texts preserved.
This is local software distribution; it does not offer a hosted Redis service. A future hosted
offering requires a separate review of SSPL section 13.

You may use, modify and redistribute covered third-party programs under their own licenses.
Restrictions in Yard's proprietary terms do not override those rights. These packages do not
grant rights to Yard's code or trademarks and do not imply endorsement by upstream projects.

## Publication

Only products reported as `ready` in `audit.json` may enter `catalog.json` and receive a binary
archive. Blocked products receive explicit reasons and source archives only if required by their
license policy, never releasable binaries. Optional source links/checksums are omitted from catalogs.
For a Developer ID signed/notarized release, complete signing and notarization before generating
its final package hashes. Ad hoc signatures establish local integrity, not publisher identity.
Changing signatures requires a new release and new checksums; never replace existing assets.
Publication and activation in Yard require reviewed, immutable download URLs and pinned hashes.
No publishing or repository creation happens when running these build/preparation scripts.
