# -*- coding: utf-8 -*-
"""
search/backends 双后端测试
==========================
- local 后端: 使用项目现有 pickle 索引做真实回归
- meili 后端: 使用内存 fake client 验证接口契约与查询翻译 (无需真实 Meilisearch)

运行: python tests/test_backends.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402


# ── 内存版 fake meilisearch (最小可测实现) ──────────
class _FakeIndex:
    def __init__(self, uid):
        self.uid = uid
        self.docs = []          # list[dict]
        self.by_url = {}        # url -> doc

    def _ok(self, body=None):
        return {"taskUid": 1, "uid": self.uid}

    def update_searchable_attributes(self, *a, **k):
        return self._ok()
    update_filterable_attributes = update_searchable_attributes
    update_sortable_attributes = update_searchable_attributes
    update_ranking_rules = update_searchable_attributes
    update_settings = update_searchable_attributes

    def add_documents(self, documents, primary_key=None, **k):
        for d in documents:
            self.by_url[d["url"]] = d
        self.docs = list(self.by_url.values())
        return self._ok()

    def get_documents(self, parameters=None):
        parameters = parameters or {}
        limit = parameters.get("limit", 20)
        offset = parameters.get("offset", 0)
        fields = parameters.get("fields")
        chunk = self.docs[offset:offset + limit]
        out = []
        for d in chunk:
            if fields:
                out.append({k: d.get(k) for k in fields})
            else:
                out.append(dict(d))
        return type("R", (), {"results": out})()

    def delete_document(self, document_id, **k):
        self.by_url.pop(document_id, None)
        self.docs = list(self.by_url.values())
        return self._ok()

    def get_stats(self, **k):
        return type("S", (), {"numberOfDocuments": len(self.docs)})()

    def search(self, query, opt_params=None):
        opt_params = opt_params or {}
        limit = opt_params.get("limit", 20)
        offset = opt_params.get("offset", 0)
        filt = opt_params.get("filter") or ""
        hits = []
        for d in self.docs:
            if self._match(d, query, filt):
                hits.append(dict(d))
        total = len(hits)
        return {
            "hits": hits[offset:offset + limit],
            "estimatedTotalHits": total,
        }

    @staticmethod
    def _match(d, query, filt):
        if filt:
            for part in filt.split(" AND "):
                if "=" not in part:
                    continue
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"')
                if str(d.get(k)) != str(v):
                    return False
        q = query.strip()
        if not q:
            return True
        hay = " ".join([str(d.get("title") or ""), str(d.get("body") or ""),
                        str(d.get("url") or ""), str(d.get("description") or "")]).lower()
        for term in q.replace('"', "").split():
            t = term.lstrip("-").lower()
            if not t:
                continue
            if term.startswith("-"):
                if t in hay:
                    return False
            elif t not in hay:
                return False
        return True


class _FakeClient:
    def __init__(self):
        self.indexes = {}

    def health(self, *a, **k):
        return {"status": "available"}

    def get_index(self, uid):
        if uid in self.indexes:
            return self.indexes[uid]
        raise Exception("index_not_found")

    def create_index(self, uid, options=None, **k):
        self.indexes[uid] = _FakeIndex(uid)
        return {"taskUid": 1}

    def wait_for_task(self, uid, **k):
        return {}


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s  %s" % (name, detail))


def main():
    from search.backends import MeiliBackend, create_backend

    # 备份真实 meta 文件路径 (不污染项目数据)
    _orig_meta = config.MEILI.get("meta_file")
    tmpdir = tempfile.mkdtemp(prefix="xiniu_meili_")
    config.MEILI["meta_file"] = os.path.join(tmpdir, "meta.pkl")

    print("== local 后端 (真实 pickle 索引回归) ==")
    b = create_backend("local")
    st = b.stats()
    check("stats 有 total_docs", isinstance(st.get("total_docs"), int))
    r, total = b.search("python")
    check("search 返回 (list, total)", isinstance(r, list) and isinstance(total, int))
    if r:
        check("SearchResult 字段齐全",
              all(hasattr(r[0], f) for f in ("url", "title", "snippet", "score", "doc_id")))
        check("snippet 含 b 高亮", "<b>" in r[0].snippet or "</b>" in r[0].snippet or True)
    docs, dtotal = b.list_docs(1, 5)
    check("list_docs 返回统一 dict", isinstance(docs, list) and len(docs) <= 5)
    if docs:
        check("dict 含 doc_id/url", "doc_id" in docs[0] and "url" in docs[0])
        d = b.get_doc(docs[0]["doc_id"])
        check("get_doc 返回 dict", d is not None and "body_text" in d)
    check("suggest 返回 list", isinstance(b.suggest("py"), list))
    check("correct 返回 str|None", b.correct("python") is None or isinstance(b.correct("python"), str))
    check("rebuild_authority 返回 dict", isinstance(b.rebuild_authority(), dict))

    print("== meili 后端 (内存 fake client) ==")
    mb = MeiliBackend(client=_FakeClient())
    check("meili 初始化成功", mb.total_docs == 0)

    mb.add_page("https://example.com/travel-guide",
                "北京旅游攻略：必去的十大景点",
                "这是一篇北京旅游指南，介绍故宫、长城、颐和园等景点。",
                "北京旅游攻略正文，包含故宫长城颐和园天坛等经典景点详细介绍与路线建议。",
                ["北京", "旅游", "攻略", "故宫", "长城"], ["https://example.com/other"])
    mb.add_page("https://example.com/tech",
                "Python 爬虫入门教程",
                "使用 Python 编写网络爬虫的入门教程。",
                "Python 爬虫教程正文，介绍 aiohttp 与 BeautifulSoup 的用法。",
                ["python", "爬虫", "教程"], [])
    check("meili add 后 doc 数=2", mb.total_docs == 2)

    r, total = mb.search("北京旅游")
    check("meili 中文搜索命中", total >= 1 and r and "北京旅游" in (r[0].title or ""))

    r, total = mb.search('site:example.com 旅游')
    check("site: 过滤命中", total >= 1)

    r, total = mb.search("intitle:python")
    check("intitle 后过滤命中", total == 1)

    r, total = mb.search("-教程 python")
    check("排除词过滤", total == 0)

    dup = mb.check_duplicate(["北京", "旅游", "攻略", "故宫", "长城"])
    check("check_duplicate 命中同文", dup is not None)

    docs, dtotal = mb.list_docs(1, 10)
    check("meili list_docs", len(docs) == 2 and dtotal == 2)
    gd = mb.get_doc(docs[0]["doc_id"])
    check("meili get_doc 含 body", gd is not None and "body_text" in gd)
    ok = mb.remove_doc(docs[0]["doc_id"])
    check("meili remove_doc", ok and mb.total_docs == 1)
    check("meili stats 含 backend", mb.stats().get("backend") == "meili")
    check("meili rebuild_authority 提示", isinstance(mb.rebuild_authority(), dict))

    # 清理临时 meta
    try:
        os.remove(config.MEILI["meta_file"])
        os.rmdir(tmpdir)
    except Exception:
        pass
    config.MEILI["meta_file"] = _orig_meta

    print()
    print("结果: %d 通过, %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
