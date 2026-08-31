"""
xiniubot 倒排索引
=================
结构:
  term -> PostingList [
      (doc_id, term_freq, [positions]),
      ...
  ]

支持:
  - 位置索引 (支持短语查询)
  - 文档长度记录 (用于 BM25)
  - 持久化到磁盘
"""

import os
import pickle
import threading
from collections import defaultdict
from dataclasses import dataclass, field

import config


# ═══════════════════════════════════════════════════════
#  文档元信息
# ═══════════════════════════════════════════════════════

@dataclass
class Document:
    doc_id: int
    url: str
    title: str = ""
    description: str = ""
    body_text: str = ""
    fetch_time: float = 0.0
    content_length: int = 0
    word_count: int = 0       # 分词后的 token 数
    outlinks: list = field(default_factory=list)   # 页面出链 (URL 列表, 供链接分析)
    title_tokens: list = field(default_factory=list)  # 标题分词 (字段加权)
    desc_tokens: list = field(default_factory=list)   # 描述分词 (字段加权)
    authority: float = 1.0     # 链接权威分 (PageRank 归一化)
    simhash: int = 0           # 内容 SimHash 指纹 (近似去重)


# ═══════════════════════════════════════════════════════
#  Posting (单条倒排记录)
# ═══════════════════════════════════════════════════════

@dataclass
class Posting:
    doc_id: int
    term_freq: int
    positions: list[int] = field(default_factory=list)


# ═══════════════════════════════════════════════════════
#  倒排索引类
# ═══════════════════════════════════════════════════════

