"""
xiniubot 搜索引擎核心
====================
整合分词、索引、排序, 提供完整搜索服务.

专业对齐增强:
  - 高级查询语法: "精确短语" / -排除 / OR / site: / intitle: / inurl:
  - 字段加权: 标题/描述命中加权 (入库时记录 title_tokens / desc_tokens)
  - 链接权威分融合 (Document.authority)
  - 内容近似去重 (SimHash)
  - 拼写纠错 (did-you-mean) 与搜索建议 (suggest)
"""

import html
import logging
import re
import time

from indexer.tokenizer import tokenize
from indexer.inverted_index import InvertedIndex, Document
from indexer.ranker import bm25_score, bm25_phrase_score
from indexer.simhash import simhash

import config

logger = logging.getLogger("xiniubot.search")


class SearchResult:
    """单条搜索结果."""
    def __init__(self):
        self.doc_id: int = 0
        self.url: str = ""
        self.title: str = ""
        self.description: str = ""
        self.snippet: str = ""
        self.score: float = 0.0
        self.word_count: int = 0
        self.authority: float = 0.0


class QuerySpec:
    """解析后的查询条件."""
    def __init__(self):
        self.terms: list[str] = []          # 必含词 (参与打分)
        self.phrases: list[list[str]] = []  # 精确短语 (逐短语)
        self.or_groups: list[list[str]] = []  # OR 语义分组
        self.exclude: set[str] = set()      # 排除词
        self.site: str | None = None        # site: 域名
        self.intitle: str | None = None     # intitle: 词
        self.inurl: str | None = None       # inurl: 串
        self.has_operators: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.or_groups or self.phrases)


def parse_query(q: str) -> QuerySpec:
    """解析查询语法, 返回 QuerySpec."""
    spec = QuerySpec()
    q = (q or "").strip()
    if not q:
        return spec

    spec.has_operators = bool(
        re.search(r'(?i)(site|intitle|inurl):|"|(?<![\w])(OR)(?![\w])|-(?!\s)', q)
    )

    # 1) 精确短语
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]*"', " ", q)

    # 2) 字段操作符
    def _grab(prefix):
        nonlocal rest
        m = re.search(r"(?<![\w])" + prefix + r":(\S+)", rest, re.I)
        if m:
            rest = rest.replace(m.group(0), " ")
            return m.group(1)
        return None

    spec.site = _grab("site")
    spec.intitle = _grab("intitle")
    spec.inurl = _grab("inurl")

    # 3) 排除
    for m in re.finditer(r'-(?:"([^"]+)"|(\S+))', rest):
        if m.group(1):
            spec.exclude.update(tokenize(m.group(1)))
        elif m.group(2):
            spec.exclude.update(tokenize(m.group(2)))
    rest = re.sub(r'-(?:"[^"]+"|\S+)', " ", rest)

    # 4) OR 分组
    for g in re.split(r"(?i)\bOR\b", rest):
        toks = tokenize(g)
        if toks:
            spec.or_groups.append(toks)

    # 短语词序
    for ph in phrases:
        pt = tokenize(ph)
        if pt:
            spec.phrases.append(pt)

    # 全局必含词 (去重, 保持顺序)
    seen = set()
    for toks in spec.or_groups:
        for t in toks:
            if t not in seen:
                seen.add(t)
                spec.terms.append(t)
    for pt in spec.phrases:
        for t in pt:
            if t not in seen:
                seen.add(t)
                spec.terms.append(t)
    spec.exclude.difference_update(seen)
    return spec


def _edit_distance(a: str, b: str, max_dist: int = 2) -> int:
    """编辑距离 (限制在 max_dist 内快速剪枝)."""
    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
        if min(prev) > max_dist:
            return max_dist + 1
    return prev[-1]


