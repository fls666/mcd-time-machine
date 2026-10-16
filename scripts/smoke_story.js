/* 故事页（story.html）前端冒烟测试。
 *
 * 用 jsdom 真跑一遍页面脚本，覆盖五条路径：
 *   A. 增强路径：注入 IntersectionObserver，验证渐显、章节圆点、条形图初始化；
 *   B. 降级路径：不注入，验证脚本失效时六章内容依然完整可读。
 *   C. 恶意数据路径：把商品名 / 门店名换成 HTML 注入载荷、带引号的属性逃逸载荷
 *      与超长中文，验证分享预览只以纯文本呈现，且绘制文字不越出画布。
 *   E. 极端数据路径：空字符串 / 空关键词 / 超长英文（无空格）/ Emoji（含 ZWJ 组合）
 *      / 中英混排 / 四项字段全开，验证不崩、不溢出、面板几何仍成立。
 *   D. 公开分享版（可选第二个参数）：验证金额 / 门店 / 到日日期确实不在文件里。
 * 各路径都会校验：隐私默认值（四项默认隐藏）、生成时间默认不入图、绘制文字在画布内。
 *
 * 本机运行（需要 jsdom）：
 *   npm install jsdom
 *   node scripts/smoke_story.js <personal story.html> [public story.html]
 *
 * 无 jsdom 时脚本会以退出码 2 跳过；设置 SMOKE_REQUIRE_JSDOM=1（CI 用）则改为失败，
 * 避免冒烟被静默跳过。
 */
const fs = require('fs');
const path = require('path');

const file = process.argv[2];
const publicFile = process.argv[3];
if (!file) {
  console.error('用法：node scripts/smoke_story.js <story.html 路径> [公开版 story.html 路径]');
  process.exit(2);
}

let JSDOM;
try {
  // 优先用项目本地安装，其次用全局 NODE_PATH
  JSDOM = require('jsdom').JSDOM;
} catch (e) {
  try {
    JSDOM = require(path.join(process.env.HOME || '', '.workbuddy', 'binaries', 'node',
      'workspace', 'node_modules', 'jsdom')).JSDOM;
  } catch (e2) {
    if (process.env.SMOKE_REQUIRE_JSDOM) {
      // CI 里必须真跑；装不上就报错，不能让冒烟静默跳过
      console.error('CI 要求执行故事页冒烟，但 jsdom 不可用：' + e2.message);
      process.exit(1);
    }
    console.warn('未安装 jsdom，跳过故事页冒烟测试。安装：npm install jsdom');
    process.exit(2);
  }
}

const html = fs.readFileSync(file, 'utf8');
const CARD_W = 1080;
const CARD_H = 1920;

let failures = 0;
function check(name, cond, extra) {
  const ok = !!cond;
  if (!ok) failures++;
  console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (extra ? '  -> ' + extra : ''));
}

/* 近似字宽：CJK 按字号全宽、ASCII 按 0.55 字号。
 * 比「每字符固定 18px」真实得多——否则长文本溢出的缺陷测不出来。 */
const CJK_RE = /[\u1100-\u115F\u2E80-\u9FFF\uA960-\uA97F\uAC00-\uD7FF\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFFEF]/;
function estimateWidth(s, size) {
  let w = 0;
  for (const ch of String(s)) w += CJK_RE.test(ch) ? size : size * 0.55;
  return w;
}

function makeCtx(sink, boxes) {
  const state = { textAlign: 'left', fontSize: 16 };
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === 'measureText') return (t) => ({ width: estimateWidth(t, state.fontSize) });
      if (prop === 'createLinearGradient') return () => ({ addColorStop() {} });
      if (prop === 'canvas') return { width: CARD_W, height: CARD_H };
      if (prop === 'textAlign') return state.textAlign;
      // 记录真正被「画进图片」的文字，用于验证敏感字段是否真的没进图、是否越界
      if (prop === 'fillText') return (t, x, y) => {
        const text = String(t);
        sink.push(text);
        if (boxes) {
          boxes.push({
            t: text, x: x, y: y, w: estimateWidth(text, state.fontSize),
            align: state.textAlign,
          });
        }
      };
      return () => {};
    },
    set(_t, prop, v) {
      if (prop === 'textAlign') state.textAlign = v;
      if (prop === 'font') {
        const m = /(\d+(?:\.\d+)?)px/.exec(String(v));
        if (m) state.fontSize = parseFloat(m[1]);
      }
      return true;
    },
  });
}

