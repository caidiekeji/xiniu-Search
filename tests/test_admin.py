# -*- coding: utf-8 -*-
"""xiniubot 管理后台 全链路测试。
运行: python tests/test_admin.py (在项目根目录下)
注意: 会真实爬取 example.com 并修改本地索引/配置覆盖层/种子, 请勿在生产数据上运行。
"""
import io, os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
out = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_out.txt"), "w", encoding="utf-8")

def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg)
    out.write(msg + "\n")
    out.flush()

import config
config.ADMIN["password"] = "testpass123"   # 固定测试密码

import admin_config
import admin_server
from admin_runner import manager
from search.engine import SearchEngine

app = admin_server.app
app.config["TESTING"] = True
client = app.test_client()

log("=== 1. 认证 ===")
r = client.get("/admin/api/status")
log("未登录访问 API ->", r.status_code, "(期望 401)")
r = client.get("/admin/")
log("未登录访问页面 ->", r.status_code, "(期望 302 跳登录)")
r = client.post("/admin/login", data={"password": "wrong"})
log("错误密码 ->", r.status_code, "含'密码错误':", "密码错误".encode() in r.data)
r = client.post("/admin/login", data={"password": "testpass123"})
log("正确密码 ->", r.status_code, "(期望 302)")
s = client.get("/admin/api/session").get_json()
CSRF = s.get("csrf", "")
log("session: auth_enabled=%s logged_in=%s csrf_len=%s" % (s["auth_enabled"], s["logged_in"], len(CSRF)))

log("")
log("=== 2. 配置读取/保存/校验 ===")
r = client.get("/admin/api/config")
cfg = r.get_json()["config"]
log("读取配置: CRAWLER.max_concurrent=%s, BM25_K1=%s, SKIP_EXT 数=%s" % (cfg["CRAWLER"]["max_concurrent"], cfg["SCALAR"]["BM25_K1"], len(cfg["SKIP_EXT"])))

# 非法值
r = client.post("/admin/api/config", json={"CRAWLER": {"max_concurrent": -5}}, headers={"X-CSRF-Token": CSRF})
log("非法并发=-5 ->", r.status_code, r.get_json())
# 合法修改
r = client.post("/admin/api/config", json={"CRAWLER": {"max_concurrent": 12, "politeness_delay": 0.5}, "SEARCH": {"page_size": 8}}, headers={"X-CSRF-Token": CSRF})
log("合法修改 ->", r.status_code, r.get_json()["ok"])
log("config 模块已更新 max_concurrent=%s politeness_delay=%s" % (config.CRAWLER["max_concurrent"], config.CRAWLER["politeness_delay"]))
log("覆盖层文件存在:", os.path.isfile(admin_config.CONFIG_FILE))
with open(admin_config.CONFIG_FILE, encoding="utf-8") as f:
    disk = json.load(f)
log("覆盖层磁盘值 max_concurrent=%s" % disk["CRAWLER"]["max_concurrent"])

# CSRF 缺失
r = client.post("/admin/api/config", json={"CRAWLER": {"max_concurrent": 5}})
log("无 CSRF 修改 ->", r.status_code, "(期望 403)")

log("")
log("=== 3. 种子管理 ===")
r = client.post("/admin/api/seeds", json={"urls": ["https://example.com/", "https://www.python.org/", "https://example.com/"]}, headers={"X-CSRF-Token": CSRF})
log("批量添加 ->", r.get_json())
r = client.post("/admin/api/seeds", json={"urls": ["https://bad-url"]}, headers={"X-CSRF-Token": CSRF})
log("非法 URL(非http) -> added=", r.get_json()["added"], "(期望不加入)")
r = client.delete("/admin/api/seeds", json={"urls": ["https://example.com/"]}, headers={"X-CSRF-Token": CSRF})
log("删除 ->", r.get_json()["seeds"])

log("")
log("=== 4. 爬虫启动/停止/状态 ===")
ok, msg = manager.start(seeds=["https://example.com/"], max_pages=2, max_depth=1, concurrency=2)
log("manager.start ->", ok, msg)
if ok:
    deadline = time.time() + 45
    while time.time() < deadline:
        st = manager.status()
        if st["status"] in ("stopped", "error") and st["finished_at"]:
            break
        time.sleep(1.5)
    st = manager.status()
    log("最终状态 ->", st["status"], "已抓取:", st["crawled"], "错误:", st["errors"], "索引:", st["index"])
    log("最近抓取条数:", len(st["recent"]))
    # API 层面查询
    r = client.get("/admin/api/status")
    js = r.get_json()
    log("API status -> status=%s crawled=%s queue=%s" % (js["status"], js["crawled"], js["queue"]))
    r = client.get("/admin/api/queue")
    qj = r.get_json()
    log("队列API -> pending=%s seen=%s 域名分布条数=%s" % (qj["pending"], qj["seen_size"], len(qj["domain_counts"])))
else:
    log("启动失败, 跳过")

log("")
log("=== 5. 索引管理 ===")
r = client.get("/admin/api/index")
log("索引统计 ->", r.get_json()["stats"])
r = client.get("/admin/api/docs?size=5")
dj = r.get_json()
log("浏览文档 -> total=%s 返回=%s" % (dj["total"], len(dj["docs"])))
if dj["docs"]:
    did = dj["docs"][0]["doc_id"]
    r = client.get("/admin/api/docs/%s" % did)
    log("文档详情 #%s -> title=%r" % (did, r.get_json()["doc"]["title"][:20]))
    # 删除再恢复(重建一条)
    r = client.delete("/admin/api/docs/%s" % did, headers={"X-CSRF-Token": CSRF})
    log("删除文档 #%s -> %s" % (did, r.get_json()))
r = client.get("/admin/api/docs?q=example")
log("按词搜索 'example' -> total=%s" % r.get_json()["total"])

log("")
log("=== 6. 任务历史 ===")
r = client.get("/admin/api/history")
hj = r.get_json()
log("任务历史条数 ->", len(hj["tasks"]))
if hj["tasks"]:
    t = hj["tasks"][0]
    log("最新任务 -> status=%s crawled=%s 种子=%s" % (t["status"], t["crawled"], t["params"]["seeds"]))

log("")
log("=== 7. 日志 ===")
r = client.get("/admin/api/logs?lines=50")
lj = r.get_json()
log("日志条数 ->", len(lj["logs"]))
if lj["logs"]:
    log("日志首行 ->", lj["logs"][-1][:100])
r = client.get("/admin/api/logs?level=ERROR")
log("ERROR 过滤条数 ->", len(r.get_json()["logs"]))

log("")
log("=== 8. 配置覆盖层跨进程生效(模拟重启) ===")
# 重新 import config 验证覆盖层生效
import importlib
importlib.reload(config)
log("重载 config 后 max_concurrent=%s page_size=%s (应保持修改值)" % (config.CRAWLER["max_concurrent"], config.SEARCH["page_size"]))

log("")
log("=== 9. 页面渲染 ===")
r = client.get("/admin/")
log("管理页 ->", r.status_code, "含控制台框架:", "管理控制台".encode() in r.data or b"xiniubot" in r.data)
r = client.get("/static/admin.css")
log("CSS ->", r.status_code)
r = client.get("/static/admin.js")
log("JS ->", r.status_code)

log("")
log("=== 全部测试完成 ===")
out.close()
