import type { AppConfig, Strategy } from "./types";

/** Build the $vectorSearch stage, accounting for AutoEmbeddings vs client-side.
 *
 *  AutoEmbeddings (embeddings.provider "auto"): Atlas embeds the query text
 *  server-side, so the stage `path` is the text field and the query is passed
 *  as `{ query: { text } }` with a `model` — there is NO client `queryVector`.
 *  Client-side embeddings: the client sends a `queryVector` on `embedding_key`. */
function vectorSearchStage(
  cfg: AppConfig,
  query: string,
  numCandidates: number,
  limit: number,
  filters: Record<string, unknown>,
): Record<string, unknown> {
  const atlas = cfg.atlas;
  const isAuto =
    cfg.embeddings?.provider === "auto" ||
    !atlas.embedding_key ||
    atlas.dimensions === -1;

  const stage: Record<string, unknown> = {
    index: atlas.vector_index || "vector_index",
  };
  if (isAuto) {
    stage.path = atlas.text_key || "text";                 // autoEmbed path
    stage.query = { text: query };                          // Atlas embeds server-side
    stage.numCandidates = numCandidates;
    stage.limit = limit;
    stage.model = String(cfg.embeddings?.config?.model ?? "<auto-embed model>");
  } else {
    stage.path = atlas.embedding_key || "embedding";
    stage.queryVector = `<embedding(${query.length} chars) -> ${atlas.dimensions ?? "N"}-dim float[]>`;
    stage.numCandidates = numCandidates;
    stage.limit = limit;
  }
  if (Object.keys(filters).length) stage.filter = filters;
  return stage;
}

/** Reconstruct an Atlas aggregation pipeline for the chosen strategy. */
export function buildPipeline(
  strategy: Strategy | string,
  cfg: AppConfig,
  query: string,
  topK: number,
  filters: Record<string, unknown>,
): unknown {
  const atlas = cfg.atlas;
  const retrieval = cfg.retrieval;
  const coll = atlas.collection || "<collection>";
  const s = String(strategy).replace(/-/g, "_");
  const isAuto =
    cfg.embeddings?.provider === "auto" ||
    !atlas.embedding_key ||
    atlas.dimensions === -1;
  // Only client-side pipelines project the stored embedding out of results.
  const stripEmbedding = isAuto ? {} : { [atlas.embedding_key || "embedding"]: 0 };

  if (s === "vector") {
    const stage = vectorSearchStage(
      cfg, query, Math.max(retrieval.vector?.num_candidates ?? 200, topK), topK, filters,
    );
    const pipeline: unknown[] = [
      { $vectorSearch: stage },
      { $set: { score: { $meta: "vectorSearchScore" } } },
    ];
    if (!isAuto) pipeline.push({ $project: stripEmbedding });
    return { collection: coll, pipeline };
  }

  if (s === "fulltext") {
    // The backend applies metadata filters as a plain $match after $search
    // (MQL, same shape as $vectorSearch.filter) — not a Lucene compound filter.
    const pipeline: unknown[] = [
      { $search: { index: atlas.search_index || "default",
                   text: { query, path: atlas.text_key || "text" } } },
    ];
    if (Object.keys(filters).length) pipeline.push({ $match: filters });
    pipeline.push({ $set: { score: { $meta: "searchScore" } } }, { $limit: topK });
    return { collection: coll, pipeline };
  }

  if (s === "hybrid") {
    const vw = retrieval.hybrid?.vector_weight ?? 0.6;
    const fw = retrieval.hybrid?.fulltext_weight ?? 0.4;
    const numCands = Math.max(retrieval.vector?.num_candidates ?? 200, topK * 10);
    const vecStage = vectorSearchStage(cfg, query, numCands, topK * 10, filters);
    // The full-text channel applies the SAME metadata filter as a $match (MQL).
    const ftPipeline: unknown[] = [
      { $search: { index: atlas.search_index || "default",
                   text: { query, path: atlas.text_key || "text" } } },
    ];
    if (Object.keys(filters).length) ftPipeline.push({ $match: filters });
    ftPipeline.push({ $limit: topK * 10 });
    return {
      collection: coll,
      strategy: "Reciprocal Rank Fusion (RRF)",
      weights: { vector: vw, fulltext: fw },
      vector_pipeline: [{ $vectorSearch: vecStage }],
      fulltext_pipeline: ftPipeline,
      fusion: "score = vector_weight / (60 + rank_vector) + fulltext_weight / (60 + rank_fulltext)",
    };
  }

  if (s === "graph") {
    return {
      collection: coll,
      pipeline: [
        { $match: { [atlas.text_key || "text"]: { $regex: query, $options: "i" } } },
        { $limit: Math.max(topK, 10) },
        { $graphLookup: {
            from: coll,
            startWith: "$entities",
            connectFromField: "entities",
            connectToField: "entities",
            as: "connected",
            maxDepth: 1,
        } },
        { $limit: topK },
      ],
    };
  }

  if (s === "parent_doc") {
    const stage = vectorSearchStage(cfg, query, topK * 10, topK, filters);
    const pipeline: unknown[] = [
      { $vectorSearch: stage },
      { $lookup: { from: coll, localField: "parent_id",
                   foreignField: "_id", as: "parent" } },
      { $replaceRoot: { newRoot: {
          $ifNull: [{ $arrayElemAt: ["$parent", 0] }, "$$ROOT"],
      } } },
    ];
    if (!isAuto) pipeline.push({ $project: stripEmbedding });
    return { collection: coll, pipeline };
  }

  if (s === "auto") {
    return { note: "Run an `auto` search to see the resolved strategy + pipeline." };
  }
  return { note: `No pipeline preview available for: ${strategy}` };
}