function boot(withObserver, source) {
  const errors = [];
  const drawn = [];
  const boxes = [];
  const dom = new JSDOM(source || html, {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    beforeParse(window) {
      // jsdom 没有布局引擎，offsetTop 恒为 0；按兄弟节点序号伪造，便于验证高亮逻辑
      Object.defineProperty(window.HTMLElement.prototype, 'offsetTop', {
        get() {
          if (!this.parentElement) return 0;
          return Array.prototype.indexOf.call(this.parentElement.children, this) * 1000;
        },
        configurable: true,
      });
      if (withObserver) {
        window.IntersectionObserver = function (cb) {
          this.observe = (el) => cb([{ isIntersecting: true, target: el }], this);
          this.unobserve = () => {};
          this.disconnect = () => {};
        };
      }
      window.HTMLCanvasElement.prototype.getContext = () => makeCtx(drawn, boxes);
      window.HTMLCanvasElement.prototype.toBlob = function (cb) { cb({ size: 999 }); };
      window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,AAA';
      window.URL.createObjectURL = () => 'blob:fake';
      window.URL.revokeObjectURL = () => {};
      window.matchMedia = () => ({ matches: false });
      window.addEventListener('error', (e) => errors.push('error: ' + e.message));
    },
  });
  return { dom, errors, drawn, boxes };
}

const FIELD_LABELS = ['消费金额', '门店名称', '消费日期', '生成时间'];

/* 绘制边界：所有文字必须落在 1080x1920 之内（右对齐要以 x 为右边界反推左边界） */
function checkBounds(boxes, label) {
  const outside = boxes.filter((b) => {
    if (!b.t) return false;
    const left = b.align === 'right' ? b.x - b.w : b.x;
    const right = b.align === 'right' ? b.x : b.x + b.w;
    return left < 0 || right > CARD_W || b.y < 0 || b.y > CARD_H;
  });
  check(`[${label}] 所有绘制文字都在 1080x1920 画布内`, outside.length === 0,
    outside.slice(0, 3).map((b) => `${JSON.stringify(b.t)} x=${b.x} w=${Math.round(b.w)}`).join(' | '));
}

/* 面板边界：脚本在绘制时把量出来的几何挂在 window.__mcdCardLayout 上。
 * 这是「文字没跑出卡片 / 没互相压叠」的直接证据——光看文字框在画布内不够，
 * 文字还得待在自己的面板里。（旧实现正是这里出问题：人格描述第 3 行
 * 落在面板外，而所有文字框都还在 1080x1920 之内，因此旧断言查不出来。） */
function checkPanelBounds(doc, label) {
  const L = doc.defaultView && doc.defaultView.__mcdCardLayout;
  if (!L) {
    check(`[${label}] 卡片布局钩子可用`, false, 'window.__mcdCardLayout 未设置');
    return;
  }
  check(`[${label}] 人格描述没有跑出人格面板`,
    L.descBottom === null || L.descBottom <= L.personaPanel.bottom,
    `descBottom=${L.descBottom} panelBottom=${L.personaPanel.bottom}`);
  check(`[${label}] 关键词胶囊块没有压到字段面板`,
    L.chipsBottom <= L.rowsPanel.top,
    `chipsBottom=${L.chipsBottom} rowsTop=${L.rowsPanel.top}`);
  check(`[${label}] 字段面板没有越过页脚上沿`,
    L.rowsPanel.bottom <= L.footTop,
    `rowsBottom=${L.rowsPanel.bottom} footTop=${L.footTop}`);
  check(`[${label}] 人格面板起于 y386 且高度为正`,
    L.personaPanel.top === 386 && L.personaPanel.bottom > L.personaPanel.top,
    JSON.stringify(L.personaPanel));
}

