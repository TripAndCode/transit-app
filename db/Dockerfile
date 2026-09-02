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
# must be installable before this image is considered built: `postgis` and
# `vector` come from the packages above, `pg_trgm` from the base image's
# contrib modules. Without this an absent extension surfaces much later, as a
# confusing failure partway through `migrate up`.
RUN set -eu; \
    ext=/usr/share/postgresql/14/extension; \
    for control in postgis vector pg_trgm; do \
        test -f "$ext/$control.control" || { echo "missing extension: $control" >&2; exit 1; }; \
    done; \
    postgis_version=$(sed -n "s/^default_version *= *'\(.*\)'/\1/p" "$ext/postgis.control"); \
    vector_version=$(sed -n "s/^default_version *= *'\(.*\)'/\1/p" "$ext/vector.control"); \
    echo "postgis $postgis_version, vector $vector_version"; \
    printf '%s\n3.2\n' "$postgis_version" | sort -C -V \
        && { echo "postgis $postgis_version is older than the required 3.2" >&2; exit 1; } || true; \
    printf '%s\n0.8\n' "$vector_version" | sort -C -V \
        && { echo "vector $vector_version is older than the required 0.8" >&2; exit 1; } || true
