# -*- coding: utf-8 -*-
"""xiniubot 专业对齐功能回归测试 (第五轮).
运行: python tests/test_pro_alignment.py  (在项目根目录下)
覆盖: URL清洗 / 断点续爬 / SimHash 去重 / 字段加权排序 / 链接权威分 /
      高级查询语法 / 后台 RANKING-DEDUP 配置 / 重建权威分 API / 搜索建议与纠错 API.
注意: 会读取/写入本地真实索引与配置覆盖层 (data/admin/config.json), 测试后恢复覆盖层.
"""
import io, os, sys, time, json, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
out = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pro_out.txt"), "w", encoding="utf-8")

def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg)
    out.write(msg + "\n")
    out.flush()

import config
config.ADMIN["password"] = "testpass123"   # 固定测试密码

from crawler.frontier import URLFrontier
from indexer.inverted_index import InvertedIndex
from indexer.authority import compute_authority
from search.engine import SearchEngine, parse_query

PASS = 0

def check(name, cond, detail=""):
    global PASS
    ok = bool(cond)
    if ok:
        PASS += 1
    log("[%s] %s%s" % ("OK" if ok else "FAIL", name, ("  " + str(detail)) if detail else ""))
    assert ok, name


log("========== A. URL 规范化 / 追踪参数清洗 ==========")
_n = URLFrontier.normalize_url
cases = [
    ("https://a.com/page?utm_source=x&id=5&utm_campaign=c", "https://a.com/page?id=5"),
    ("https://A.com/Path/#frag", "https://a.com/Path"),
    ("https://a.com/x?fbclid=abc&ref=1&msclkid=m", "https://a.com/x"),
    ("https://a.com/?spm=1&source=app", "https://a.com/"),
]
for src, want in cases:
    got = _n(src)
    check("URL清洗 %s" % src, got == want, "%s (期望 %s)" % (got, want))


log("========== B. 断点续爬 ==========")
tmp = tempfile.mkdtemp()
f = URLFrontier(["https://a.com/"])
f.add_sync("https://b.com/x?utm_source=t&id=5", depth=1)
f.add_sync("https://a.com/page?utm_source=z", depth=1)
f._total_crawled = 3
path = os.path.join(tmp, "frontier.json")
f.save(path)
g = URLFrontier(None)
g.load(path)
check("续爬 roundtrip pending", g.pending_count == f.pending_count,
      "pending=%s==%s" % (g.pending_count, f.pending_count))
check("续爬 roundtrip crawled", g.total_crawled == 3)
check("续爬 roundtrip seen", URLFrontier.url_fingerprint("https://a.com/") in g._seen, "seen=%s" % sorted(g._seen))
check("续爬 roundtrip 已清洗 URL", any("?id=5" in e.url for e in g._queue) or any("id=5" in e.url for e in g._queue),
      [e.url for e in g._queue])

# XiniuBot 构造: resume 读取真实 frontier.json
from main import XiniuBot
bot_r = XiniuBot(seed_urls=["https://example.com/"], max_pages=1, max_depth=0, resume=True)
log("  bot resume=True -> _resumed=%s pending=%s 累计已抓=%s (取决于本地 frontier.json)" % (
    bot_r._resumed, bot_r.frontier.pending_count, bot_r._crawled_count))
bot_f = XiniuBot(seed_urls=["https://example.com/"], max_pages=1, max_depth=0, resume=False)
check("resume=False 不计入已抓取", bot_f._crawled_count == 0, "crawled=%s" % bot_f._crawled_count)
check("resume=True 计入累计", bot_r._crawled_count == (bot_r.frontier.total_crawled if bot_r._resumed else 0))


log("========== C. SimHash 内容去重 ==========")
idx_c = InvertedIndex(index_dir=tempfile.mkdtemp())
e_c = SearchEngine(idx_c)
_tok0 = ["人工智能", "产业", "大会", "智能", "计算", "数据", "大会", "演讲"]
e_c.add_page("https://c.com/1", "北京人工智能大会开幕", "", " ".join(_tok0), _tok0)
mirror = ["人工智能", "产业", "大会", "智能", "计算", "数据", "大会", "演讲", "智能"]
check("近似重复被检出", e_c.check_duplicate(mirror) is not None)
check("无重复不误报", e_c.check_duplicate(["美食", "旅游", "烤鸭", "小吃"]) is None)


log("========== D. 字段加权排序 + 链接权威分 ==========")
idx_d = InvertedIndex(index_dir=tempfile.mkdtemp())
e_d = SearchEngine(idx_d)
# 文档0: 标题含"北京", 正文不含; 文档1: 正文含"北京"
e_d.add_page("https://d.com/title-hit", "北京美食地图", "", "城市 地图 指南 城市", ["城市", "地图", "指南", "城市"])
e_d.add_page("https://d.com/body-hit", "城市指南", "", "北京 美食 推荐 城市 北京 美食", ["北京", "美食", "推荐", "城市", "北京", "美食"])
r, total = e_d.search("北京")
check("字段加权: 标题命中排前", r[0].url == "https://d.com/title-hit",
      "top=%s score=%.4f" % (r[0].url, r[0].score))