function commonChecks(doc, errors, drawn, boxes, label) {
  check(`[${label}] 脚本无运行时异常`, errors.length === 0, errors.join(' | '));
  for (let i = 1; i <= 6; i++) {
    check(`[${label}] 第 ${i} 章存在`, doc.getElementById('ch' + i));
  }
  const canvas = doc.getElementById('cardCanvas');
  check(`[${label}] 画布 1080x1920`,
    canvas && canvas.getAttribute('width') === '1080' && canvas.getAttribute('height') === '1920');

  // ---- 关键：默认隐藏时，真实金额 / 门店 / 日期不得被画进图片
  const payload = JSON.parse(doc.getElementById('story-data').textContent);
  const secrets = [
    payload.fields.amount.value,
    payload.fields.store.value,
    payload.fields.date.value,
  ];
  const before = drawn.slice();
  const leaked = secrets.filter(
    (s) => s && s !== '已隐藏' && s !== '暂无金额数据' && s !== '暂无门店记录'
      && s !== '公开版未写入' && before.some((t) => t.indexOf(s) >= 0));
  check(`[${label}] 默认状态未把敏感值画进图片`, leaked.length === 0, leaked.join(' | '));
  check(`[${label}] 图片上显示「已隐藏」`, before.indexOf('已隐藏') >= 0);

  const configOut = doc.getElementById('configOut');
  const text = configOut ? configOut.textContent : '';
  check(`[${label}] 默认四项均未开启`,
    /"amount":false/.test(text) && /"store":false/.test(text) &&
    /"date":false/.test(text) && /"generated":false/.test(text));
  check(`[${label}] 默认金额/门店/日期显示为已隐藏`,
    /消费金额：已隐藏/.test(text) && /门店名称：已隐藏/.test(text) && /消费日期：已隐藏/.test(text));

  // ---- 生成时间默认不入图
  check(`[${label}] 默认不出现生成时间`,
    !before.some((t) => t.indexOf('生成于') >= 0),
    before.filter((t) => t.indexOf('生成于') >= 0).join(' | '));

  // 只勾选前三项：生成时间应仍然不出现在图片里
  const sws = doc.querySelectorAll('.sw input');
  if (sws.length < 4 || !sws[0].disabled) {
    doc.getElementById('f-amount').click();
    doc.getElementById('f-store').click();
    doc.getElementById('f-date').click();
    const on = doc.getElementById('configOut').textContent;
    check(`[${label}] 勾选后前三项开启、生成时间仍关闭`,
      /"amount":true/.test(on) && /"store":true/.test(on) &&
      /"date":true/.test(on) && /"generated":false/.test(on));
    const after = drawn.slice(before.length);
    check(`[${label}] 勾选后敏感值才进入图片`,
      after.some((t) => t.indexOf(payload.fields.date.value.replace(/…$/, '')) >= 0),
      payload.fields.date.value);
    check(`[${label}] 前三项勾选后仍无生成时间`,
      !after.some((t) => t.indexOf('生成于') >= 0));

    const genBox = doc.getElementById('f-generated');
    if (genBox) {
      genBox.click();
      const genDrawn = drawn.slice(before.length + after.length);
      check(`[${label}] 勾选生成时间后图片上才出现`,
        genDrawn.some((t) => t.indexOf('生成于') >= 0));
    }
  }

  checkBounds(boxes, label);
  doc.getElementById('downloadCard').click();
  doc.getElementById('copyConfig').click();
  check(`[${label}] 导出/复制按钮无异常`, errors.length === 0, errors.join(' | '));
  checkPanelBounds(doc, label);
}

// ---------------------------------------------------------- C 恶意数据源（P1）
const EVIL_NAME = '<img src=x onerror=alert(1)>' + '超长恶意商品名'.repeat(6);
const EVIL_STORE = "麦当劳' onmouseover='alert(2)<b>注入</b>&\"门店" + '分店'.repeat(30);
const LONG_CJK = '超长' + '芝士双层牛肉汉堡加培根加蛋'.repeat(12);

