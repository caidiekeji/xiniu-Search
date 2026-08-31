/* xiniubot 管理后台 - 前端逻辑 (原生 JS SPA) */
(function () {
  "use strict";

  // ── 工具 ──────────────────────────────────────────
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var CSRF = "";

  function esc(v) {
    if (v === null || v === undefined) return "";
    return String(v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtTime(ts) {
    if (!ts) return "-";
    var d = new Date(ts * 1000);
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function fmtDuration(sec) {
    if (!sec) return "-";
    sec = Math.round(sec);
    if (sec < 60) return sec + " 秒";
    var m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return m + " 分 " + s + " 秒";
    var h = Math.floor(m / 60); m = m % 60;
    return h + " 时 " + m + " 分";
  }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    opts.headers["X-CSRF-Token"] = CSRF;
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "响应解析失败" }; });
    });
  }

  function toast(msg, type) {
    var wrap = $(".toast-wrap");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "toast-wrap";
      document.body.appendChild(wrap);
    }
    var t = document.createElement("div");
    t.className = "toast " + (type || "");
    t.textContent = msg;
    wrap.appendChild(t);
    setTimeout(function () { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(function () { t.remove(); }, 320); }, 3200);
  }

  function emptyHtml(msg) {
    return '<div class="empty">' + esc(msg || "暂无数据") + "</div>";
  }

  // ── 状态常量 ──────────────────────────────────────
  var STATUS_MAP = {
    stopped: { text: "已停止", cls: "stopped", badge: "badge-dim" },
    starting: { text: "启动中", cls: "running", badge: "badge-blue" },
    running: { text: "运行中", cls: "running", badge: "badge-green" },
    stopping: { text: "停止中", cls: "stopping", badge: "badge-amber" },
    error: { text: "异常", cls: "error", badge: "badge-red" },
  };

  function statusMeta(s) { return STATUS_MAP[s] || { text: s || "未知", cls: "", badge: "badge-dim" }; }

  // ── 全局导航 ──────────────────────────────────────
  var VIEWS = {};
  var currentView = "dashboard";
  var POLL = null;

  function switchView(name) {
    currentView = name;
    $all(".nav-item[data-view]").forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-view") === name);
    });
    var titles = { dashboard: "仪表盘", control: "爬虫控制", seeds: "种子管理", queue: "队列监控", index: "索引管理", history: "任务历史", logs: "系统日志" };
    $("#page-title").textContent = titles[name] || name;
    var view = $("#content");
    if (VIEWS[name]) {
      VIEWS[name](view);
    } else {
      view.innerHTML = emptyHtml("视图不存在");
    }
    if (POLL) { clearInterval(POLL); POLL = null; }
    if (name === "dashboard") POLL = setInterval(function () { VIEWS.dashboard && VIEWS.dashboard.refresh && VIEWS.dashboard.refresh(); }, 2500);
    if (name === "logs") POLL = setInterval(function () { VIEWS.logs && VIEWS.logs.refresh && VIEWS.logs.refresh(); }, 2500);
  }

  function topStatus() {
    api("/admin/api/status").then(function (d) {
      var meta = statusMeta(d.status);
      var el = $("#top-status");
      el.className = "crawler-status " + meta.cls;
      el.innerHTML = '<i class="dot"></i> ' + esc(meta.text) + (d.running_seconds ? " · " + esc(fmtDuration(d.running_seconds)) : "");
    });
  }

  // ── 仪表盘 ────────────────────────────────────────
  var trend = [];

  VIEWS.dashboard = function (view) {
    view.innerHTML =
      '<div class="grid grid-4" id="dash-metrics"></div>' +
      '<div class="grid grid-3" style="margin-top:1rem">' +
      '  <div class="card" style="grid-column: span 2"><div class="card-title">抓取趋势 (近 2 分钟)</div><canvas id="trend-canvas" height="120"></canvas></div>' +
      '  <div class="card"><div class="card-title">最近抓取</div><div id="dash-recent"></div></div>' +
      "</div>";
    VIEWS.dashboard.refresh = function () {
      api("/admin/api/status").then(function (d) {
        renderDashboard(d);
      });
    };
    VIEWS.dashboard.refresh();
  };

  function renderDashboard(d) {
    var meta = statusMeta(d.status);
    var idx = d.index || {};
    var maxPages = (d.params && d.params.max_pages) || 0;
    var pct = maxPages ? Math.min(100, Math.round(d.crawled / maxPages * 100)) : 0;
    var rate = d.running_seconds > 0 ? (d.crawled / d.running_seconds).toFixed(2) : "0.00";

    var cards = [
      { label: "爬虫状态", value: meta.text, sub: d.last_error ? "错误: " + esc(d.last_error) : "", cls: meta.cls === "running" ? "green" : (meta.cls === "error" ? "red" : "") },
      { label: "已抓取 / 上限", value: esc(d.crawled) + " / " + esc(maxPages), sub: "速率 " + rate + " 页/秒", cls: "accent" },
      { label: "错误", value: esc(d.errors), sub: "请求失败计数", cls: d.errors > 0 ? "red" : "" },
      { label: "待爬队列", value: esc(d.queue), sub: "累计入队 " + esc(d.total_added), cls: "blue" },
      { label: "索引文档", value: esc(idx.total_docs || 0), sub: "词汇量 " + esc(idx.vocabulary_size || 0) },
      { label: "平均文档长度", value: esc(idx.avg_doc_length || 0), sub: "总词数 " + esc(idx.total_word_count || 0) },
      { label: "种子数", value: esc(d.seeds_count), sub: "已配种子 URL" },
      { label: "运行时长", value: esc(fmtDuration(d.running_seconds)), sub: "开始于 " + esc(fmtTime(d.started_at)) },
    ];
    $("#dash-metrics").innerHTML = cards.map(function (c) {
      return '<div class="metric ' + c.cls + '"><div class="label">' + c.label + '</div><div class="value">' + c.value + '</div><div class="sub">' + (c.sub || "") + "</div>" +
        (c.label === "已抓取 / 上限" ? '<div class="progress"><span style="width:' + pct + '%"></span></div>' : "") +
        "</div>";
    }).join("");

    // 趋势
    var now = Date.now();
    trend.push({ t: now, crawled: d.crawled, queue: d.queue });
    trend = trend.filter(function (p) { return now - p.t < 125000; });
    drawTrend();

    // 最近抓取
    var recent = d.recent || [];
    $("#dash-recent").innerHTML = recent.length
      ? '<table><thead><tr><th>URL</th><th>词</th><th>链</th></tr></thead><tbody>' +
        recent.slice(0, 12).map(function (r) {
          return "<tr><td><div>" + esc(r.title || r.url) + '</div><div class="sub-text">' + esc(r.url) + "</div></td><td>" + esc(r.tokens) + "</td><td>" + esc(r.links) + "</td></tr>";
        }).join("") + "</tbody></table>"
      : emptyHtml("爬虫运行后将在这里显示最近抓取的页面");
  }

  function drawTrend() {
    var canvas = $("#trend-canvas");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var W = canvas.width = canvas.offsetWidth || 600;
    var H = canvas.height = 120;
    ctx.clearRect(0, 0, W, H);
    if (trend.length < 2) { drawEmpty(canvas, ctx, W, H, "等待采集数据..."); return; }
    var maxV = 1;
    trend.forEach(function (p) { if (p.crawled > maxV) maxV = p.crawled; });
    var padL = 34, padR = 8, padT = 8, padB = 18;
    var iw = W - padL - padR, ih = H - padT - padB;
    var minT = trend[0].t, maxT = trend[trend.length - 1].t;
    if (maxT - minT < 1) maxT = minT + 1;

    ctx.strokeStyle = "#25252a"; ctx.lineWidth = 1;
    for (var g = 0; g <= 3; g++) {
      var gy = padT + ih * g / 3;
      ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(W - padR, gy); ctx.stroke();
      ctx.fillStyle = "#5a5a60"; ctx.font = "10px monospace";
      ctx.fillText(String(Math.round(maxV * (1 - g / 3))), 4, gy + 3);
    }

    ctx.strokeStyle = "#f0b429"; ctx.lineWidth = 2; ctx.beginPath();
    trend.forEach(function (p, i) {
      var x = padL + (p.t - minT) / (maxT - minT) * iw;
      var y = padT + ih - (p.crawled / maxV) * ih;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.fillStyle = "#8b8a88"; ctx.font = "10px monospace";
    ctx.fillText(fmtTime(minT / 1000).slice(5), padL, H - 4);
    ctx.fillText(fmtTime(maxT / 1000).slice(5), W - padR - 60, H - 4);
  }

  function drawEmpty(canvas, ctx, W, H, msg) {
    ctx.fillStyle = "#5a5a60"; ctx.font = "12px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(msg, W / 2, H / 2);
    ctx.textAlign = "left";
  }

  // ── 爬虫控制 ──────────────────────────────────────
  var CONFIG_CACHE = null;

  VIEWS.control = function (view) {
    view.innerHTML =
      '<div class="card"><div class="card-title">运行状态</div><div id="ctl-status"></div></div>' +
      '<div class="card"><div class="card-title">任务参数</div>' +
      '<div class="form-grid">' +
      '  <div class="field"><label>最大页数</label><input type="number" id="ctl-max-pages" value="" min="1"></div>' +
      '  <div class="field"><label>最大深度</label><input type="number" id="ctl-max-depth" value="" min="1"></div>' +
      '  <div class="field"><label>并发数</label><input type="number" id="ctl-concurrency" value="" min="1"></div>' +
      '  <div class="field"><label>断点续爬</label><div class="check-row"><input type="checkbox" id="ctl-resume" checked> <span class="sub-text">恢复上次未完成队列</span></div></div>' +
      '  <div class="field"><label>种子来源</label><select id="ctl-seed-mode"><option value="stored">使用已配置种子</option><option value="custom">临时指定</option></select></div>' +
      '  <div class="field" style="grid-column: 1 / -1" id="ctl-seed-custom" style="display:none"><label>临时种子 (逗号分隔)</label><input type="text" id="ctl-seeds" placeholder="https://a.com,https://b.com"></div>' +
      "</div>" +
      '<div style="display:flex;gap:.6rem;margin-top:1rem">' +
      '  <button class="btn btn-success" id="ctl-start">▶ 启动爬虫</button>' +
      '  <button class="btn btn-danger" id="ctl-stop">■ 停止爬虫</button>' +
      "</div></div>" +
      '<div class="card"><div class="card-title">运行参数配置 <span class="sub-text">(保存后全局生效, 新任务立即采用)</span></div>' +
      '<div id="cfg-form"></div>' +
      '<div style="display:flex;gap:.6rem;margin-top:1rem">' +
      '  <button class="btn btn-primary" id="cfg-save">保存配置</button>' +
      '  <button class="btn" id="cfg-reload">恢复为表单值</button>' +
      "</div></div>";

    api("/admin/api/config").then(function (d) {
      if (d.ok) { CONFIG_CACHE = d.config; renderConfigForm(d.config); }
    });
    renderCtlStatus();

    $("#ctl-start").addEventListener("click", function () {
      var body = { max_pages: $("#ctl-max-pages").value, max_depth: $("#ctl-max-depth").value, concurrency: $("#ctl-concurrency").value, resume: $("#ctl-resume").checked };
      if ($("#ctl-seed-mode").value === "custom") {
        var urls = ($("#ctl-seeds").value || "").split(/[,，\n]/).map(function (s) { return s.trim(); }).filter(Boolean);
        body.seeds = urls;
      }
      api("/admin/api/crawler/start", { method: "POST", body: body }).then(function (d) {
        if (d.ok) toast(d.message, "ok"); else toast(d.error || "启动失败", "err");
        renderCtlStatus();
      });
    });
    $("#ctl-stop").addEventListener("click", function () {
      api("/admin/api/crawler/stop", { method: "POST", body: {} }).then(function (d) {
        if (d.ok) toast(d.message, "ok"); else toast(d.error || "停止失败", "err");
        renderCtlStatus();
      });
    });
    $("#ctl-seed-mode").addEventListener("change", function () {
      $("#ctl-seed-custom").style.display = this.value === "custom" ? "" : "none";
    });
    $("#cfg-save").addEventListener("click", saveConfigForm);
    $("#cfg-reload").addEventListener("click", function () {
      if (CONFIG_CACHE) renderConfigForm(CONFIG_CACHE);
      toast("已恢复为当前生效配置", "ok");
    });

    VIEWS.control.refresh = function () { renderCtlStatus(); };
  };

  function renderCtlStatus() {
    api("/admin/api/status").then(function (d) {
      var meta = statusMeta(d.status);
      var params = d.params || {};
      var html =
        '<div style="display:flex;align-items:center;gap:1rem;flex-wrap:wrap">' +
        '<span class="badge ' + meta.badge + '" style="font-size:.85rem">' + esc(meta.text) + "</span>" +
        "<span class='sub-text'>已抓取 <b>" + esc(d.crawled) + "</b> · 错误 <b>" + esc(d.errors) + "</b> · 队列 <b>" + esc(d.queue) + "</b> · 运行 " + esc(fmtDuration(d.running_seconds)) + "</span>" +
        "</div>";
      if (d.last_error) html += '<div class="login-error" style="margin-top:.6rem">' + esc(d.last_error) + "</div>";
      if (params.seeds && params.seeds.length) {
        html += '<div class="kv" style="margin-top:.8rem"><dt>本次种子</dt><dd>' + esc(params.seeds.join(" , ")) + "</dd>" +
          "<dt>参数</dt><dd>页数 " + esc(params.max_pages) + " / 深度 " + esc(params.max_depth) + " / 并发 " + esc(params.concurrency) + (params.resume ? " / 断点续爬" : "") + "</dd></div>";
      }
      $("#ctl-status").innerHTML = html;
    });
  }

  function renderConfigForm(cfg) {
    var groups = [
      { key: "CRAWLER", title: "爬虫", fields: cfg.CRAWLER },
      { key: "SEARCH", title: "搜索", fields: cfg.SEARCH },
      { key: "RANKING", title: "排序加权", fields: cfg.RANKING },
      { key: "DEDUP", title: "内容去重", fields: cfg.DEDUP },
      { key: "TOKENIZER", title: "分词", fields: cfg.TOKENIZER },
      { key: "SCALAR", title: "排序参数", fields: cfg.SCALAR },
    ];
    var html = "";
    groups.forEach(function (g) {
      html += '<div class="section-title">' + esc(g.title) + "</div><div class='form-grid'>";
      Object.keys(g.fields).forEach(function (name) {
        var v = g.fields[name];
        var isBool = typeof v === "boolean";
        var id = "cfg-" + g.key + "-" + name;
        if (isBool) {
          html += '<div class="field"><label>' + esc(name) + '</label><div class="check-row"><input type="checkbox" id="' + id + '" ' + (v ? "checked" : "") + "></div></div>";
        } else {
          html += '<div class="field"><label>' + esc(name) + '</label><input type="' + (typeof v === "number" ? "number" : "text") + '" id="' + id + '" value="' + esc(v) + '"></div>';
        }
      });
      html += "</div>";
    });
    // SKIP_EXT
    html += '<div class="section-title">URL 过滤扩展名 <span class="sub-text">(逗号分隔)</span></div>';
    html += '<div class="field"><textarea id="cfg-SKIP_EXT" rows="3" class="mono">' + esc((cfg.SKIP_EXT || []).join(", ")) + "</textarea></div>";
    $("#cfg-form").innerHTML = html;
  }

  function saveConfigForm() {
    var typeOf = {
      max_concurrent: "int", max_depth: "int", max_pages: "int", timeout: "float",
      politeness_delay: "float", max_retries: "int", max_url_length: "int",
      max_page_size: "int", respect_robots: "bool", user_agent: "str",
      page_size: "int", snippet_chars: "int",
      title_weight: "float", description_weight: "float", body_weight: "float",
      authority_weight: "float", time_decay_days: "float",
      enabled: "bool", simhash_threshold: "int",
      max_word_len: "int", hmm_enabled: "bool",
      BM25_K1: "float", BM25_B: "float"
    };
    var groupOf = {
      max_concurrent: "CRAWLER", max_depth: "CRAWLER", max_pages: "CRAWLER", timeout: "CRAWLER",
      politeness_delay: "CRAWLER", max_retries: "CRAWLER", max_url_length: "CRAWLER",
      max_page_size: "CRAWLER", respect_robots: "CRAWLER", user_agent: "CRAWLER",
      page_size: "SEARCH", snippet_chars: "SEARCH",
      title_weight: "RANKING", description_weight: "RANKING", body_weight: "RANKING",
      authority_weight: "RANKING", time_decay_days: "RANKING",
      enabled: "DEDUP", simhash_threshold: "DEDUP",
      max_word_len: "TOKENIZER", hmm_enabled: "TOKENIZER",
      BM25_K1: "SCALAR", BM25_B: "SCALAR"
    };
    var groups = { CRAWLER: {}, SEARCH: {}, RANKING: {}, DEDUP: {}, TOKENIZER: {}, SCALAR: {} };
    Object.keys(typeOf).forEach(function (name) {
      var g = groupOf[name];
      var el = $("#cfg-" + g + "-" + name);
      if (!el) return;
      var t = typeOf[name];
      var val;
      if (t === "bool") val = el.checked;
      else if (t === "int") { val = parseInt(el.value, 10); if (isNaN(val)) return; }
      else if (t === "float") { val = parseFloat(el.value); if (isNaN(val)) return; }
      else val = el.value;
      groups[g][name] = val;
    });
    var skip = $("#cfg-SKIP_EXT");
    if (skip) groups.SKIP_EXT = skip.value;
    api("/admin/api/config", { method: "POST", body: groups }).then(function (d) {
      if (d.ok) {
        toast("配置已保存并生效", "ok");
        CONFIG_CACHE = d.config;
      } else {
        toast((d.errors || ["保存失败"]).join("; "), "err");
      }
    });
  }

  // ── 种子管理 ──────────────────────────────────────
  VIEWS.seeds = function (view) {
    view.innerHTML =
      '<div class="card"><div class="card-title">添加种子</div>' +
      '<div class="searchbar"><input type="text" id="seed-input" placeholder="单个 URL, 如 https://news.ycombinator.com"><button class="btn btn-primary" id="seed-add">添加</button></div>' +
      '<div class="field"><label>批量添加 (每行或逗号分隔一个 URL)</label><textarea id="seed-batch" rows="3" class="mono" placeholder="https://a.com\nhttps://b.com"></textarea></div>' +
      '<button class="btn" id="seed-add-batch" style="margin-top:.6rem">批量添加</button></div>' +
      '<div class="card"><div class="card-title">种子列表 <span id="seed-count" class="sub-text"></span></div><div id="seed-list"></div>' +
      '<button class="btn btn-danger btn-sm" id="seed-clear" style="margin-top:.6rem">清空全部</button></div>';
    loadSeeds();
    $("#seed-add").addEventListener("click", function () {
      var u = $("#seed-input").value.trim();
      if (!u) return;
      api("/admin/api/seeds", { method: "POST", body: { urls: [u] } }).then(function (d) {
        if (d.ok) { $("#seed-input").value = ""; renderSeedList(d.seeds); toast("已添加 " + d.added.length + " 个", "ok"); }
        else toast(d.error || "添加失败", "err");
      });
    });
    $("#seed-add-batch").addEventListener("click", function () {
      var raw = $("#seed-batch").value;
      var urls = raw.split(/[\n,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
      if (!urls.length) return;
      api("/admin/api/seeds", { method: "POST", body: { urls: urls } }).then(function (d) {
        if (d.ok) { $("#seed-batch").value = ""; renderSeedList(d.seeds); toast("新增 " + d.added.length + " 个", "ok"); }
        else toast(d.error || "添加失败", "err");
      });
    });
    $("#seed-clear").addEventListener("click", function () {
      if (!confirm("确认清空全部种子?")) return;
      api("/admin/api/seeds", { method: "DELETE", body: { urls: [] } }).then(function () {
        renderSeedList([]);
        toast("已清空", "ok");
      });
    });
  };

  function loadSeeds() {
    api("/admin/api/seeds").then(function (d) { if (d.ok) renderSeedList(d.seeds); });
  }

  function renderSeedList(seeds) {
    $("#seed-count").textContent = "共 " + seeds.length + " 个";
    $("#seed-list").innerHTML = seeds.length
      ? '<div class="tbl-wrap"><table><thead><tr><th style="width:60px">#</th><th>URL</th><th style="width:80px">操作</th></tr></thead><tbody>' +
        seeds.map(function (u, i) {
          return "<tr><td>" + (i + 1) + '</td><td><a href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(u) + '</a></td>' +
            '<td><button class="btn btn-danger btn-sm" data-del="' + esc(u) + '">删除</button></td></tr>';
        }).join("") + "</tbody></table></div>"
      : emptyHtml("还没有种子, 请在上方添加");
    $all("#seed-list [data-del]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var u = btn.getAttribute("data-del");
        api("/admin/api/seeds", { method: "DELETE", body: { urls: [u] } }).then(function (d) {
          if (d.ok) renderSeedList(d.seeds);
        });
      });
    });
  }

  // ── 队列监控 ──────────────────────────────────────
  VIEWS.queue = function (view) {
    view.innerHTML =
      '<div class="grid grid-4" id="queue-metrics"></div>' +
      '<div class="grid grid-2" style="margin-top:1rem">' +
      '  <div class="card"><div class="card-title">待爬队列 (前 200)</div><div id="queue-sample" class="tbl-wrap"></div></div>' +
      '  <div class="card"><div class="card-title">域名分布 Top 30</div><div id="queue-domains"></div></div>' +
      "</div>";
    loadQueue();
  };

  function loadQueue() {
    api("/admin/api/queue").then(function (d) {
      if (!d.ok) return;
      $("#queue-metrics").innerHTML = [
        { label: "待爬队列", value: d.pending, cls: "accent" },
        { label: "累计入队", value: d.total_added, cls: "blue" },
        { label: "已弹出", value: d.total_crawled, cls: "" },
        { label: "去重集合", value: d.seen_size, cls: "green" },
      ].map(function (m) { return '<div class="metric ' + m.cls + '"><div class="label">' + m.label + '</div><div class="value">' + m.value + "</div></div>"; }).join("");

      var sample = d.queue_sample || [];
      $("#queue-sample").innerHTML = sample.length
        ? '<table><thead><tr><th>深度</th><th>优先级</th><th>URL</th></tr></thead><tbody>' +
          sample.map(function (e) {
            return "<tr><td>" + esc(e.depth) + '</td><td class="mono">' + esc(e.priority) + '</td><td><span class="sub-text">' + esc(e.anchor) + "</span><br>" + esc(e.url) + "</td></tr>";
          }).join("") + "</tbody></table>"
        : emptyHtml(d.status === "running" ? "队列为空或正在调度" : "爬虫未运行, 无队列数据");

      var doms = d.domain_counts || [];
      var maxD = doms.reduce(function (m, x) { return Math.max(m, x[1]); }, 1);
      $("#queue-domains").innerHTML = doms.length
        ? doms.map(function (x) {
            var w = Math.round(x[1] / maxD * 100);
            return '<div class="domain-bar"><span class="name">' + esc(x[0]) + '</span><div class="track"><div class="fill" style="width:' + w + '%"></div></div><span class="num">' + x[1] + "</span></div>";
          }).join("")
        : emptyHtml("暂无队列数据");
    });
  }

  // ── 索引管理 ──────────────────────────────────────
  VIEWS.index = function (view) {
    view.innerHTML =
      '<div class="grid grid-4" id="idx-metrics"></div>' +
      '<div class="card" style="margin-top:1rem"><div class="card-title">文档检索</div>' +
      '<div class="searchbar"><input type="text" id="idx-q" placeholder="按关键词搜索已索引文档 (留空浏览全部)"><button class="btn btn-primary" id="idx-search">搜索</button></div>' +
      '<div id="idx-docs" class="tbl-wrap"></div>' +
      '<div class="pager" id="idx-pager"></div></div>' +
      '<div class="card" style="margin-top:1rem"><div class="card-title">链接权威分 (PageRank)</div>' +
      '<div class="sub-text" style="margin-bottom:.6rem">基于已索引文档间的链接关系计算 PageRank (0~1), 按"排序加权"里的权重融合进搜索结果排序。爬虫任务结束后会自动重建。</div>' +
      '<button class="btn btn-primary" id="idx-rebuild">立即重建权威分</button> <span id="idx-rebuild-result" class="sub-text"></span></div>' +
      '<div id="doc-modal-wrap"></div>';
    loadIdxMetrics();
    loadDocs(1, "");
    $("#idx-search").addEventListener("click", function () { loadDocs(1, $("#idx-q").value.trim()); });
    $("#idx-q").addEventListener("keydown", function (e) { if (e.key === "Enter") loadDocs(1, this.value.trim()); });
    $("#idx-rebuild").addEventListener("click", function () {
      var btn = this, span = $("#idx-rebuild-result");
      btn.disabled = true; span.textContent = "计算中...";
      api("/admin/api/index/rebuild-authority", { method: "POST", body: {} }).then(function (d) {
        btn.disabled = false;
        if (d.ok) { span.textContent = "完成: " + d.stats.docs + " 篇文档, 权威分范围 " + d.stats.min.toFixed(4) + " ~ " + d.stats.max.toFixed(4) + ", 索引已保存"; loadIdxMetrics(); }
        else { span.textContent = ""; toast(d.error || "重建失败", "err"); }
      });
    });
  };

  var idxPage = 1, idxSize = 20, idxTotal = 0, idxQuery = "";

  function loadIdxMetrics() {
    api("/admin/api/index").then(function (d) {
      if (!d.ok) return;
      var s = d.stats || {};
      $("#idx-metrics").innerHTML = [
        { label: "文档数", value: s.total_docs, cls: "accent" },
        { label: "词汇量", value: s.vocabulary_size, cls: "blue" },
        { label: "平均长度", value: s.avg_doc_length, cls: "" },
        { label: "总词数", value: s.total_word_count, cls: "green" },
      ].map(function (m) { return '<div class="metric ' + m.cls + '"><div class="label">' + m.label + '</div><div class="value">' + esc(m.value) + "</div></div>"; }).join("");
    });
  }

  function loadDocs(page, q) {
    idxPage = page; idxQuery = q;
    var qs = "page=" + page + "&size=" + idxSize + (q ? "&q=" + encodeURIComponent(q) : "");
    api("/admin/api/docs?" + qs).then(function (d) {
      if (!d.ok) return;
      idxTotal = d.total;
      $("#idx-docs").innerHTML = d.docs.length
        ? '<table><thead><tr><th style="width:60px">ID</th><th>标题</th><th>URL</th><th style="width:70px">词数</th><th style="width:80px">权威</th><th style="width:70px">评分</th><th style="width:130px">操作</th></tr></thead><tbody>' +
          d.docs.map(function (doc) {
            var scoreCell = doc.score !== undefined ? doc.score.toFixed(4) : "-";
            var authCell = doc.authority !== undefined ? doc.authority.toFixed(3) : "-";
            return "<tr><td class='mono'>" + doc.doc_id + '</td><td>' + esc(doc.title || "-") + '</td><td><span class="sub-text">' + esc(doc.url) + "</span></td><td>" + esc(doc.word_count) + '</td><td class="mono">' + authCell + '</td><td class="mono">' + scoreCell + '</td>' +
              '<td><button class="btn btn-sm" data-view="' + doc.doc_id + '">查看</button> <button class="btn btn-danger btn-sm" data-del="' + doc.doc_id + '">删除</button></td></tr>';
          }).join("") + "</tbody></table>"
        : emptyHtml(q ? "未找到匹配文档" : "索引为空");
      $all("#idx-docs [data-view]").forEach(function (b) { b.addEventListener("click", function () { showDoc(b.getAttribute("data-view")); }); });
      $all("#idx-docs [data-del]").forEach(function (b) { b.addEventListener("click", function () { delDoc(b.getAttribute("data-del")); }); });
      var pages = Math.max(1, Math.ceil(idxTotal / idxSize));
      $("#idx-pager").innerHTML = '<span>共 ' + idxTotal + ' 条 · 第 ' + page + '/' + pages + ' 页</span>' +
        '<button class="btn btn-sm" ' + (page <= 1 ? "disabled" : "") + ' id="pg-prev">上一页</button>' +
        '<button class="btn btn-sm" ' + (page >= pages ? "disabled" : "") + ' id="pg-next">下一页</button>';
      var prev = $("#pg-prev"), next = $("#pg-next");
      if (prev) prev.addEventListener("click", function () { loadDocs(page - 1, idxQuery); });
      if (next) next.addEventListener("click", function () { loadDocs(page + 1, idxQuery); });
    });
  }

  function showDoc(id) {
    api("/admin/api/docs/" + id).then(function (d) {
      if (!d.ok) { toast(d.error || "加载失败", "err"); return; }
      var doc = d.doc;
      var body = esc(doc.body_text || "(无正文)");
      var wrap = $("#doc-modal-wrap");
      wrap.innerHTML =
        '<div style="position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;z-index:500;padding:1rem" id="doc-modal-bg">' +
        '<div class="card" style="max-width:720px;width:100%;max-height:82vh;overflow:auto;margin:0">' +
        '<div class="card-title">文档 #' + doc.doc_id + ' <button class="btn btn-sm" id="doc-close" style="float:right">关闭</button></div>' +
        '<div class="kv"><dt>标题</dt><dd>' + esc(doc.title || "-") + '</dd><dt>URL</dt><dd><a href="' + esc(doc.url) + '" target="_blank" rel="noopener">' + esc(doc.url) + "</a></dd>" +
        "<dt>描述</dt><dd>" + esc(doc.description || "-") + "</dd><dt>词数</dt><dd>" + esc(doc.word_count) + "</dd>" +
        "<dt>权威分</dt><dd class='mono'>" + (doc.authority !== undefined ? doc.authority.toFixed(4) : "-") + "</dd>" +
        "<dt>SimHash</dt><dd class='mono'>" + esc(doc.simhash) + "</dd><dt>抓取时间</dt><dd>" + esc(fmtTime(doc.fetch_time)) + "</dd>" +
        "<dt>出链数</dt><dd>" + esc((doc.outlinks || []).length) + "</dd></div>" +
        '<div class="section-title">正文 (前 5000 字)</div><div style="font-size:.8rem;line-height:1.7;white-space:pre-wrap;word-break:break-all;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:.8rem">' + body + "</div></div></div>";
      $("#doc-close").addEventListener("click", function () { $("#doc-modal-wrap").innerHTML = ""; });
      $("#doc-modal-bg").addEventListener("click", function (e) { if (e.target === this) this.parentElement.innerHTML = ""; });
    });
  }

  function delDoc(id) {
    if (!confirm("确认删除文档 #" + id + "?")) return;
    api("/admin/api/docs/" + id, { method: "DELETE", body: {} }).then(function (d) {
      if (d.ok) { toast("已删除", "ok"); loadDocs(idxPage, idxQuery); loadIdxMetrics(); }
      else toast(d.error || "删除失败", "err");
    });
  }

  // ── 任务历史 ──────────────────────────────────────
  VIEWS.history = function (view) {
    view.innerHTML = '<div class="card"><div class="card-title">任务历史 <span class="sub-text">(最多保留 100 条)</span></div><div id="history-list" class="tbl-wrap"></div></div>';
    api("/admin/api/history").then(function (d) {
      if (!d.ok) return;
      var tasks = d.tasks || [];
      $("#history-list").innerHTML = tasks.length
        ? "<table><thead><tr><th>开始时间</th><th>结束时间</th><th>耗时</th><th>状态</th><th>已抓取</th><th>错误</th><th>参数</th><th>种子</th></tr></thead><tbody>" +
          tasks.map(function (t) {
            var meta = statusMeta(t.status);
            var params = t.params || {};
            var seeds = (params.seeds || []).slice(0, 3).join(" , ") + ((params.seeds || []).length > 3 ? " …" : "");
            return "<tr><td class='mono'>" + esc(fmtTime(t.started_at)) + '</td><td class="mono">' + esc(fmtTime(t.finished_at)) + "</td><td>" + esc(fmtDuration(t.duration)) + '</td>' +
              '<td><span class="badge ' + meta.badge + '">' + esc(meta.text) + "</span></td>" +
              "<td>" + esc(t.crawled) + "</td><td>" + esc(t.errors) + "</td>" +
              "<td><span class='sub-text'>页 " + esc(params.max_pages) + " / 深 " + esc(params.max_depth) + " / 并发 " + esc(params.concurrency) + "</span></td>" +
              '<td><span class="sub-text">' + esc(seeds || "-") + "</span></td></tr>";
          }).join("") + "</tbody></table>"
        : emptyHtml("暂无任务记录, 启动爬虫后会在这里记录");
    });
  };

  // ── 日志 ──────────────────────────────────────────
  var logLevel = "";

  VIEWS.logs = function (view) {
    view.innerHTML =
      '<div class="card"><div class="card-title">爬虫日志 (自动刷新) ' +
      '<select id="log-level" style="float:right;background:var(--surface-2);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:.2rem .5rem;font-size:.8rem">' +
      '<option value="">全部</option><option value="INFO">INFO</option><option value="WARNING">WARNING</option><option value="ERROR">ERROR</option></select></div>' +
      '<div class="log-box" id="log-box"></div></div>';
    $("#log-level").addEventListener("change", function () { logLevel = this.value; loadLogs(); });
    VIEWS.logs.refresh = function () { loadLogs(); };
    loadLogs();
  };

  function loadLogs() {
    api("/admin/api/logs?lines=300" + (logLevel ? "&level=" + logLevel : "")).then(function (d) {
      if (!d.ok) return;
      var box = $("#log-box");
      if (!box) return;
      var keep = box.scrollHeight - box.scrollTop < 200; // 接近底部时自动跟随
      box.innerHTML = (d.logs || []).map(function (line) {
        var cls = "log-line";
        if (line.indexOf("ERROR") >= 0 || line.indexOf("CRITICAL") >= 0) cls += " ERROR";
        else if (line.indexOf("WARN") >= 0) cls += " WARN";
        else if (line.indexOf("INFO") >= 0) cls += " INFO";
        else if (line.indexOf("DEBUG") >= 0) cls += " DEBUG";
        return '<div class="' + cls + '">' + esc(line) + "</div>";
      }).join("") || emptyHtml("暂无日志");
      if (keep) box.scrollTop = box.scrollHeight;
    });
  }

  // ── 时钟 ──────────────────────────────────────────
  function tickClock() {
    var el = $("#top-clock");
    if (!el) return;
    var d = new Date();
    function p(n) { return (n < 10 ? "0" : "") + n; }
    el.textContent = p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  // ── 初始化 ────────────────────────────────────────
  function init() {
    api("/admin/api/session").then(function (s) {
      if (s.auth_enabled && !s.logged_in) {
        // 未登录 → 展示登录页
        return;
      }
      CSRF = s.csrf || "";
      showApp();
    });
  }

  function showApp() {
    $("#login-view").style.display = "none";
    $("#app-view").style.display = "flex";
    tickClock();
    setInterval(tickClock, 1000);
    setInterval(topStatus, 2500);
    topStatus();

    var onHash = function () {
      var name = (location.hash || "#dashboard").replace("#", "");
      if (!VIEWS[name]) name = "dashboard";
      switchView(name);
    };
    $all(".nav-item[data-view]").forEach(function (el) {
      el.addEventListener("click", function () {
        location.hash = el.getAttribute("data-view");
      });
    });
    window.addEventListener("hashchange", onHash);
    onHash();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
