"""
xiniubot 搜索后端抽象
====================
提供两种可切换的检索实现 (config.SEARCH_BACKEND):

  - "local": 自研 pickle 倒排索引 (SearchEngine + InvertedIndex), 完全向后兼容
  - "meili": Meilisearch (Docker/独立服务), 开箱即用中文分词/typo/相关性排序

统一接口 (SearchBackend):
  search / add_page / check_duplicate / save / suggest / correct
  stats / list_docs / get_doc / remove_doc / rebuild_authority
  .index (兼容属性: stats()/vocabulary_size)

Meili 模式下对原有高级语法的映射:
  - "短语"        -> Meili 引号短语
  - -排除         -> Meili 查询级排除词
  - OR            -> Meili 默认 OR 语义
  - site:域名     -> Meili filter (domain 字段精确匹配)
  - intitle:/inurl: -> 拉取候选集后在本地做字段过滤 (Meili filter 不支持 contains)
  - 字段加权/权威分 -> Meili searchableAttributes 顺序 + rankingRules 中的 authority:desc
"""

import html
import logging
import os
import pickle
import time
from urllib.parse import urlparse

import config
from indexer.simhash import simhash
from indexer.tokenizer import tokenize
from search.engine import SearchResult, parse_query

logger = logging.getLogger("xiniubot.backend")


# ═══════════════════════════════════════════════════════
#  Local 后端
# ═══════════════════════════════════════════════════════
class LocalBackend:
    """自研 pickle 索引后端 (现有行为, 用于回归与回退)."""

    def __init__(self, engine=None):
        from search.engine import SearchEngine
        self.engine = engine or SearchEngine()

    @property
    def index(self):
        return self.engine.index

    def search(self, query, page=1, page_size=None, phrase=False):
        return self.engine.search(query, page=page, page_size=page_size, phrase=phrase)

    def add_page(self, url, title, description, body_text, tokens, outlinks=None):
        return self.engine.add_page(url, title, description, body_text, tokens, outlinks)

    def check_duplicate(self, tokens):
        return self.engine.check_duplicate(tokens)

    def save(self):
        self.engine.save()

    def suggest(self, prefix, limit=8):
        return self.engine.suggest(prefix, limit)

    def correct(self, query):
        return self.engine.correct(query)

    def stats(self):
        return self.engine.index.stats()

    @property
    def total_docs(self):
        return self.engine.index.total_docs

    def list_docs(self, page, size):
        idx = self.engine.index
        ids = sorted(idx._documents.keys(), reverse=True)
        total = len(ids)
        start = (page - 1) * size
        chunk = ids[start:start + size]
        out = []
        for did in chunk:
            d = idx.get_document(did)
            if d:
                out.append(_doc_to_dict(d))
        return out, total

    def get_doc(self, doc_id):
        d = self.engine.index.get_document(int(doc_id))
        return _doc_to_dict(d) if d else None

    def remove_doc(self, doc_id):
        idx = self.engine.index
        ok = idx.remove_document(int(doc_id))
        if ok:
            idx.save()
        return ok

    def rebuild_authority(self):
        from indexer.authority import compute_authority
        stats = compute_authority(self.engine.index)
        self.engine.save()
        return stats


def _doc_to_dict(d) -> dict:
    """把 InvertedIndex.Document 转成统一 dict."""
    return {
        "doc_id": d.doc_id,
        "url": d.url,
        "title": d.title,
        "description": d.description,
        "body_text": d.body_text,
        "word_count": d.word_count,
        "content_length": d.content_length,
        "authority": getattr(d, "authority", 0.0),
        "simhash": getattr(d, "simhash", 0),
        "outlinks": list(getattr(d, "outlinks", []) or []),
        "fetch_time": getattr(d, "fetch_time", 0) or 0,
    }


