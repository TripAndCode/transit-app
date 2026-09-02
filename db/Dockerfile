# Multi-architecture Postgres for local dev, the throwaway test database, and
# any self-hosted deployment. The official `postgres` image publishes arm64
# alongside amd64, so an arm64 host runs this natively. `postgis/postgis`
# publishes amd64 only, for every tag, and cannot: it needs an emulated
# platform, which taxes every query.
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

# Positive control for the layer above: the build fails here rather than
# partway through `migrate up`. The check lives in its own script so it can be
# tested — a version floor buried in a `RUN` one-liner can be turned into a
# no-op by a later cleanup with nothing to catch it.
COPY assert_extensions.sh /usr/local/bin/assert-extensions
RUN /usr/local/bin/assert-extensions /usr/share/postgresql/14/extension \
        postgis=3.2 vector=0.8 pg_trgm