/* 把页面里的数据块整体换成恶意载荷，其余 HTML 保持原样：
 * 这样测的是真实渲染 + 真实脚本，而不是手工造的片段。 */
function hostileSource() {
  const m = html.match(/(<script id="story-data"[^>]*>)([\s\S]*?)(<\/script>)/);
  if (!m) return null;
  const payload = JSON.parse(m[2]);
  payload.persona = EVIL_NAME;
  payload.keywords = [EVIL_NAME, LONG_CJK, '薯条'];
  payload.persona_desc = LONG_CJK;
  payload.fields.store.value = EVIL_STORE;
  payload.fields.amount.value = '¥1<script>alert(3)</script>';
  payload.fields.date.value = "2026-06-27'";
  const raw = JSON.stringify(payload).replace(/</g, '\\u003c').replace(/>/g, '\\u003e');
  return html.replace(m[0], m[1] + raw + m[3]);
}

// ------------------------------------------------- E 极端数据源（P2）
// 长文本 / Emoji / 中英混排 / 空字符串，都是「字体度量与常规数据不同」的输入，
// 最容易让按预算分配的版式算错。这里让四项字段全开（行数最多、每行最长）。
const LONG_ASCII = 'Supercalifragilisticexpialidocious'.repeat(6);
const EMOJI = '😋🍟🥤🔥🎉'.repeat(8);
const ZWJ_EMOJI = '👨‍👩‍👧‍👦🏳️‍🌈👩‍💻🧑‍🍳'.repeat(6);
const MIXED = 'Big Mac 巨无霸 x2 + 麦辣鸡翅 Spicy Wings + Coca-Cola 中可乐'.repeat(6);

function extremeSource() {
  const m = html.match(/(<script id="story-data"[^>]*>)([\s\S]*?)(<\/script>)/);
  if (!m) return null;
  const payload = JSON.parse(m[2]);
  payload.persona = '';                       // 空字符串
  payload.persona_desc = '';
  payload.keywords = [];                      // 空关键词 → 必须走兜底文案
  payload.fields.amount.value = LONG_ASCII;
  payload.fields.store.value = EMOJI + ZWJ_EMOJI;
  payload.fields.date.value = MIXED;
  payload.fields.generated.value = '   ';      // 只有空白
  payload.privacy = { amount: true, store: true, date: true, generated: true };
  const raw = JSON.stringify(payload).replace(/</g, '\\u003c').replace(/>/g, '\\u003e');
  return html.replace(m[0], m[1] + raw + m[3]);
}

/* 把四项开关全部打开（公开版的开关是禁用的，那里本来就不该有值可开）。 */
function pressAll(doc) {
  ['f-amount', 'f-store', 'f-date', 'f-generated'].forEach((id) => {
    const b = doc.getElementById(id);
    if (b && !b.disabled) b.click();
  });
}

