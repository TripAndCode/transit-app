FROM postgis/postgis:14-3.2

# Pinned for reproducible image builds — bump deliberately, not on rebuild.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-14-pgvector=0.8.2-1.pgdg11+1 \
    && rm -rf /var/lib/apt/lists/*