# 权威分: 互相链接的文档 PageRank 归一化到 [0,1]
idx_a = InvertedIndex(index_dir=tempfile.mkdtemp())
e_a = SearchEngine(idx_a)
e_a.add_page("https://a.com/0", "首页", "", "北京 人工智能 大会 北京 人工智能 数据 计算", ["北京", "人工智能", "大会", "北京", "人工智能", "数据", "计算"], outlinks=["https://a.com/1", "https://a.com/2"])
e_a.add_page("https://a.com/1", "新闻", "", "北京 人工智能 报告 北京 人工智能 规模", ["北京", "人工智能", "报告", "北京", "人工智能", "规模"], outlinks=["https://a.com/2"])
e_a.add_page("https://a.com/2", "文章", "", "北京 美食 旅游 美食 北京", ["北京", "美食", "旅游", "美食", "北京"], outlinks=["https://a.com/0"])
stats = compute_authority(e_a.index)
auths = [getattr(e_a.index.get_document(i), "authority", -1) for i in range(3)]
check("权威分写入", all(a >= 0 for a in auths), "auths=%s" % auths)
check("权威分范围 [0,1]", all(0 <= a <= 1.0001 for a in auths), "min=%.4f max=%.4f" % (min(auths), max(auths)))
# 相对权威: 入链最多的 doc2 应不低于 doc1
check("入链多者权威更高", auths[2] >= auths[1] - 1e-9, "auth2=%.4f auth1=%.4f" % (auths[2], auths[1]))


log("========== E. 高级查询语法 ==========")
q = parse_query('人工智能 OR 美食 site:blog.c.com -旅游')
check("语法: site", q.site == "blog.c.com", q.site)
check("语法: OR 分组", len(q.or_groups) == 2, q.or_groups)
check("语法: 排除", "旅" in q.exclude and "游" in q.exclude, q.exclude)
q2 = parse_query('"精确短语" intitle:北京')
check("语法: 短语", len(q2.phrases) == 1, q2.phrases)
check("语法: intitle", q2.intitle == "北京", q2.intitle)


log("========== F. 后台 API: RANKING/DEDUP/权威分重建/resume ==========")
import admin_server
from admin_runner import manager
import admin_config
app = admin_server.app
app.config["TESTING"] = True
c = app.test_client()
overlay_existed = os.path.isfile(admin_config.CONFIG_FILE)
c.post("/admin/login", data={"password": "testpass123"})
csrf = c.get("/admin/api/session").get_json()["csrf"]

r = c.get("/admin/api/config")
cfg = r.get_json()["config"]
check("配置含 RANKING", "RANKING" in cfg and "authority_weight" in cfg["RANKING"])
check("配置含 DEDUP", "DEDUP" in cfg and "simhash_threshold" in cfg["DEDUP"])
r = c.post("/admin/api/config", json={"DEDUP": {"simhash_threshold": 5}}, headers={"X-CSRF-Token": csrf})
check("DEDUP 阈值更新生效", r.get_json().get("ok") and config.DEDUP["simhash_threshold"] == 5)
c.post("/admin/api/config", json={"DEDUP": {"simhash_threshold": 3}}, headers={"X-CSRF-Token": csrf})

r = c.get("/admin/api/docs?page=1&size=5")
dd = r.get_json()
check("文档列表含权威字段", bool(dd["docs"]) and all("authority" in x and "simhash" in x for x in dd["docs"]),
      "docs=%s" % len(dd["docs"]))
r = c.post("/admin/api/index/rebuild-authority", json={}, headers={"X-CSRF-Token": csrf})
check("重建权威分 API", r.status_code == 200 and r.get_json().get("ok"), r.get_json())

import unittest.mock as mock
with mock.patch.object(manager, "start", return_value=(True, "ok")) as m:
    c.post("/admin/api/crawler/start", json={"max_pages": 2, "resume": False}, headers={"X-CSRF-Token": csrf})
    _, kw = m.call_args
    check("crawler start 透传 resume", kw.get("resume") is False, kw)

# 恢复覆盖层
if overlay_existed:
    log("  覆盖层原本存在, 保留")
else:
    try:
        os.remove(admin_config.CONFIG_FILE)
        log("  覆盖层为测试新建, 已删除")
    except OSError:
        pass


log("========== G. 搜索服务: 建议 / 纠错 / 缓存 / 高级语法 ==========")
import search_server
sapp = search_server.app
sapp.config["TESTING"] = True
sc = sapp.test_client()
r = sc.get("/api/search?q=python&page=1&page_size=3")
sj = r.get_json()
check("搜索API", "results" in sj and "suggestion" in sj, "total=%s" % sj.get("total"))
r = sc.get("/api/suggest?prefix=py")
check("建议API", r.get_json()["suggestions"] is not None)
r = sc.get("/api/correct?q=pythno")
check("纠错API", r.get_json() is not None)
r = sc.get("/")
hp = r.get_data(as_text=True)
check("首页含高级语法提示", "site:" in hp)
r = sc.get("/search?q=site:example.com")
check("结果页高级语法 site:", r.status_code == 200)


log("")
log("========== 汇总: %d 项断言全部通过 ==========" % PASS)
out.close()