// -------------------------------------------------------------- A 增强路径
const enhanced = boot(true);
setTimeout(() => {
  const doc = enhanced.dom.window.document;
  check('[增强] 已启用 js 渐显', doc.documentElement.classList.contains('js'));
  const dots = doc.querySelectorAll('#dots a');
  check('[增强] 生成 6 个章节圆点', dots.length === 6, '实际 ' + dots.length);
  check('[增强] 首章圆点高亮', dots[0] && dots[0].classList.contains('active'));
  check('[增强] 渐显元素被标记为可见', doc.querySelectorAll('.reveal.is-visible').length > 0,
    doc.querySelectorAll('.reveal.is-visible').length + ' 个');
  const filled = Array.prototype.filter.call(
    doc.querySelectorAll('.fill[data-w]'), (node) => node.style.width);
  check('[增强] 条形图已按 data-w 设宽', filled.length > 0, filled.length + ' 个');
  const sw = doc.querySelectorAll('.sw input');
  check('[增强] 分享面板有 4 个字段开关', sw.length === 4, '实际 ' + sw.length);
  check('[增强] 默认四项都未勾选', Array.prototype.every.call(sw, (b) => !b.checked));
  check('[增强] 顶部有个人完整版提示横幅', !!doc.querySelector('.mode-banner.personal'));
  commonChecks(doc, enhanced.errors, enhanced.drawn, enhanced.boxes, '增强');
  enhanced.dom.window.close();

  // -------------------------------------------------------------- B 降级路径
  const degraded = boot(false);
  setTimeout(() => {
    const doc2 = degraded.dom.window.document;
    check('[降级] 未启用 js 渐显', !doc2.documentElement.classList.contains('js'));
    const all = doc2.querySelectorAll('.reveal');
    const shown = doc2.querySelectorAll('.reveal.is-visible');
    check('[降级] 所有章节内容直接可见', all.length > 0 && all.length === shown.length,
      shown.length + '/' + all.length);
    const circles = doc2.querySelectorAll('#dots a');
    check('[降级] 导航圆点仍可用（不影响阅读）', circles.length === 6, '实际 ' + circles.length);
    commonChecks(doc2, degraded.errors, degraded.drawn, degraded.boxes, '降级');
    degraded.dom.window.close();

    // ------------------------------------------------- C 恶意数据路径（P1）
    const evilHtml = hostileSource();
    check('[恶意] 找到内联数据块', !!evilHtml);
    if (!evilHtml) {
      console.log(`\n${failures} 项失败`);
      process.exit(1);
    }
    const hostile = boot(false, evilHtml);
    setTimeout(() => {
      const doc3 = hostile.dom.window.document;
      // 先把开关打开，让恶意值真正进入预览与配置文本
      commonChecks(doc3, hostile.errors, hostile.drawn, hostile.boxes, '恶意');

      const list = doc3.getElementById('fieldPreview');
      const cfg = doc3.getElementById('configOut');
      const injected = list.querySelectorAll('img,script,iframe,svg,object,embed');
      check('[恶意] 预览区没有被注入任何元素', injected.length === 0,
        injected.length + ' 个：' + Array.prototype.map.call(injected, (n) => n.tagName).join(','));
      check('[恶意] 预览里没有可执行事件属性',
        doc3.querySelectorAll('[onerror],[onmouseover],[onload],[onclick]').length === 0);
      check('[恶意] 恶意商品名以纯文本呈现', list.textContent.indexOf(EVIL_NAME) >= 0);
      check('[恶意] 恶意门店名以纯文本呈现', list.textContent.indexOf(EVIL_STORE) >= 0);
      check('[恶意] 预览 HTML 里只有转义形态，没有真正的标签',
        list.innerHTML.indexOf('&lt;img') >= 0 && list.innerHTML.indexOf('<img') === -1,
        list.innerHTML.slice(0, 120));
      check('[恶意] 超长中文关键词完整保留在预览与配置里',
        list.textContent.indexOf(LONG_CJK) >= 0 && cfg.textContent.indexOf(LONG_CJK) >= 0);

      const labels = Array.prototype.map.call(list.querySelectorAll('b'), (b) => b.textContent);
      const allowed = ['麦麦时光机 · 我的年度回忆', '我的口味人格', '年度关键词'].concat(FIELD_LABELS);
      check('[恶意] 预览的标签仍全是固定文案，未被数据污染',
        labels.length > 0 && labels.every((t) => allowed.indexOf(t) >= 0), labels.join(' / '));
      check('[恶意] 预览行数与字段数一致，没有多出节点',
        list.querySelectorAll('li').length === 3 + doc3.querySelectorAll('.sw input').length,
        list.querySelectorAll('li').length + ' 行');
      check('[恶意] 配置文本是纯文本容器，未被解析出子元素',
        !!cfg && cfg.querySelectorAll('*').length === 0 &&
        cfg.textContent.indexOf(EVIL_STORE) >= 0);
      // 页面原本只有 2 个 script（内联数据块 + 主脚本），注入后数量不得变化
      check('[恶意] 文档脚本节点数未增加',
        doc3.querySelectorAll('script').length === 2,
        doc3.querySelectorAll('script').length + ' 个');

      // 长文本必须被截断成「内容前缀 + 省略号」：既不溢出，也不悄悄丢内容
      const clipped = hostile.drawn.filter((t) => t.length > 1 && t.slice(-1) === '…');
      check('[恶意] 超长文本被截断并带省略号（而非溢出）', clipped.length > 0,
        clipped.slice(0, 2).join(' | '));
      const storeDrawn = hostile.drawn.filter(
        (t) => t.length > 4 && EVIL_STORE.indexOf(t.replace(/…$/, '')) === 0);
      check('[恶意] 门店名以内容前缀形式绘制，未被替换成别的内容',
        storeDrawn.length > 0, storeDrawn.slice(0, 2).join(' | '));

      checkBounds(hostile.boxes, '恶意');
      checkPanelBounds(doc3, '恶意');
      // 这条是回归护栏：超长人格名 + 超长描述会把人脸面板「撑破」，
      // 修复后描述行数必须按预算被裁掉，而不是画到面板外面去
      const hL = hostile.dom.window.__mcdCardLayout;
      check('[恶意] 超长描述被按预算裁剪而不是越出面板',
        !hL || hL.descBottom === null || hL.descBottom <= hL.personaPanel.bottom,
        hL ? `描述行数=${hL.descLines} descBottom=${hL.descBottom} panelBottom=${hL.personaPanel.bottom}` : '');
      hostile.dom.window.close();

      // ------------------------------------------------ E 极端数据（P2）
      const extremeHtml = extremeSource();
      check('[极端] 已构造极端数据页面', !!extremeHtml);
      if (!extremeHtml) {
        console.log(`\n${failures} 项失败`);
        process.exit(1);
      }
      const ext = boot(false, extremeHtml);
      setTimeout(() => {
        const docE = ext.dom.window.document;
        check('[极端] 脚本无运行时异常', ext.errors.length === 0, ext.errors.join(' | '));
        const E_L = ext.dom.window.__mcdCardLayout;
        check('[极端] 卡片布局钩子可用', !!E_L);
        // 空关键词必须回落到兜底文案，画面上不能出现空胶囊
        check('[极端] 空关键词回落到「还在探索中」',
          ext.drawn.indexOf('还在探索中') >= 0, ext.drawn.slice(0, 6).join(' | '));
        // 人格名为空字符串：不崩，且回落到固定文案（绝不画空串）
        check('[极端] 人格名为空时回落到「口味待探索」',
          E_L && E_L.personaNameLines === 1 && ext.drawn.indexOf('口味待探索') >= 0,
          E_L ? '行数=' + E_L.personaNameLines : '无钩子');

        const pE = JSON.parse(docE.getElementById('story-data').textContent);
        // 四项全开：每个非空字段的值都必须被画进图片（截断后以原值为前缀）
        const missing = [];
        ['amount', 'store', 'date', 'generated'].forEach((k) => {
          const raw = String(pE.fields[k].value || '').trim();
          if (!raw) return;
          const hit = ext.drawn.some((t) => t.length > 1 &&
            raw.indexOf(t.replace(/…$/, '')) === 0 && t.replace(/…$/, '').length > 0);
          if (!hit) missing.push(k + '=' + raw.slice(0, 12));
        });
        check('[极端] 四项全开后每个非空字段都进了图片', missing.length === 0, missing.join(' | '));
        // 只有空白的值不能画出一行空白，要回落到「—」
        check('[极端] 空白值回落到「—」而不是画空行',
          ext.drawn.indexOf('—') >= 0 && ext.drawn.indexOf('   ') < 0,
          ext.drawn.filter((t) => !t.trim()).length + ' 条空白绘制');

        // 超长值必须被截断成「原值前缀 + 省略号」，而不是整段画出去
        const clippedOf = (value) => ext.drawn.some(
          (t) => t.slice(-1) === '…' && t.length > 1 && value.indexOf(t.slice(0, -1)) === 0);
        check('[极端] 超长英文（无空格）被截断', clippedOf(LONG_ASCII));
        check('[极端] Emoji 串被截断', clippedOf(EMOJI + ZWJ_EMOJI));
        check('[极端] 中英混排被截断', clippedOf(MIXED));
        check('[极端] 没有整段超长值被原样画出去',
          !ext.drawn.some((t) => t.indexOf(LONG_ASCII) === 0 || t.indexOf(EMOJI) === 0));
        // Emoji 不能被切在代理对中间（画出来会是方框）
        const LONE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
        check('[极端] 没有把 Emoji 劈成孤立代理对',
          ext.drawn.every((t) => !LONE.test(t)),
          ext.drawn.filter((t) => LONE.test(t)).slice(0, 2).join(' | '));

        checkBounds(ext.boxes, '极端');
        checkPanelBounds(docE, '极端');
        // 关键词为空时没有胶囊行，字段面板必须仍在页脚之上
        check('[极端] 字段面板底边仍在页脚上沿之上',
          !!E_L && E_L.rowsPanel.bottom <= E_L.footTop,
          E_L ? `rowsBottom=${E_L.rowsPanel.bottom} footTop=${E_L.footTop}` : '');
        docE.getElementById('downloadCard').click();
        docE.getElementById('copyConfig').click();
        check('[极端] 导出/复制按钮无异常', ext.errors.length === 0, ext.errors.join(' | '));
        ext.dom.window.close();
      }, 400);

      // ------------------------------------------------ D 公开分享版（P1）
      if (!publicFile) {
        check('[公开版] 已提供公开版页面用于比对', false,
          '缺少第二个参数：node scripts/smoke_story.js <personal> <public>');
        console.log(`\n${failures} 项失败`);
        process.exit(1);
      }
      const pubHtml = fs.readFileSync(publicFile, 'utf8');
      const pub = boot(false, pubHtml);
      setTimeout(() => {
        const doc4 = pub.dom.window.document;
        const payload4 = JSON.parse(doc4.getElementById('story-data').textContent);
        check('[公开版] 标记为公开模式',
          payload4.redacted === true && payload4.privacy_mode === 'public');
        check('[公开版] html 标记为 data-privacy=public',
          doc4.documentElement.getAttribute('data-privacy') === 'public');
        check('[公开版] 正文里没有金额符号 ¥', pubHtml.indexOf('¥') === -1,
          (pubHtml.match(/¥[\d,.]*/g) || []).slice(0, 3).join(' '));
        check('[公开版] 正文里没有到日日期', !/\d{4}-\d{2}-\d{2}/.test(pubHtml),
          (pubHtml.match(/\d{4}-\d{2}-\d{2}/g) || []).slice(0, 3).join(' '));
        check('[公开版] 内联数据里没有原值',
          payload4.fields.amount.value === '公开版未写入' &&
          payload4.fields.store.value === '公开版未写入' &&
          payload4.generated_at === '');
        const sws = doc4.querySelectorAll('.sw input');
        check('[公开版] 四个字段开关全部置灰',
          sws.length === 4 && Array.prototype.every.call(sws, (b) => b.disabled));
        check('[公开版] 页面顶部有公开版说明横幅', !!doc4.querySelector('.mode-banner.public'));
        check('[公开版] 预览显示占位说明，而不是原值',
          doc4.getElementById('fieldPreview').textContent.indexOf('公开版未写入') >= 0);
        check('[公开版] 不含指向完整报告的链接',
          doc4.querySelectorAll("a[href='report.html']").length === 0);
        check('[公开版] 脚本无运行时异常', pub.errors.length === 0, pub.errors.join(' | '));
        checkBounds(pub.boxes, '公开版');
        checkPanelBounds(doc4, '公开版');
        pub.dom.window.close();

        console.log(failures === 0 ? '\n全部通过' : `\n${failures} 项失败`);
        process.exit(failures === 0 ? 0 : 1);
      }, 400);
    }, 400);
  }, 400);
}, 400);
