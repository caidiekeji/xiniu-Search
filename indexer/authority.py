"""
xiniubot 链接权威分 (简化 PageRank)
===================================
在已索引文档的"出链-入链"关系上迭代计算权威分:
  - 只有链接到"已索引文档"的边才参与计算 (抓取范围受限时的合理近似)
  - 支持阻尼系数与悬挂节点处理
  - 结果归一化到 [0, 1] 并写回 Document.authority

排序时通过 RANKING.authority_weight 与 BM25 融合.
"""

import logging
from collections import defaultdict

logger = logging.getLogger("xiniubot.authority")


def compute_pagerank(
    index,
    damping: float = 0.85,
    iterations: int = 15,
    epsilon: float = 1e-6,
) -> dict[int, float]:
    """对倒排索引中的文档计算简化 PageRank, 返回 {doc_id: score}."""
    docs = index._documents if hasattr(index, "_documents") else {}
    n = len(docs)
    if n == 0:
        return {}

    # url -> doc_id 映射 (用于解析出链)
    url_to_doc: dict[str, int] = {}
    for doc_id, doc in docs.items():
        if doc.url:
            url_to_doc[doc.url] = doc_id

    # 构建图: from_doc -> set(to_doc)
    edges: dict[int, set[int]] = {}
    for doc_id, doc in docs.items():
        out = set()
        for u in getattr(doc, "outlinks", None) or []:
            target = url_to_doc.get(u)
            if target is not None and target != doc_id:
                out.add(target)
        edges[doc_id] = out

    # 入链: to_doc -> list(from_doc)
    inlinks: dict[int, list[int]] = defaultdict(list)
    for src, outs in edges.items():
        for dst in outs:
            inlinks[dst].append(src)

    pr = {d: 1.0 / n for d in docs}
    dangle = damping / n if n else 0.0

    for _ in range(iterations):
        new_pr: dict[int, float] = {}
        dangling_sum = 0.0
        for d, score in pr.items():
            outs = edges.get(d, set())
            if not outs:
                dangling_sum += score
        for d in docs:
            s = (1.0 - damping) / n + dangle * dangling_sum
            for src in inlinks.get(d, []):
                outdeg = len(edges.get(src, set())) or 1
                s += damping * pr[src] / outdeg
            new_pr[d] = s
        # 收敛判定
        diff = sum(abs(new_pr[d] - pr[d]) for d in docs)
        pr = new_pr
        if diff < epsilon:
            break

    return pr


def normalize(scores: dict[int, float]) -> dict[int, float]:
    """min-max 归一化到 [0,1]."""
    if not scores:
        return {}
    lo = min(scores.values())
    hi = max(scores.values())
    span = hi - lo
    if span <= 1e-12:
        return {d: 1.0 for d in scores}
    return {d: (v - lo) / span for d, v in scores.items()}


def compute_authority(index) -> dict:
    """计算权威分并写回索引中的每个文档. 返回统计信息."""
    raw = compute_pagerank(index)
    norm = normalize(raw)
    changed = 0
    for doc_id, score in norm.items():
        doc = index.get_document(doc_id)
        if doc is not None:
            doc.authority = round(score, 6)
            changed += 1
    stats = {
        "docs": changed,
        "authority_weight": None,  # 由调用方补 RANKING 权重
        "example_max": round(max(norm.values()), 6) if norm else 0,
        "example_min": round(min(norm.values()), 6) if norm else 0,
    }
    return stats
