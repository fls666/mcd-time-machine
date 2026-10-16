/* 麦麦时光机 · 故事页交互（V2.0）
 *
 * 原则：
 * 1. 无脚本可用时，页面内容依然完整（章节由 Python 直接渲染，JS 只做增强）。
 * 2. 不请求网络、不引用外部库；所有行为失败都静默降级，不抛错打断阅读。
 * 3. 分享卡片在本地 Canvas 绘制并导出 PNG，不写入任何元数据，
 *    导出内容只包含用户主动勾选的字段。
 */
(function () {
  "use strict";

  var doc = document;
  var root = doc.documentElement;

  function $(id) { return doc.getElementById(id); }
  function safe(fn) { try { fn(); } catch (e) { /* 增强功能失败不影响阅读 */ } }

  // 统一用 textContent 建节点：订单里的商品名等动态文本一律按纯文本处理，
  // 绝不被当作 HTML 解析（不使用 innerHTML 拼接）
  function el(tag, text) {
    var node = doc.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function reduceMotion() {
    try {
      return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (e) { return false; }
  }

  // ------------------------------------------------------------ 数据
  var DATA = {};
  safe(function () {
    DATA = JSON.parse(($("story-data") || {}).textContent || "{}");
  });

  var CARD_W = 1080;
  var CARD_H = 1920;

  // ------------------------------------------------------------ 滚动渐显
  // 只有 JS 跑起来了才给 <html> 加 .js，配合 CSS 保证动画失效时内容仍可见
  function initReveal() {
    var items = doc.querySelectorAll(".reveal");
    if (!items.length) return;

    if (reduceMotion() || !("IntersectionObserver" in window)) {
      for (var i = 0; i < items.length; i++) items[i].classList.add("is-visible");
      root.classList.remove("js"); // 不做渐显，直接呈现
      return;
    }

    root.classList.add("js");
    var observer = new IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].isIntersecting) {
          entries[i].target.classList.add("is-visible");
          observer.unobserve(entries[i].target);
        }
      }
    }, { threshold: 0.14, rootMargin: "0px 0px -8% 0px" });

    for (var j = 0; j < items.length; j++) observer.observe(items[j]);
  }

  // ------------------------------------------------------------ 进度条与章节圆点
  function initNav() {
    var scenes = doc.querySelectorAll(".scene[id]");
    if (!scenes.length) return;
    var bar = $("progressBar");
    var dotsBox = $("dots");
    var links = [];

    if (dotsBox) {
      for (var i = 0; i < scenes.length; i++) {
        var scene = scenes[i];
        var a = doc.createElement("a");
        a.href = "#" + scene.id;
        a.setAttribute("aria-label", "第 " + (i + 1) + " 章 " + (scene.getAttribute("data-title") || ""));
        var tip = doc.createElement("span");
        tip.textContent = (i + 1) + " " + (scene.getAttribute("data-title") || "章节");
        a.appendChild(tip);
        dotsBox.appendChild(a);
        links.push(a);
      }
    }

    var ticking = false;
    function update() {
      ticking = false;
      var scrollTop = window.pageYOffset || doc.body.scrollTop || 0;
      var height = (doc.body.scrollHeight || 0) - window.innerHeight;
      if (bar) {
        bar.style.width = (height > 0 ? Math.min(100, Math.max(0, scrollTop / height * 100)) : 0) + "%";
      }
      var mid = scrollTop + window.innerHeight * 0.45;
      var activeIdx = 0;
      for (var k = 0; k < scenes.length; k++) {
        if (scenes[k].offsetTop <= mid) activeIdx = k;
      }
      for (var m = 0; m < links.length; m++) {
        if (m === activeIdx) links[m].classList.add("active");
        else links[m].classList.remove("active");
      }
    }
    function onScroll() {
      if (!ticking) {
        ticking = true;
        window.requestAnimationFrame(update);
      }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    update();
  }

  // ------------------------------------------------------------ 隐私浏览（整页打码）
  function initPrivacy() {
    var btn = $("privToggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var on = btn.getAttribute("aria-pressed") === "true";
      on = !on;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.textContent = on ? "隐私浏览：开（已打码）" : "隐私浏览：关";
      if (on) root.classList.add("story-privacy");
      else root.classList.remove("story-privacy");
    });
  }

  // ------------------------------------------------------------ 条形图入场
  function initBars() {
    var fills = doc.querySelectorAll(".fill[data-w]");
    if (!fills.length) return;
    if (!("IntersectionObserver" in window) || reduceMotion()) {
      for (var i = 0; i < fills.length; i++) {
        fills[i].style.width = fills[i].getAttribute("data-w") + "%";
      }
      return;
    }
    var ob = new IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].isIntersecting) {
          entries[i].target.style.width = entries[i].target.getAttribute("data-w") + "%";
          ob.unobserve(entries[i].target);
        }
      }
    }, { threshold: 0.25 });
    for (var j = 0; j < fills.length; j++) ob.observe(fills[j]);
  }

  // ------------------------------------------------------------ 分享卡片绘制
  function font(size, weight) {
    return (weight || 400) + " " + size + 'px -apple-system, BlinkMacSystemFont, "PingFang SC",' +
      ' "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif';
  }

  /* 卡片内边距固定 84px，所有可绘制内容的硬边界都在这个区间内 */
  var PAD = 84;
  var INNER = CARD_W - PAD * 2;      // 912
  var FOOT_TOP = CARD_H - 260;       // 页脚上沿，正文块不得越过

  /* 单行截断：超出 maxWidth 时逐字回退并补省略号。
   * 商品名 / 门店名长度不可控，直接 fillText 会溢出画布或压到相邻元素上。 */
  function clip(ctx, text, maxWidth) {
    var s = String(text == null ? "" : text);
    if (!s || maxWidth <= 0) return "";
    if (ctx.measureText(s).width <= maxWidth) return s;
    var ell = "…";
    var ellW = ctx.measureText(ell).width;
    var out = "";
    for (var i = 0; i < s.length; i++) {
      var next = out + s[i];
      if (ctx.measureText(next).width + ellW > maxWidth) break;
      out = next;
    }
    return out ? out + ell : "";
  }

  function roundRect(ctx, x, y, w, h, r) {
    var rr = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + rr, y);
    ctx.lineTo(x + w - rr, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + rr);
    ctx.lineTo(x + w, y + h - rr);
    ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
    ctx.lineTo(x + rr, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - rr);
    ctx.lineTo(x, y + rr);
    ctx.quadraticCurveTo(x, y, x + rr, y);
    ctx.closePath();
  }

  /* 中英混排换行：CJK 逐字断行，连续 ASCII 视为整体，避免单词被劈开。
   * 超出 maxLines 时末行补省略号——宁可明示截断，也不静默吞掉内容。 */
  function wrap(ctx, text, maxWidth, maxLines) {
    var lines = [];
    var line = "";
    var s = String(text == null ? "" : text);
    for (var i = 0; i < s.length; i++) {
      var ch = s[i];
      if (ctx.measureText(line + ch).width <= maxWidth || line === "") {
        line += ch;
      } else {
        lines.push(line);
        line = ch;
      }
    }
    if (line) lines.push(line);
    if (maxLines && lines.length > maxLines) {
      var kept = lines.slice(0, maxLines);
      var last = kept[maxLines - 1];
      kept[maxLines - 1] = clip(ctx, last + "…", maxWidth) || last;
      return kept;
    }
    return lines;
  }

  function drawWrapped(ctx, text, x, y, maxWidth, lineHeight, maxLines) {
    var lines = wrap(ctx, text, maxWidth, maxLines);
    for (var i = 0; i < lines.length; i++) {
      ctx.fillText(lines[i], x, y + i * lineHeight);
    }
    return y + lines.length * lineHeight;
  }

  /* 关键词胶囊：单个词先按可用宽度截断，再据此算胶囊宽度，
   * 保证任何长度都不会顶出卡片左右边界。 */
  function chip(ctx, text, x, y, padX, height, maxWidth) {
    ctx.font = font(34, 600);
    var label = clip(ctx, text, Math.max(0, (maxWidth || INNER) - padX * 2));
    var w = ctx.measureText(label).width + padX * 2;
    ctx.fillStyle = "rgba(255,255,255,.16)";
    roundRect(ctx, x, y, w, height, height / 2);
    ctx.fill();
    ctx.fillStyle = "#FFFFFF";
    ctx.textBaseline = "middle";
    ctx.fillText(label, x + padX, y + height / 2 + 1);
    ctx.textBaseline = "alphabetic";
    return w;
  }

  function drawCard(canvas, cfg) {
    var ctx = canvas.getContext("2d");
    if (!ctx) return;
    canvas.width = CARD_W;
    canvas.height = CARD_H;

    var g = ctx.createLinearGradient(0, 0, CARD_W, CARD_H);
    g.addColorStop(0, "#DA291C");
    g.addColorStop(1, "#93150C");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, CARD_W, CARD_H);

    ctx.fillStyle = "rgba(255,199,44,.22)";
    ctx.beginPath();
    ctx.arc(CARD_W - 90, 120, 300, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(60, CARD_H - 140, 220, 0, Math.PI * 2);
    ctx.fill();

    var white = "#FFFFFF";
    var soft = "rgba(255,255,255,.78)";

    // 头部
    ctx.fillStyle = soft;
    ctx.font = font(34, 500);
    ctx.fillText("麦麦时光机 · 我的年度回忆", PAD, 168);

    ctx.fillStyle = white;
    ctx.font = font(78, 800);
    ctx.fillText("我的年度回忆", PAD, 272);

    ctx.fillStyle = "rgba(255,255,255,.30)";
    ctx.fillRect(PAD, 312, 300, 4);

    // 口味人格
    var panelY = 386;
    ctx.fillStyle = "rgba(255,255,255,.12)";
    roundRect(ctx, PAD, panelY, INNER, 470, 36);
    ctx.fill();

    ctx.fillStyle = soft;
    ctx.font = font(32, 500);
    ctx.fillText("我的口味人格", 132, panelY + 78);

    ctx.fillStyle = "#FFC72C";
    ctx.font = font(86, 800);
    // 最多两行；截断时末行补省略号，不会溢出面板
    var personaLines = wrap(ctx, cfg.persona, INNER - 96, 2);
    for (var i = 0; i < personaLines.length; i++) {
      ctx.fillText(personaLines[i], 132, panelY + 186 + i * 100);
    }

    ctx.fillStyle = "rgba(255,255,255,.90)";
    ctx.font = font(34, 400);
    var descTop = panelY + 186 + personaLines.length * 100 + 30;
    drawWrapped(ctx, cfg.personaDesc, 132, descTop, INNER - 96, 48, 3);

    // 年度关键词
    ctx.fillStyle = soft;
    ctx.font = font(32, 500);
    ctx.fillText("年度关键词", PAD, 950);

    // 先排版（不绘制），拿到关键词块的实际高度，再据此决定下方字段面板的位置，
    // 保证面板底部不会压到页脚、也不会越出画布下沿
    var words = cfg.keywords && cfg.keywords.length ? cfg.keywords : ["还在探索中"];
    var layout = [];
    var kx = PAD;
    var ky = 1000;
    ctx.font = font(34, 600);
    for (var w = 0; w < words.length && w < 4; w++) {
      var word = String(words[w]);
      var label = clip(ctx, word, INNER - 56);
      var need = ctx.measureText(label).width + 56;
      if (kx + need > CARD_W - PAD) { kx = PAD; ky += 84; }
      layout.push({ text: word, x: kx, y: ky });
      kx += need + 18;
    }
    var rows = cfg.rows || [];
    var panelH = 78 * rows.length + 74;
    var panelTop = ky + 150 - 56;
    // 布局边界：整体上移，最多上移到关键词标题下方，绝不越过画布
    var overflow = panelTop + panelH - FOOT_TOP;
    if (overflow > 0) {
      var shift = Math.min(overflow, ky - 1040);
      if (shift > 0) {
        for (var s = 0; s < layout.length; s++) layout[s].y -= shift;
        panelTop -= shift;
      }
    }
    for (var d = 0; d < layout.length; d++) {
      chip(ctx, layout[d].text, layout[d].x, layout[d].y, 28, 68, INNER);
    }

    // 字段行（只包含勾选的字段；未勾选显示「已隐藏」）
    var ry = panelTop + 56;
    ctx.fillStyle = "rgba(255,255,255,.14)";
    roundRect(ctx, PAD, panelTop, INNER, panelH, 32);
    ctx.fill();

    ctx.textBaseline = "alphabetic";
    for (var r = 0; r < rows.length; r++) {
      var row = rows[r];
      var y = ry + r * 78;
      ctx.fillStyle = soft;
      ctx.font = font(32, 500);
      ctx.fillText(row.label, 132, y);
      var labelW = ctx.measureText(row.label).width;

      ctx.textAlign = "right";
      ctx.font = row.hidden ? font(32, 500) : font(34, 700);
      ctx.fillStyle = row.hidden ? "rgba(255,255,255,.55)" : white;
      // 值右对齐绘制：先按「不与左侧标签重叠」算出可用宽度再截断
      var avail = CARD_W - 132 - (132 + labelW) - 32;
      ctx.fillText(clip(ctx, row.text, avail), CARD_W - 132, y);
      ctx.textAlign = "left";
    }

    // 页脚
    ctx.fillStyle = "rgba(255,255,255,.72)";
    ctx.font = font(28, 400);
    ctx.fillText(clip(ctx, cfg.sourceLabel, INNER), PAD, CARD_H - 168);
    ctx.fillStyle = "rgba(255,255,255,.52)";
    ctx.font = font(24, 400);
    // 生成时间默认隐藏：只有用户主动勾选「生成时间」才在图上出现
    var genRow = null;
    for (var k = 0; k < rows.length; k++) {
      if (rows[k].key === "generated") genRow = rows[k];
    }
    var genShown = !!(genRow && !genRow.hidden && genRow.available !== false);
    var footLine = (genShown ? "生成于 " + (genRow.text || "—") + " · " : "") +
      "仅包含你勾选的字段";
    ctx.fillText(clip(ctx, footLine, INNER), PAD, CARD_H - 122);
    ctx.fillText(clip(ctx, "麦麦时光机 · 大赛参赛作品 · 非麦当劳官方产品", INNER),
      PAD, CARD_H - 80);
  }

  // ------------------------------------------------------------ 分享面板逻辑
  function initShare() {
    var canvas = $("cardCanvas");
    if (!canvas) return;
    var fields = (DATA.fields || {});
    var order = ["amount", "store", "date", "generated"];
    var toggles = {};
    var previewList = $("fieldPreview");
    var configOut = $("configOut");

    function rowOf(key) {
      var f = fields[key] || { label: key, value: "—", hidden_text: "已隐藏" };
      // 公开分享版：值根本没写进文件，只能显示占位符（勾选也没用）
      if (f.available === false) {
        return { key: key, label: f.label, hidden: false, available: false,
                 text: f.value || "公开版未写入" };
      }
      var on = !!(toggles[key] && toggles[key].checked);
      return {
        key: key,
        label: f.label,
        hidden: !on,
        available: true,
        text: on ? f.value : (f.hidden_text || "已隐藏")
      };
    }

    // 预览行 <li><b>标签</b><span>值</span></li>，标签与值都走 textContent
    function previewRow(label, text) {
      var li = el("li");
      li.appendChild(el("b", label));
      if (text !== undefined && text !== null && text !== "") {
        li.appendChild(el("span", text));
      }
      return li;
    }

    function currentRows() {
      var rows = [];
      for (var i = 0; i < order.length; i++) {
        if (fields[order[i]]) rows.push(rowOf(order[i]));
      }
      return rows;
    }

    function refresh() {
      var rows = currentRows();
      var cfg = {
        persona: DATA.persona || "口味待探索",
        personaDesc: DATA.persona_desc || "",
        keywords: DATA.keywords || [],
        rows: rows,
        sourceLabel: DATA.source_label || "来源未知",
        generatedAt: DATA.generated_at || ""
      };
      drawCard(canvas, cfg);

      if (previewList) {
        previewList.textContent = "";
        previewList.appendChild(previewRow("麦麦时光机 · 我的年度回忆", null));
        previewList.appendChild(previewRow("我的口味人格", cfg.persona));
        previewList.appendChild(
          previewRow("年度关键词", (DATA.keywords || []).join("、") || "—")
        );
        for (var i = 0; i < rows.length; i++) {
          previewList.appendChild(previewRow(rows[i].label, rows[i].text));
        }
      }

      if (configOut) {
        configOut.textContent = buildConfigText(cfg, rows);
      }
    }

    function buildConfigText(cfg, rows) {
      var lines = [
        "麦麦时光机 · 我的年度回忆",
        "我的口味人格：" + cfg.persona,
        "年度关键词：" + ((DATA.keywords || []).join("、") || "—")
      ];
      for (var i = 0; i < rows.length; i++) {
        lines.push(rows[i].label + "：" + rows[i].text);
      }
      lines.push("导出配置：" + JSON.stringify({
        amount: rows.some(function (r) { return r.key === "amount" && !r.hidden; }),
        store: rows.some(function (r) { return r.key === "store" && !r.hidden; }),
        date: rows.some(function (r) { return r.key === "date" && !r.hidden; }),
        generated: rows.some(function (r) { return r.key === "generated" && !r.hidden; })
      }));
      if (DATA.redacted) {
        lines.push("生成模式：公开分享版（金额 / 门店名称 / 到日日期未写入本文件）");
      }
      return lines.join("\n");
    }

    for (var j = 0; j < order.length; j++) {
      // 注意：变量名不能叫 el —— var 是函数级提升，会遮蔽上面的 el() 建节点助手
      var box = $("f-" + order[j]);
      if (!box) continue;
      toggles[order[j]] = box;
      box.addEventListener("change", refresh);
      // 与 Python 侧默认保持一致：未勾选即隐藏
      box.checked = !!(DATA.privacy && DATA.privacy[order[j]]);
    }

    var toast = $("toast");
    function say(msg) {
      if (!toast) return;
      toast.textContent = msg;
      toast.classList.add("show");
      window.setTimeout(function () { toast.classList.remove("show"); }, 2400);
    }

    var dl = $("downloadCard");
    if (dl) {
      dl.addEventListener("click", function () {
        var name = "share-card.png";
        function trigger(href, revoke) {
          var a = doc.createElement("a");
          a.href = href;
          a.download = name;
          doc.body.appendChild(a);
          a.click();
          doc.body.removeChild(a);
          if (revoke) window.setTimeout(function () { window.URL.revokeObjectURL(href); }, 4000);
        }
        try {
          if (canvas.toBlob) {
            canvas.toBlob(function (blob) {
              if (!blob) { say("导出失败，请改用截图"); return; }
              trigger(window.URL.createObjectURL(blob), true);
              say("已导出 share-card.png（1080×1920，不含元数据）");
            }, "image/png");
          } else {
            trigger(canvas.toDataURL("image/png"), false);
            say("已导出 share-card.png");
          }
        } catch (e) {
          say("当前浏览器不支持导出，可用长按/截图保存");
        }
      });
    }

    var copyBtn = $("copyConfig");
    if (copyBtn) {
      copyBtn.addEventListener("click", function () {
        var text = (configOut && configOut.textContent) || buildConfigText({}, currentRows());
        function fallback() {
          try {
            var ta = doc.createElement("textarea");
            ta.value = text;
            ta.style.position = "fixed";
            ta.style.left = "-9999px";
            doc.body.appendChild(ta);
            ta.select();
            doc.execCommand("copy");
            doc.body.removeChild(ta);
            say("卡片配置已复制到剪贴板");
          } catch (e) {
            say("复制失败，请手动选择下方文本");
          }
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            say("卡片配置已复制到剪贴板");
          })["catch"](fallback);
        } else {
          fallback();
        }
      });
    }

    refresh();
  }

  // ------------------------------------------------------------ 启动
  function boot() {
    initReveal();
    initNav();
    initPrivacy();
    initBars();
    initShare();
  }

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
