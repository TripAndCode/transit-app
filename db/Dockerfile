FROM postgis/postgis:14-3.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        postgresql-server-dev-14 \
        git \
    && git clone --branch v0.7.4 --depth 1 https://github.com/pgvector/pgvector.git \
    && cd pgvector && make && make install \
    && cd .. && rm -rf pgvector \
    && apt-get purge -y build-essential postgresql-server-dev-14 git \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
