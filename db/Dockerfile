FROM postgis/postgis:14-3.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-14-pgvector \
    && rm -rf /var/lib/apt/lists/*
