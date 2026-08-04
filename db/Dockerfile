FROM postgis/postgis:14-3.2

# Pinned for reproducible image builds — bump deliberately, not on rebuild.
# 0.8.2-1.pgdg11+1 was pruned from the PGDG apt pool (only 0.8.3+ remain as
# of 2026-07-27); bumped to the latest available. Safe here specifically
# because this is a single fresh image build, not an apt install against an
# already-running server — there's no existing compiled postgresql-14
# binary for a newer pgvector's `Depends: postgresql-14` to upgrade past
# and ABI-mismatch (the problem CI's docker-exec-into-a-live-container
# approach has to work around by building pgvector from source instead).
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-14-pgvector=0.8.5-1.pgdg11+1 \
    && rm -rf /var/lib/apt/lists/*