# ═══════════════════════════════════════════════════════
#  Meili 后端
# ═══════════════════════════════════════════════════════
class MeiliBackend:
    """Meilisearch 后端 (开箱即用的中文全文检索)."""

    def __init__(self, client=None):
        self._meili_ok = False
        try:
            from meilisearch import Client
            self._client = client or Client(config.MEILI["host"], config.MEILI.get("api_key", "") or None)
            self._client.health()
            self._meili_ok = True
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "无法连接 Meilisearch (%s). 请启动 meilisearch 服务, 或将 SEARCH_BACKEND 设为 local." % exc
            ) from exc

        self._index_name = config.MEILI["index_name"]
        self._meta_file = config.MEILI["meta_file"]
        self._index = self._ensure_index()

        # 本地元数据 (SimHash 去重 + url->doc_id 映射)
        self._simhashes: dict[int, int] = {}
        self._url_docid: dict[str, int] = {}
        self._next_docid = 1
        self._load_meta()

    # ── 索引与 settings ──────────────────────────────
    def _wait(self, task_uid: int):
        try:
            self._client.wait_for_task(int(task_uid), timeout_in_ms=20000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Meili 任务等待失败: %s", exc)

    def _ensure_index(self):
        name = self._index_name
        try:
            idx = self._client.get_index(name)
        except Exception:  # noqa: BLE001
            info = self._client.create_index(name, {"primaryKey": "url"})
            self._wait(info["taskUid"])
            idx = self._client.get_index(name)
        # settings: 字段加权(顺序) / 过滤 / 排序 / 权威融合
        try:
            idx.update_searchable_attributes(["title", "description", "body", "url"])
            idx.update_filterable_attributes(["domain", "url", "doc_id", "authority"])
            idx.update_sortable_attributes(["doc_id", "authority", "fetch_time"])
            rules = ["words", "typo", "proximity", "attribute", "sort", "exactness"]
            if config.RANKING.get("authority_weight", 0) > 0:
                rules.append("authority:desc")
            idx.update_ranking_rules(rules)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Meili settings 更新失败: %s", exc)
        return idx

    # ── 本地元数据持久化 ─────────────────────────────
    def _load_meta(self):
        if os.path.isfile(self._meta_file):
            try:
                with open(self._meta_file, "rb") as f:
                    data = pickle.load(f)
                self._simhashes = data.get("simhashes", {})
                self._url_docid = data.get("url_docid", {})
                self._next_docid = data.get("next_docid", 1)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Meili 元数据读取失败, 尝试从索引重建: %s", exc)
        # 从 Meili 全量重建
        try:
            offset = 0
            while True:
                page = self._index.get_documents({"limit": 1000, "offset": offset,
                                                  "fields": ["url", "doc_id", "simhash"]})
                res = page.results
                if not res:
                    break
                for d in res:
                    did = int(d.get("doc_id") or 0)
                    url = d.get("url", "")
                    if did and url:
                        self._url_docid[url] = did
                        sh = d.get("simhash")
                        if sh:
                            self._simhashes[did] = int(sh)
                        if did >= self._next_docid:
                            self._next_docid = did + 1
                offset += len(res)
                if len(res) < 1000:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Meili 元数据重建失败: %s", exc)

    def save(self):
        try:
            os.makedirs(os.path.dirname(self._meta_file), exist_ok=True)
            with open(self._meta_file, "wb") as f:
                pickle.dump({
                    "simhashes": self._simhashes,
                    "url_docid": self._url_docid,
                    "next_docid": self._next_docid,
                }, f)
            logger.info("Meili 本地元数据已保存: %d 篇", len(self._url_docid))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Meili 元数据保存失败: %s", exc)

    @property
    def index(self):
        return _MeiliIndexView(self)

    @property
    def total_docs(self):
        try:
            return int(self._index.get_stats().numberOfDocuments)
        except Exception:  # noqa: BLE001
            return len(self._url_docid)

    # ── 写入 ─────────────────────────────────────────
    def add_page(self, url, title, description, body_text, tokens, outlinks=None):
        doc_id = self._url_docid.get(url)
        if doc_id is None:
            doc_id = self._next_docid
            self._next_docid += 1
        sh = simhash(tokens)
        host = _host_of(url)
        doc = {
            "url": url,
            "doc_id": doc_id,
            "title": title or "",
            "description": description or "",
            "body": body_text or "",
            "authority": 1.0,
            "fetch_time": time.time(),
            "simhash": sh,
            "outlinks": list(outlinks) if outlinks else [],
            "domain": host,
            "word_count": len(tokens),
        }
        try:
            info = self._index.add_documents([doc])
            self._wait(info["taskUid"])
        except Exception as exc:  # noqa: BLE001
            logger.error("Meili 写入失败 %s: %s", url[:80], exc)
            raise
        self._url_docid[url] = doc_id
        self._simhashes[doc_id] = sh
        return doc_id

    def check_duplicate(self, tokens):
        if not config.DEDUP["enabled"]:
            return None
        sh = simhash(tokens)
        for did, old in self._simhashes.items():
            if _hamming(sh, old) <= config.DEDUP["simhash_threshold"]:
                return did
        return None

    # ── 检索 ─────────────────────────────────────────
    def search(self, query, page=1, page_size=None, phrase=False):
        page_size = page_size or config.SEARCH["page_size"]
        spec = parse_query(query)
        if phrase and spec.terms:
            spec.phrases = [list(spec.terms)]
        try:
            return self._search_spec(spec, page, page_size)
        except Exception as exc:  # noqa: BLE001
            logger.error("Meili 搜索失败: %s", exc)
            return [], 0

    def _search_spec(self, spec, page, page_size):
        q = _meili_query(spec)
        filters = []
        if spec.site:
            filters.append('domain = "%s"' % _escape_filter(spec.site))
        filter_expr = " AND ".join(filters) if filters else None

        need_post = bool(spec.intitle or spec.inurl)
        if not need_post:
            res = self._index.search(q or "", {
                "limit": page_size,
                "offset": (page - 1) * page_size,
                "filter": filter_expr,
                "attributesToHighlight": ["title", "description"],
                "highlightPreTag": "<b>",
                "highlightPostTag": "</b>",
            })
            hits = res.get("hits", [])
            total = int(res.get("estimatedTotalHits", 0))
            return [self._hit_to_result(h) for h in hits], total

        # intitle / inurl: 拉候选集后本地过滤 (Meili filter 不支持 contains)
        limit = config.MEILI.get("max_docs_per_query", 2000)
        hits = []
        offset = 0
        while offset < limit:
            res = self._index.search(q or "", {
                "limit": 1000,
                "offset": offset,
                "filter": filter_expr,
                "attributesToHighlight": ["title", "description"],
                "highlightPreTag": "<b>",
                "highlightPostTag": "</b>",
            })
            hits.extend(res.get("hits", []) or [])
            if not res.get("hits"):
                break
            offset += len(res["hits"])
        if spec.intitle:
            it = spec.intitle.lower()
            hits = [h for h in hits if it in (h.get("title") or "").lower()]
        if spec.inurl:
            iu = spec.inurl.lower()
            hits = [h for h in hits if iu in (h.get("url") or "").lower()]
        total = len(hits)
        start = (page - 1) * page_size
        return [self._hit_to_result(h) for h in hits[start:start + page_size]], total

    def _hit_to_result(self, h) -> SearchResult:
        sr = SearchResult()
        sr.doc_id = int(h.get("doc_id") or 0)
        sr.url = h.get("url", "")
        sr.title = h.get("title") or h.get("url", "")
        sr.description = h.get("description") or ""
        sr.word_count = int(h.get("word_count") or 0)
        sr.authority = float(h.get("authority") or 0.0)
        sr.score = float(h.get("_rankingScore") or 0.0)
        # 高亮摘要 (Meili 用 <b> 已通过 highlightPreTag/PostTag 配置)
        fmt = h.get("_formatted") or {}
        snippet = fmt.get("description") or fmt.get("body") or sr.description
        sr.snippet = _clean_snippet(snippet, config.SEARCH["snippet_chars"])
        return sr

    # ── 建议 / 纠错 ─────────────────────────────────
    def suggest(self, prefix, limit=8):
        prefix = (prefix or "").strip()
        if not prefix or not self._meili_ok:
            return []
        try:
            res = self._index.search(prefix, {
                "limit": min(limit, 20),
                "attributesToRetrieve": ["title"],
            })
            words: list[str] = []
            seen: set[str] = set()
            for h in res.get("hits", []) or []:
                for t in tokenize(h.get("title") or ""):
                    t = t.lower()
                    if t.startswith(prefix.lower()) and t not in seen:
                        seen.add(t)
                        words.append(t)
                        if len(words) >= limit:
                            break
                if len(words) >= limit:
                    break
            return words
        except Exception as exc:  # noqa: BLE001
            logger.error("Meili suggest 失败: %s", exc)
            return []

    def correct(self, query):
        # Meili 自带 typo tolerance, 不需要 did-you-mean
        return None

    # ── 统计 / 文档管理 ──────────────────────────────
    def stats(self):
        total = self.total_docs
        return {
            "total_docs": total,
            "vocabulary_size": 0,
            "avg_doc_length": 0,
            "backend": "meili",
        }

    def list_docs(self, page, size):
        try:
            page_res = self._index.get_documents({
                "limit": size,
                "offset": (page - 1) * size,
                "fields": ["doc_id", "url", "title", "description", "word_count", "authority"],
            })
            out = []
            for d in page_res.results:
                out.append({
                    "doc_id": int(d.get("doc_id") or 0),
                    "url": d.get("url", ""),
                    "title": d.get("title") or "",
                    "description": d.get("description") or "",
                    "word_count": int(d.get("word_count") or 0),
                    "authority": float(d.get("authority") or 0.0),
                    "score": 0.0,
                })
            return out, self.total_docs
        except Exception as exc:  # noqa: BLE001
            logger.error("Meili 文档列表失败: %s", exc)
            return [], 0

    def get_doc(self, doc_id):
        try:
            res = self._index.search("", {
                "limit": 1,
                "filter": "doc_id = %d" % int(doc_id),
                "attributesToRetrieve": ["*"],
            })
            hits = res.get("hits") or []
            if not hits:
                return None
            h = hits[0]
            return {
                "doc_id": int(h.get("doc_id") or 0),
                "url": h.get("url", ""),
                "title": h.get("title") or "",
                "description": h.get("description") or "",
                "body_text": h.get("body") or "",
                "word_count": int(h.get("word_count") or 0),
                "content_length": len(h.get("body") or ""),
                "authority": float(h.get("authority") or 0.0),
                "simhash": int(h.get("simhash") or 0),
                "outlinks": list(h.get("outlinks") or []),
                "fetch_time": h.get("fetch_time") or 0,
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Meili get_doc 失败: %s", exc)
            return None

    def remove_doc(self, doc_id):
        try:
            doc = self.get_doc(doc_id)
            if not doc:
                return False
            info = self._index.delete_document(doc["url"])
            self._wait(info["taskUid"])
            self._url_docid.pop(doc["url"], None)
            self._simhashes.pop(int(doc_id), None)
            self.save()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Meili remove_doc 失败: %s", exc)
            return False

    def rebuild_authority(self):
        # Meili 模式: 权威分由 rankingRules 中的 authority:desc 承担
        logger.info("Meili 模式: 权威分由 Meili 排序规则承担, 无需本地 PageRank")
        return {"docs": self.total_docs, "backend": "meili", "note": "authority:desc 规则已启用"}


class _MeiliIndexView:
    """兼容 engine.index 的最小视图 (供 search_server/main 的 stats 调用)."""

    def __init__(self, backend: "MeiliBackend"):
        self._backend = backend

    @property
    def vocabulary_size(self) -> int:
        return 0

    @property
    def total_docs(self) -> int:
        return self._backend.total_docs

    def stats(self) -> dict:
        return self._backend.stats()


# ═══════════════════════════════════════════════════════
#  工具
# ═══════════════════════════════════════════════════════
def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _escape_filter(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


def _meili_query(spec) -> str:
    """把 QuerySpec 翻译成 Meili query 字符串."""
    parts: list[str] = []
    for pt in spec.phrases:
        parts.append('"%s"' % " ".join(pt))
    seen = set(spec.phrases and [tuple(p) for p in spec.phrases] or [])
    for t in spec.terms:
        if t in seen:
            continue
        parts.append(t)
    for t in spec.exclude:
        parts.append("-%s" % t)
    return " ".join(parts)


def _clean_snippet(text: str, max_len: int) -> str:
    if not text:
        return ""
    text = html.unescape(text or "")
    if len(text) > max_len:
        text = text[:max_len] + "..."
    # Meili 高亮默认 <em>, 统一为 <b> 以匹配前端样式
    text = text.replace("<em>", "<b>").replace("</em>", "</b>")
    return text


# ═══════════════════════════════════════════════════════
#  工厂
# ═══════════════════════════════════════════════════════
def create_backend(backend: str | None = None):
    """按 config.SEARCH_BACKEND 创建后端实例."""
    name = (backend or config.SEARCH_BACKEND or "local").strip().lower()
    if name == "meili":
        return MeiliBackend()
    return LocalBackend()
