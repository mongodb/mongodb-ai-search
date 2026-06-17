import type { AppConfig, Strategy } from "./types";

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

  if (s === "vector") {
    const stage: Record<string, unknown> = {
      index: atlas.vector_index || "vector_index",
      path: atlas.embedding_key || "embedding",
      queryVector: `<embedding(${query.length} chars) -> ${atlas.dimensions ?? "N"}-dim float[]>`,
      numCandidates: Math.max(retrieval.vector?.num_candidates ?? 200, topK),
      limit: topK,
    };
    if (Object.keys(filters).length) stage.filter = filters;
    return {
      collection: coll,
      pipeline: [
        { $vectorSearch: stage },
        { $set: { score: { $meta: "vectorSearchScore" } } },
        { $project: { [atlas.embedding_key || "embedding"]: 0 } },
      ],
    };
  }

  if (s === "fulltext") {
    const compound: Record<string, unknown> = {
      must: [{ text: { query, path: atlas.text_key || "text" } }],
    };
    if (Object.keys(filters).length) {
      compound.filter = Object.entries(filters).map(([k, v]) => ({
        equals: { path: k, value: v },
      }));
    }
    return {
      collection: coll,
      pipeline: [
        { $search: { index: atlas.search_index || "default", compound } },
        { $set: { score: { $meta: "searchScore" } } },
        { $limit: topK },
      ],
    };
  }

  if (s === "hybrid") {
    const vw = retrieval.hybrid?.vector_weight ?? 0.6;
    const fw = retrieval.hybrid?.fulltext_weight ?? 0.4;
    const vecStage: Record<string, unknown> = {
      index: atlas.vector_index || "vector_index",
      path: atlas.embedding_key || "embedding",
      queryVector: `<embedding -> ${atlas.dimensions ?? "N"}-dim>`,
      numCandidates: Math.max(retrieval.vector?.num_candidates ?? 200, topK * 10),
      limit: topK * 10,
    };
    if (Object.keys(filters).length) vecStage.filter = filters;
    return {
      collection: coll,
      strategy: "Reciprocal Rank Fusion (RRF)",
      weights: { vector: vw, fulltext: fw },
      vector_pipeline: [{ $vectorSearch: vecStage }],
      fulltext_pipeline: [
        { $search: { index: atlas.search_index || "default",
                     text: { query, path: atlas.text_key || "text" } } },
        { $limit: topK * 10 },
      ],
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
    const stage: Record<string, unknown> = {
      index: atlas.vector_index || "vector_index",
      path: atlas.embedding_key || "embedding",
      queryVector: `<embedding -> ${atlas.dimensions ?? "N"}-dim>`,
      numCandidates: topK * 10,
      limit: topK,
    };
    if (Object.keys(filters).length) stage.filter = filters;
    return {
      collection: coll,
      pipeline: [
        { $vectorSearch: stage },
        { $lookup: { from: coll, localField: "parent_id",
                     foreignField: "_id", as: "parent" } },
        { $replaceRoot: { newRoot: {
            $ifNull: [{ $arrayElemAt: ["$parent", 0] }, "$$ROOT"],
        } } },
        { $project: { [atlas.embedding_key || "embedding"]: 0 } },
      ],
    };
  }

  if (s === "auto") {
    return { note: "Run an `auto` search to see the resolved strategy + pipeline." };
  }
  return { note: `No pipeline preview available for: ${strategy}` };
}