class SearchEngine:
    """搜索引擎核心."""

    def __init__(self, index: InvertedIndex | None = None):
        self._index = index or InvertedIndex()
        self._vocab: dict[str, int] | None = None  # term -> 文档频率

    @property
    def index(self) -> InvertedIndex:
        return self._index

    # ── 查询语法 ──────────────────────────────────────

    def search(
        self,
        query: str,
        page: int = 1,
        page_size: int | None = None,
        phrase: bool = False,
    ) -> tuple[list[SearchResult], int]:
        """搜索: 支持高级语法; phrase=True 时整句按短语处理."""
        spec = parse_query(query)
        if phrase and spec.terms:
            spec.phrases = [list(spec.terms)]
        return self._search_spec(spec, page, page_size)

    def _search_spec(self, spec: QuerySpec, page: int, page_size: int | None):
        page_size = page_size or config.SEARCH["page_size"]
        index = self._index
        if index.total_docs == 0:
            return [], 0

        # 1) 候选集
        candidates: set[int] = set()
        if spec.has_content:
            for toks in spec.or_groups:
                candidates |= self._term_docs(toks)
            for pt in spec.phrases:
                candidates |= set(index.phrase_search(pt))
        else:
            candidates = set(index._documents.keys())

        # 2) 硬过滤: site / inurl / intitle / exclude
        keep = set()
        for did in candidates:
            doc = index.get_document(did)
            if doc is None:
                continue
            if spec.site and not _domain_match(doc.url, spec.site):
                continue
            if spec.inurl and spec.inurl not in doc.url:
                continue
            if spec.intitle and not (getattr(doc, "title_tokens", None) and spec.intitle in doc.title_tokens):
                continue
            keep.add(did)
        candidates = keep

        if spec.exclude:
            ex_docs: set[int] = set()
            for t in spec.exclude:
                ex_docs |= {p.doc_id for p in index.get_postings(t)}
            candidates -= ex_docs

        if not candidates:
            return [], 0

        # 3) 打分
        if spec.terms:
            scores: dict[int, float] = dict(
                bm25_score(index, spec.terms, top_k=len(candidates), candidate_docs=candidates)
            )
            for pt in spec.phrases:
                ph = dict(
                    bm25_phrase_score(index, pt, top_k=len(candidates), candidate_docs=candidates)
                )
                for k, v in ph.items():
                    scores[k] = scores.get(k, 0.0) + v
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:1000]
        else:
            # 纯过滤查询 (如 site:xxx): 按文档 id 倒序
            ranked = [(did, 0.0) for did in sorted(candidates, reverse=True)]

        total = len(ranked)
        start = (page - 1) * page_size
        end = start + page_size
        results = [
            self._build_result(index, did, score, spec.terms)
            for did, score in ranked[start:end]
        ]
        return results, total

    def _term_docs(self, toks: list[str]) -> set[int]:
        """包含全部 toks 的文档集 (组内 AND)."""
        result: set[int] | None = None
        for t in toks:
            s = {p.doc_id for p in self._index.get_postings(t)}
            if not s:
                return set()
            result = s if result is None else (result & s)
        return result or set()

    def _build_result(self, index, doc_id: int, score: float, query_terms: list[str]) -> SearchResult:
        doc = index.get_document(doc_id)
        sr = SearchResult()
        sr.doc_id = doc_id
        sr.url = doc.url
        sr.title = doc.title or doc.url
        sr.description = doc.description
        sr.score = score
        sr.word_count = doc.word_count
        sr.authority = getattr(doc, "authority", 0.0)
        sr.snippet = self._generate_snippet(doc.body_text, query_terms)
        return sr

    def _generate_snippet(self, text: str, terms: list[str], max_len: int | None = None) -> str:
        """生成结果摘要并高亮关键词."""
        max_len = max_len or config.SEARCH["snippet_chars"]
        if not text:
            return ""
        text_lower = text.lower()
        terms_lower = [t.lower() for t in terms]

        best_pos = -1
        for term in terms_lower:
            pos = text_lower.find(term)
            if pos >= 0 and (best_pos < 0 or pos < best_pos):
                best_pos = pos

        if best_pos < 0:
            snippet = text[:max_len]
        else:
            half = max_len // 2
            start = max(0, best_pos - half)
            end = min(len(text), start + max_len)
            snippet = text[start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(text):
                snippet = snippet + "..."

        snippet = html.escape(snippet)
        for term in terms:
            display = html.escape(term)
            pattern = re.compile(re.escape(html.escape(term)), re.IGNORECASE)
            snippet = pattern.sub(f"<b>{display}</b>", snippet)
        return snippet

    # ── 索引写入 ──────────────────────────────────────

    def add_page(self, url: str, title: str, description: str, body_text: str,
                 tokens: list[str], outlinks: list[str] | None = None):
        """将爬取的页面添加到索引, 记录字段词/出链/SimHash/抓取时间."""
        doc_id = self._index.new_doc_id()
        doc = Document(
            doc_id=doc_id,
            url=url,
            title=title,
            description=description,
            body_text=body_text,
            content_length=len(body_text),
        )
        doc.outlinks = list(outlinks) if outlinks else []
        doc.title_tokens = tokenize(title) if title else []
        doc.desc_tokens = tokenize(description) if description else []
        doc.simhash = simhash(tokens)
        doc.fetch_time = time.time()

        term_positions: dict[str, list[int]] = {}
        for pos, token in enumerate(tokens):
            if token not in term_positions:
                term_positions[token] = []
            term_positions[token].append(pos)
        # 标题/描述词若未出现在正文倒排, 补一条位置: 保证"仅字段命中"也能被检索,
        # 且不会与正文重复计数 (专业搜索引擎字段词与正文词共用倒排, 字段加权在排序层放大)
        next_pos = len(tokens)
        for extra in (doc.title_tokens, doc.desc_tokens):
            for token in extra:
                if token not in term_positions:
                    term_positions[token] = [next_pos]
                    next_pos += 1

        self._index.add_document(doc, term_positions)
        self._vocab = None
        return doc_id

    def check_duplicate(self, tokens: list[str]) -> int | None:
        """内容近似去重检查: 返回相似文档 doc_id 或 None."""
        if not config.DEDUP["enabled"]:
            return None
        sh = simhash(tokens)
        sims = self._index.find_similar(sh, config.DEDUP["simhash_threshold"])
        return sims[0] if sims else None

    def save(self):
        """保存索引."""
        self._index.save()
        logger.info(f"索引已保存: {self._index.stats()}")

    # ── 词汇表 / 纠错 / 建议 ──────────────────────────

    def _ensure_vocab(self) -> dict[str, int]:
        if self._vocab is None:
            self._vocab = {term: len(pl) for term, pl in self._index._postings.items()}
        return self._vocab

    def suggest(self, prefix: str, limit: int = 8) -> list[str]:
        """搜索建议: 返回以 prefix 开头、按文档频率排序的词."""
        prefix = (prefix or "").strip().lower()
        if not prefix:
            return []
        vocab = self._ensure_vocab()
        hits = [(df, w) for w, df in vocab.items() if w.startswith(prefix)]
        hits.sort(key=lambda x: (-x[0], x[1]))
        return [w for _, w in hits[:limit]]

    def correct(self, query: str) -> str | None:
        """拼写纠错 (did-you-mean): 对未命中词返回纠正后的查询, 否则 None."""
        toks = tokenize(query)
        if not toks:
            return None
        vocab = self._ensure_vocab()
        corrected = []
        changed = False
        for t in toks:
            if t in vocab:
                corrected.append(t)
                continue
            if not re.search(r"[a-zA-Z0-9]", t):
                corrected.append(t)
                continue
            cand = self._nearest(t, vocab)
            if cand:
                corrected.append(cand)
                changed = True
            else:
                corrected.append(t)
        if not changed:
            return None
        return " ".join(corrected)

    def _nearest(self, word: str, vocab: dict[str, int], max_dist: int = 2) -> str | None:
        best, best_d = None, max_dist + 1
        for cand in vocab:
            if cand == word:
                continue
            if abs(len(cand) - len(word)) > max_dist:
                continue
            d = _edit_distance(word, cand, max_dist)
            if d < best_d:
                best, best_d = cand, d
                if d == 1:
                    break
        return best


def _domain_match(url: str, site: str) -> bool:
    """site: 域名匹配 (支持子域名后缀匹配)."""
    site = site.lower().strip()
    if not site:
        return True
    m = re.match(r"^https?://([^/?#]+)", url)
    host = m.group(1).lower() if m else url.lower()
    if host == site:
        return True
    return host.endswith("." + site)
