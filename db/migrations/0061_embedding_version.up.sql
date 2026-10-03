-- Which embedder produced each stored vector: "<model id>@<sentence-transformers
-- version>". Vectors from two different embedders are not comparable, so a
-- model or library upgrade silently degrades nearest-neighbour routing to
-- nonsense distances unless the index is rebuilt. Readers filter on this
-- column and warn when rows stamped with another version survive.
--
-- NULL means "written before stamping existed", which readers treat as
-- compatible: the alternative is blanking a working index on deploy.
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS embedding_version TEXT;

-- The cache itself holds no vector; this records the version its question was
-- promoted into rag_chunks under, so a re-index knows which promotions are
-- stale.
ALTER TABLE ask_intent_cache ADD COLUMN IF NOT EXISTS embedding_version TEXT;
