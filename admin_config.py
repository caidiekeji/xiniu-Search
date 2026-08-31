# -*- coding: utf-8 -*-
"""
xiniubot 管理后台 - 配置管理
============================
将可编辑配置项读写到 data/admin/config.json, 并实时覆盖 config 模块,
使修改对同一进程内的爬虫 / 搜索立即生效, 重启后由 config._load_overlay 恢复.

说明:
  - 每个可编辑项带类型与取值范围校验, 防止写入非法值.
  - SKIP_EXT 为扩展名集合, 以逗号分隔字符串存储.
"""

import json
import os

import config

CONFIG_FILE = os.path.join(config.BASE_DIR, "data", "admin", "config.json")


# ── 可编辑配置 schema ────────────────────────────────
CRAWLER_FIELDS = {
    "max_concurrent":   {"type": "int",   "min": 1,     "max": 200,       "label": "最大并发数",     "hint": "同时进行的下载任务数"},
    "max_depth":        {"type": "int",   "min": 1,     "max": 20,        "label": "最大抓取深度",   "hint": "从种子算起的链接层级"},
    "max_pages":        {"type": "int",   "min": 1,     "max": 10000000,  "label": "最大抓取页数",   "hint": "单次任务抓取上限"},
    "timeout":          {"type": "float", "min": 1.0,   "max": 300.0,     "label": "请求超时(秒)",  "hint": "单次 HTTP 请求超时"},
    "politeness_delay": {"type": "float", "min": 0.0,   "max": 60.0,      "label": "礼貌延迟(秒)",  "hint": "同一域名两次请求最小间隔"},
    "max_retries":      {"type": "int",   "min": 0,     "max": 10,        "label": "最大重试次数",   "hint": "5xx / 超时重试"},
    "max_url_length":   {"type": "int",   "min": 64,    "max": 8192,      "label": "URL 最大长度",   "hint": "超长 URL 直接跳过"},
    "max_page_size":    {"type": "int",   "min": 1024,  "max": 104857600, "label": "单页最大字节",   "hint": "超出则丢弃该页"},
    "respect_robots":   {"type": "bool",  "label": "遵守 robots.txt",     "hint": "开启后按站点 robots 规则抓取"},
    "user_agent":       {"type": "str",   "maxlen": 200, "label": "User-Agent", "hint": "请求携带的 UA 标识"},
}

SEARCH_FIELDS = {
    "page_size":     {"type": "int", "min": 1, "max": 100, "label": "每页结果数",   "hint": "搜索页面默认每页条数"},
    "snippet_chars": {"type": "int", "min": 50, "max": 2000, "label": "摘要长度",   "hint": "结果摘要截取字符数"},
}

TOKENIZER_FIELDS = {
    "max_word_len": {"type": "int", "min": 2, "max": 16, "label": "最大词长",       "hint": "词典最长匹配长度"},
    "hmm_enabled":  {"type": "bool", "label": "HMM 未登录词识别", "hint": "对未收录词启用隐马尔可夫切分"},
}

RANKING_FIELDS = {
    "title_weight":       {"type": "float", "min": 0.0, "max": 10.0, "label": "标题权重",       "hint": "标题命中词的加权系数"},
    "description_weight": {"type": "float", "min": 0.0, "max": 10.0, "label": "描述权重",       "hint": "描述命中词的加权系数"},
    "body_weight":        {"type": "float", "min": 0.0, "max": 10.0, "label": "正文权重",       "hint": "正文命中词的加权系数"},
    "authority_weight":   {"type": "float", "min": 0.0, "max": 10.0, "label": "链接权威权重",   "hint": "PageRank 分数的融合强度, 0 关闭"},
    "time_decay_days":    {"type": "float", "min": 0.0, "max": 3650.0, "label": "时间衰减(天)", "hint": "按抓取时间衰减, 0 关闭"},
}

DEDUP_FIELDS = {
    "enabled":          {"type": "bool", "label": "内容近似去重", "hint": "开启后相似内容 (SimHash) 不再重复入库"},
    "simhash_threshold": {"type": "int", "min": 0, "max": 16, "label": "相似阈值", "hint": "海明距离 <= 阈值判为重复"},
}

# 模块级标量 (非 dict 配置)
SCALAR_FIELDS = {
    "BM25_K1": {"type": "float", "min": 0.1, "max": 5.0, "label": "BM25 k1", "hint": "词频饱和度参数"},
    "BM25_B":  {"type": "float", "min": 0.0, "max": 1.5, "label": "BM25 b",  "hint": "文档长度归一化参数"},
}