class InvertedIndex:
    """
    内存倒排索引, 支持:
      - add_document(doc_id, term_positions_map)
      - search(term) -> PostingList
      - phrase_search(terms) -> doc_id 列表
      - persist / load
    """

    def __init__(self, index_dir: str | None = None):
        self._index_dir = index_dir or config.STORAGE["index_dir"]
        os.makedirs(self._index_dir, exist_ok=True)

        # term -> list[Posting]
        self._postings: dict[str, list[Posting]] = defaultdict(list)

        # doc_id -> Document
        self._documents: dict[int, Document] = {}

        # doc_id -> word_count (分词数)
        self._doc_lengths: dict[int, int] = {}

        # 全局统计
        self._total_docs: int = 0
        self._total_word_count: int = 0
        self._next_doc_id: int = 0

        self._lock = threading.Lock()

        # (doc_id, simhash) 列表, 用于内容近似去重
        self._simhashes: list[tuple[int, int]] = []

        # 尝试加载已有索引
        self._load()

    # ── 统计信息 ──────────────────────────────────────

    @property
    def total_docs(self) -> int:
        return self._total_docs

    @property
    def avg_doc_length(self) -> float:
        if self._total_docs == 0:
            return 0.0
        return self._total_word_count / self._total_docs

    @property
    def vocabulary_size(self) -> int:
        return len(self._postings)

    def get_doc_length(self, doc_id: int) -> int:
        return self._doc_lengths.get(doc_id, 0)

    def get_document(self, doc_id: int) -> Document | None:
        return self._documents.get(doc_id)

    # ── 添加文档 ──────────────────────────────────────

    def new_doc_id(self) -> int:
        with self._lock:
            doc_id = self._next_doc_id
            self._next_doc_id += 1
            return doc_id

    def add_document(self, doc: Document, term_positions: dict[str, list[int]]):
        """
        添加一个文档到索引.

        Args:
            doc: Document 对象
            term_positions: {term: [pos1, pos2, ...]} 词项在文档中的位置
        """
        with self._lock:
            doc_id = doc.doc_id
            self._documents[doc_id] = doc

            word_count = 0
            for term, positions in term_positions.items():
                tf = len(positions)
                word_count += tf
                self._postings[term].append(
                    Posting(doc_id=doc_id, term_freq=tf, positions=positions)
                )

            self._doc_lengths[doc_id] = word_count
            doc.word_count = word_count
            if doc.simhash:
                self._simhashes.append((doc_id, doc.simhash))
            self._total_docs += 1
            self._total_word_count += word_count

    def remove_document(self, doc_id: int) -> bool:
        """删除指定文档及其所有倒排记录. 返回是否删除成功."""
        with self._lock:
            doc = self._documents.pop(doc_id, None)
            if doc is None:
                return False
            wc = self._doc_lengths.pop(doc_id, 0)
            removed = False
            for term in list(self._postings.keys()):
                plist = self._postings[term]
                before = len(plist)
                plist[:] = [p for p in plist if p.doc_id != doc_id]
                if len(plist) != before:
                    removed = True
                if not plist:
                    del self._postings[term]
            self._total_docs -= 1
            self._total_word_count -= wc
            self._simhashes = [(i, h) for i, h in self._simhashes if i != doc_id]
            return removed

    def find_similar(self, fingerprint: int, threshold: int = 3) -> list[int]:
        """查找内容与给定 SimHash 指纹近似重复的 doc_id 列表 (海明距离 <= threshold)."""
        if not fingerprint:
            return []
        from indexer.simhash import hamming
        return [did for did, fp in self._simhashes if fp and hamming(fp, fingerprint) <= threshold]


    # ── 查询 ──────────────────────────────────────────

    def get_postings(self, term: str) -> list[Posting]:
        """获取词项的倒排列表."""
        return self._postings.get(term, [])

    def get_doc_freq(self, term: str) -> int:
        """获取词项的文档频率."""
        return len(self._postings.get(term, []))

    def phrase_search(self, terms: list[str]) -> list[int]:
        """
        短语查询: 查找包含连续词序列的文档.
        返回匹配的 doc_id 列表.
        """
        if not terms:
            return []
        if len(terms) == 1:
            return [p.doc_id for p in self.get_postings(terms[0])]

        # 获取每个词项的 posting list
        posting_lists = [self.get_postings(t) for t in terms]
        if any(len(pl) == 0 for pl in posting_lists):
            return []

        # 求文档交集
        common_docs = set(p.doc_id for p in posting_lists[0])
        for pl in posting_lists[1:]:
            common_docs &= set(p.doc_id for p in pl)

        if not common_docs:
            return []

        # 在公共文档中检查位置连续性
        result = []
        for doc_id in common_docs:
            # 获取第一个词项的位置
            pos_sets = []
            for i, term in enumerate(terms):
                for p in self.get_postings(term):
                    if p.doc_id == doc_id:
                        pos_sets.append(set(p.positions))
                        break
                else:
                    pos_sets.append(set())

            # 检查是否存在连续位置: pos[0]+1 == pos[1], pos[1]+1 == pos[2], ...
            first_positions = pos_sets[0]
            for start_pos in first_positions:
                match = True
                for i in range(1, len(terms)):
                    if (start_pos + i) not in pos_sets[i]:
                        match = False
                        break
                if match:
                    result.append(doc_id)
                    break

        return result

    # ── 持久化 ────────────────────────────────────────

    def save(self):
        """将索引持久化到磁盘."""
        with self._lock:
            data = {
                "postings": dict(self._postings),
                "documents": self._documents,
                "doc_lengths": self._doc_lengths,
                "total_docs": self._total_docs,
                "total_word_count": self._total_word_count,
                "next_doc_id": self._next_doc_id,
                "simhashes": list(self._simhashes),
            }
            path = os.path.join(self._index_dir, "inverted_index.pkl")
            tmp_path = path + ".tmp"
            with open(tmp_path, "wb") as f:
                pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_path, path)

    def _load(self):
        """从磁盘加载索引."""
        path = os.path.join(self._index_dir, "inverted_index.pkl")
        if not os.path.isfile(path):
            return
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            self._postings = defaultdict(list, data["postings"])
            self._documents = data["documents"]
            self._doc_lengths = data["doc_lengths"]
            self._total_docs = data["total_docs"]
            self._total_word_count = data["total_word_count"]
            self._next_doc_id = data["next_doc_id"]
            self._simhashes = list(data.get("simhashes", []))
        except Exception:
            pass

    # ── 统计输出 ──────────────────────────────────────

    def stats(self) -> dict:
        return {
            "total_docs": self._total_docs,
            "vocabulary_size": self.vocabulary_size,
            "avg_doc_length": f"{self.avg_doc_length:.1f}",
            "total_word_count": self._total_word_count,
        }
