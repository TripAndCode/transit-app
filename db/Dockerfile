# Multi-architecture Postgres for local dev, the throwaway test database, and
# any self-hosted deployment. The official `postgres` image publishes arm64
# alongside amd64, so an Apple Silicon or arm64 Linux machine runs this
# natively instead of under emulation — `postgis/postgis` ships amd64 only,
# which forced a `platform: linux/amd64` pin and made every query pay a QEMU
# translation tax.
FROM postgres:14

# PostGIS and pgvector come from PGDG, which the official image already
# configures.
#
# These are deliberately not pinned to exact apt versions. A PGDG version
# string carries the Debian release it was built for (`…pgdg13+1`), and this
# base is a moving tag: when it advances to a new Debian release, every exact
# pin stops resolving and the build fails on one architecture before the
# other, depending on which layers are cached. Pinning also breaks on its own
# whenever PGDG prunes a superseded patch release from the pool. The minimum
# versions asserted below are what the schema actually depends on, so they are
# what this image guarantees.
#
# Installing at image-build time is what keeps this safe: the server is not
# running yet, so a package that wants to upgrade `postgresql-14` cannot
# ABI-mismatch an already-compiled extension against a live backend. The same
# apt install against a running container is the case that breaks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-14-postgis-3 \
        postgresql-14-pgvector \
    && rm -rf /var/lib/apt/lists/*

# Positive control for the layer above. Every extension the migrations create
# must be present before this image is considered built: `postgis` and
# `vector` come from the packages above, `pg_trgm` from the base image's
# contrib modules. Without this an absent extension surfaces much later, as a
# confusing failure partway through `migrate up`.
#
# Only the two apt-installed extensions carry a version floor, because only
# they can move independently of this file; `pg_trgm` ships with the base
# image at whatever version its Postgres provides, so presence is the whole
# check there.
#
# This reads packaging metadata, so it proves an extension is installable, not
# that it loads — a control file beside an unusable shared object would still
# pass. Actually creating each extension needs a running server, which the
# first `migrate up` against this image does; keep that a step away rather
# than starting a throwaway server inside the build.
RUN set -eu; \
    ext=/usr/share/postgresql/14/extension; \
    for control in postgis vector pg_trgm; do \
        test -f "$ext/$control.control" || { echo "missing extension: $control" >&2; exit 1; }; \
    done; \
    version_of() { sed -n "s/^default_version *= *'\(.*\)'/\1/p" "$ext/$1.control"; }; \
    require() { \
        installed=$(version_of "$1"); \
        echo "$1 $installed"; \
        dpkg --compare-versions "$installed" ge "$2" \
            || { echo "$1 $installed is below the required $2" >&2; exit 1; }; \
    }; \
    require postgis 3.2; \
    require vector 0.8