FIELD_GROUPS = [
    {"key": "CRAWLER",   "title": "爬虫",        "fields": CRAWLER_FIELDS},
    {"key": "SEARCH",    "title": "搜索",        "fields": SEARCH_FIELDS},
    {"key": "RANKING",   "title": "排序加权",    "fields": RANKING_FIELDS},
    {"key": "DEDUP",     "title": "内容去重",    "fields": DEDUP_FIELDS},
    {"key": "TOKENIZER", "title": "分词",        "fields": TOKENIZER_FIELDS},
    {"key": "SCALAR",    "title": "排序参数",    "fields": SCALAR_FIELDS},
]


# ── 校验辅助 ─────────────────────────────────────────
def _coerce(spec, raw):
    t = spec["type"]
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if t == "int":
        return int(str(raw).strip())
    if t == "float":
        return float(str(raw).strip())
    return str(raw)


def _validate(spec, value):
    t = spec["type"]
    if t in ("int", "float"):
        mn, mx = spec.get("min"), spec.get("max")
        if mn is not None and value < mn:
            return False, "不能小于 %s" % mn
        if mx is not None and value > mx:
            return False, "不能大于 %s" % mx
    elif t == "str":
        if spec.get("maxlen") and len(value) > spec["maxlen"]:
            return False, "长度不能超过 %s" % spec["maxlen"]
        if not str(value).strip():
            return False, "不能为空"
    return True, ""


# ── 读取 / 保存 ──────────────────────────────────────
def current_values():
    """返回当前配置值, 供管理界面回显."""
    out = {}
    for g in FIELD_GROUPS:
        key = g["key"]
        vals = {}
        for name in g["fields"]:
            if key == "SCALAR":
                vals[name] = getattr(config, name)
            else:
                vals[name] = getattr(config, key)[name]
        out[key] = vals
    out["SKIP_EXT"] = sorted(config.SKIP_EXT)
    return out


def save():
    """把当前 config 值写入覆盖层文件 (供重启后加载)."""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    data = {}
    for g in FIELD_GROUPS:
        key = g["key"]
        if key == "SCALAR":
            for name in g["fields"]:
                data[name] = getattr(config, name)
        else:
            d = {}
            for name in g["fields"]:
                d[name] = getattr(config, key)[name]
            data[key] = d
    data["SKIP_EXT"] = sorted(config.SKIP_EXT)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update(patch):
    """
    校验并应用配置修改, 成功后持久化.
    patch 形如 {"CRAWLER": {"max_concurrent": 30}, "SKIP_EXT": "pdf,doc"}
    返回 {"ok": bool, "errors": [...]}.
    """
    if not isinstance(patch, dict):
        return {"ok": False, "errors": ["请求体格式错误"]}

    errors = []
    new_vals = {}

    for g in FIELD_GROUPS:
        key = g["key"]
        group_patch = patch.get(key)
        if not isinstance(group_patch, dict):
            continue
        new_vals[key] = {}
        for name, spec in g["fields"].items():
            if name not in group_patch:
                continue
            try:
                value = _coerce(spec, group_patch[name])
            except (TypeError, ValueError):
                errors.append("%s: 格式不正确" % spec["label"])
                continue
            ok, msg = _validate(spec, value)
            if not ok:
                errors.append("%s: %s" % (spec["label"], msg))
                continue
            new_vals[key][name] = value

    # SKIP_EXT: 接受逗号分隔字符串或数组
    new_skip = None
    if "SKIP_EXT" in patch:
        raw = patch["SKIP_EXT"]
        if isinstance(raw, (list, tuple)):
            items = [str(x).strip().lstrip(".") for x in raw if str(x).strip()]
        else:
            raw = str(raw).replace("，", ",")
            items = [x.strip().lstrip(".") for x in raw.split(",") if x.strip()]
        new_skip = [x for x in items if x]

    if errors:
        return {"ok": False, "errors": errors}

    # 应用
    for g in FIELD_GROUPS:
        key = g["key"]
        if key not in new_vals:
            continue
        for name, value in new_vals[key].items():
            if key == "SCALAR":
                setattr(config, name, value)
            else:
                getattr(config, key)[name] = value

    if new_skip is not None:
        config.SKIP_EXT = set("." + x for x in new_skip)

    save()
    return {"ok": True, "errors": []}
