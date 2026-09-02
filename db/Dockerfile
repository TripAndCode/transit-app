# Multi-architecture Postgres for local dev, the throwaway test database, and
# any self-hosted deployment. The official `postgres` image publishes arm64
# alongside amd64, so an Apple Silicon or arm64 Linux machine runs this
# natively instead of under emulation — `postgis/postgis` ships amd64 only,
# which forced a `platform: linux/amd64` pin and made every query pay a QEMU
# translation tax.
FROM postgres:14

# PostGIS and pgvector come from PGDG, which the official image already
# configures. Versions are pinned so an image rebuild is reproducible; bump
# them deliberately. A pin that disappears from the apt pool is ordinary
# maintenance, not a broken build — PGDG prunes superseded patch releases.
#
# Installing at image-build time is what keeps this safe: the server is not
# running yet, so a package that wants to upgrade `postgresql-14` cannot
# ABI-mismatch an already-compiled extension against a live backend. The same
# apt install against a running container is the case that breaks.
ARG POSTGIS_VERSION=3.6.4+dfsg-2.pgdg12+1
ARG PGVECTOR_VERSION=0.8.6-1.pgdg12+1
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        "postgresql-14-postgis-3=${POSTGIS_VERSION}" \
        "postgresql-14-pgvector=${PGVECTOR_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
