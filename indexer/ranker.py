"""
xiniubot 排序器 (BM25 + 专业增强)
=================================
基础: BM25
   score(Q, D) = Σ IDF(qi) * (f(qi,D) * (k1+1)) / (f(qi,D) + k1*(1-b+b*|D|/avgdl))
   IDF(qi) = ln((N - n(qi) + 0.5) / (n(qi) + 0.5) + 1)

专业对齐增强:
  1. 字段加权: 词项命中标题/描述时按 RANKING.title_weight / description_weight 放大
  2. 链接权威分: 按 RANKING.authority_weight 与 PageRank 权威分融合
  3. 时间衰减: 按 RANKING.time_decay_days 对旧内容降权 (0 = 关闭)
"""

import math
import time

import config
from indexer.inverted_index import InvertedIndex


def _field_boost(doc, term: str) -> float:
    """字段加权: 命中标题/描述放大, 正文基准为 1."""
    r = config.RANKING
    boost = 1.0
    if doc is None:
        return boost
    title_tokens = getattr(doc, "title_tokens", None)
    desc_tokens = getattr(doc, "desc_tokens", None)
    if title_tokens and term in title_tokens:
        boost *= r.get("title_weight", 1.0)
    if desc_tokens and term in desc_tokens:
        boost *= r.get("description_weight", 1.0)
    return boost


def _finalize(index: InvertedIndex, scores: dict[int, float], top_k: int):
    """应用权威分融合与时间衰减后排序."""
    r = config.RANKING
    authority_w = r.get("authority_weight", 0.0)
    decay_days = r.get("time_decay_days", 0)

    final: dict[int, float] = {}
    for doc_id, s in scores.items():
        doc = index.get_document(doc_id)
        factor = 1.0
        if doc is not None:
            if authority_w > 0:
                factor *= (1.0 + authority_w * float(getattr(doc, "authority", 0.0)))
            if decay_days > 0 and getattr(doc, "fetch_time", 0):
                age_days = (time.time() - doc.fetch_time) / 86400.0
                factor *= max(0.5, 1.0 / (1.0 + age_days / decay_days))
        final[doc_id] = s * factor

    ranked = sorted(final.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


def bm25_score(
    index: InvertedIndex,
    query_terms: list[str],
    top_k: int = 100,
    candidate_docs: set[int] | None = None,
) -> list[tuple[int, float]]:
    """对查询词列表计算 BM25 分数, 返回 top_k 个 (doc_id, score)."""
    k1 = config.BM25_K1
    b = config.BM25_B
    N = index.total_docs
    if N == 0:
        return []

    avg_dl = index.avg_doc_length or 1.0

    scores: dict[int, float] = {}
    for term in query_terms:
        postings = index.get_postings(term)
        n_t = len(postings)
        if n_t == 0:
            continue
        idf = math.log((N - n_t + 0.5) / (n_t + 0.5) + 1.0)

        for p in postings:
            if candidate_docs is not None and p.doc_id not in candidate_docs:
                continue
            dl = index.get_doc_length(p.doc_id) or avg_dl
            tf = p.term_freq
            tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))
            base = idf * tf_norm
            doc = index.get_document(p.doc_id)
            scores[p.doc_id] = scores.get(p.doc_id, 0.0) + base * _field_boost(doc, term)

    return _finalize(index, scores, top_k)


def bm25_phrase_score(
    index: InvertedIndex,
    query_terms: list[str],
    top_k: int = 100,
    candidate_docs: set[int] | None = None,
) -> list[tuple[int, float]]:
    """短语查询的 BM25 评分: 先做短语匹配, 再用 BM25 打分."""
    phrase_docs = set(index.phrase_search(query_terms))
    if not phrase_docs:
        return []
    if candidate_docs is not None:
        phrase_docs &= candidate_docs
    if not phrase_docs:
        return []

    return bm25_score(index, query_terms, top_k=top_k, candidate_docs=phrase_docs)
