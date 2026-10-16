/* 真实浏览器验收（Playwright + Chromium headless）。
 *
 * 补上 jsdom 冒烟测不到的那一层：真实布局引擎与真实 Canvas 编码。
 * 覆盖审查要求的五项：
 *   1) 375px 手机宽度下无横向溢出；
 *   2) 较长商品名 / 门店名不撑破布局；
 *   3) 隐私模式（页面开关 + 公开分享版）；
 *   4) 六章节滚动叙事真的逐章显现；
 *   5) 导出 share-card.png 并校验尺寸与元数据。
 *
 * 用法（需要 playwright 与 chromium）：
 *   npm install playwright && npx playwright install chromium
 *   node scripts/browser_check.js <personal story.html> [public story.html] [--keep-png DIR]
 *
 * 依赖缺失时以退出码 2 跳过；设置 SMOKE_REQUIRE_PLAYWRIGHT=1（CI 用）则改为失败。
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { pathToFileURL } = require('url');

const personal = process.argv[2];
const publicFile = process.argv[3] && !process.argv[3].startsWith('--') ? process.argv[3] : null;
const keepIdx = process.argv.indexOf('--keep-png');
const keepDir = keepIdx > -1 ? process.argv[keepIdx + 1] : null;

if (!personal) {
  console.error('用法：node scripts/browser_check.js <personal story.html> [public story.html]');
  process.exit(2);
}

function loadPlaywright() {
  try {
    return require('playwright');
  } catch (e) {
    try {
      return require(path.join(process.env.HOME || '', '.workbuddy', 'binaries', 'node',
        'workspace', 'node_modules', 'playwright'));
    } catch (e2) {
      if (process.env.SMOKE_REQUIRE_PLAYWRIGHT) {
        console.error('CI 要求执行浏览器验收，但 playwright 不可用：' + e2.message);
        process.exit(1);
      }
      console.warn('未安装 playwright，跳过浏览器验收。安装：npm install playwright && npx playwright install chromium');
      process.exit(2);
    }
  }
}

const { chromium } = loadPlaywright();
const VIEWPORT = { width: 375, height: 812 };   // iPhone X 竖屏，审查指定的宽度

let failures = 0;
function check(name, cond, extra) {
  const ok = !!cond;
  if (!ok) failures++;
  console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (extra ? '  -> ' + extra : ''));
}
const url = (p) => pathToFileURL(path.resolve(p)).href;

/* 把内联数据块换成超长 / 恶意载荷，用于验证长文本不撑破布局 */
function hostileCopy(src, outPath) {
  const m = src.match(/(<script id="story-data"[^>]*>)([\s\S]*?)(<\/script>)/);
  if (!m) return null;
  const payload = JSON.parse(m[2]);
  const longName = '芝士双层牛肉汉堡加培根加蛋加双层鸡排' .repeat(4);
  payload.keywords = [longName, '超长关键词'.repeat(10), '薯条'];
  payload.persona = '嗜辣星人' .repeat(12);
  payload.persona_desc = '这是一段很长的口味人格描述'.repeat(6);
  payload.fields.store.value = '麦当劳（科技园得来速餐厅）'.repeat(6) + ' · 18 次';
  payload.fields.amount.value = '累计消费 ¥123,456.78';
  payload.fields.date.value = '2025-09-19 ~ 2026-10-12';
  const raw = JSON.stringify(payload).replace(/</g, '\\u003c').replace(/>/g, '\\u003e');
  fs.writeFileSync(outPath, src.replace(m[0], m[1] + raw + m[3]), 'utf8');
  return outPath;
}

/* PNG 结构解析：拿尺寸 + 列出所有 chunk 类型（用来确认没有写元数据） */
function parsePng(buf) {
  if (buf.length < 24 || buf.slice(0, 8).toString('hex') !== '89504e470d0a1a0a') return null;
  const chunks = [];
  let dims = null;
  let off = 8;
  while (off + 8 <= buf.length) {
    const len = buf.readUInt32BE(off);
    const type = buf.slice(off + 4, off + 8).toString('ascii');
    chunks.push(type);
    if (type === 'IHDR') {
      dims = { width: buf.readUInt32BE(off + 8), height: buf.readUInt32BE(off + 12) };
    }
    off += 12 + len;
    if (type === 'IEND') break;
  }
  return { dims, chunks };
}

/* 横向溢出检查：滚动宽度不得超过视口；并逐个元素核对右边界。
 * - 处在横向滚动容器内的元素不算页面级溢出；
 * - 完全位于左边界外的元素（如可达性「跳到正文」链接，刻意放在画布外）不算溢出。 */
function geometry(page) {
  return page.evaluate(() => {
    const vw = window.innerWidth;
    const clipped = (el) => {
      let p = el.parentElement;
      while (p && p !== document.documentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return true;
        p = p.parentElement;
      }
      return false;
    };
    const bad = [];
    document.querySelectorAll('body *').forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0 || clipped(el)) return;
      if (r.right <= 0) return;                    // 刻意放在左侧画布外
      if (r.right > vw + 1 || r.left < -1) {
        const cls = typeof el.className === 'string' && el.className
          ? '.' + el.className.split(/\s+/)[0] : '';
        bad.push(el.tagName + (el.id ? '#' + el.id : '') + cls +
          ' [' + Math.round(r.left) + ',' + Math.round(r.right) + ']');
      }
    });
    return { vw, scrollW: document.documentElement.scrollWidth, bad: bad.slice(0, 5) };
  });
}

/* 滚过整页：按视口高度的 0.6 逐屏滚动，让 IntersectionObserver 有机会看到每个 .reveal */
async function scrollThroughPage(page) {
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  const step = Math.round(VIEWPORT.height * 0.6);
  for (let y = 0; y < height; y += step) {
    await page.evaluate((top) => window.scrollTo(0, top), y);
    await page.waitForTimeout(90);
  }
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.waitForTimeout(1000);   // 等 0.7s 的渐显过渡跑完
}

/* 把第 chapterId 章第 index 个 .reveal 滚到视口正中（保证必然进入观察器范围），
 * 再轮询到它拿到 .is-visible 且渐显过渡结束。有上界，不会无限等。 */
async function revealElement(page, chapterId, index, maxMs = 2500) {
  const scrolled = await page.evaluate((arg) => {
    const els = document.querySelectorAll('#ch' + arg[0] + ' .reveal');
    const el = els[arg[1]];
    if (!el) return false;
    const r = el.getBoundingClientRect();
    window.scrollBy(0, r.top + r.height / 2 - window.innerHeight / 2);
    return true;
  }, [chapterId, index]);
  if (!scrolled) return null;
  const deadline = Date.now() + maxMs;
  let last = null;
  for (;;) {
    last = await page.evaluate((arg) => {
      const els = document.querySelectorAll('#ch' + arg[0] + ' .reveal');
      const el = els[arg[1]];
      if (!el) return null;
      return {
        visible: el.classList.contains('is-visible'),
        opacity: parseFloat(getComputedStyle(el).opacity),
        centered: Math.abs((el.getBoundingClientRect().top +
          el.getBoundingClientRect().bottom) / 2 - window.innerHeight / 2) < 4,
      };
    }, [chapterId, index]);
    if (last && last.visible && last.opacity >= 0.99 && last.centered) return last;
    if (Date.now() >= deadline) return last;
    await page.waitForTimeout(120);
  }
}

async function downloadCard(page, savePath) {
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 15000 }),
    page.click('#downloadCard'),
  ]);
  const suggested = download.suggestedFilename();
  await download.saveAs(savePath);
  return suggested;
}

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: VIEWPORT, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcdtm-browser-'));
  const pngPersonal = path.join(keepDir || tmpDir, 'share-card.png');
  if (keepDir) fs.mkdirSync(keepDir, { recursive: true });

  // ---------------------------------------------------------------- 个人完整版
  await page.goto(url(personal), { waitUntil: 'load' });
  await page.waitForTimeout(300);

  const g1 = await geometry(page);
  check('[375px] 页面滚动宽度未超出视口', g1.scrollW <= g1.vw + 1,
    `scrollWidth=${g1.scrollW} innerWidth=${g1.vw}`);
  check('[375px] 没有元素越出左右边界', g1.bad.length === 0, g1.bad.join(' | '));

  // 六章节存在且可滚动到位
  for (let i = 1; i <= 6; i++) {
    const exists = await page.locator('#ch' + i).count();
    check(`[章节] 第 ${i} 章存在`, exists === 1);
  }
  // 先确认「渐显」本身在生效：首屏外的 .reveal 加载时应当是未显现状态。
  // 否则后面的逐元素断言会退化成永远成立的空转。
  const atLoad = await page.evaluate(() => {
    const all = Array.prototype.slice.call(document.querySelectorAll('.reveal'));
    const hidden = all.filter((el) => !el.classList.contains('is-visible'));
    const zero = hidden.filter((el) => parseFloat(getComputedStyle(el).opacity) < 0.01);
    return { total: all.length, hidden: hidden.length, zero: zero.length };
  });
  check('[章节] 首屏外的内容加载时确实未显现（渐显真正生效）',
    atLoad.total > 0 && atLoad.hidden > 0 && atLoad.zero > 0, JSON.stringify(atLoad));

  // 滚动叙事：把每章的每个渐显元素依次滚到视口中央，逐一确认它真的显现了。
  // 逐个验证而不是只看「章内数量」——否则整屏章节容易空转成假通过。
  for (let i = 1; i <= 6; i++) {
    const total = await page.locator('#ch' + i + ' .reveal').count();
    if (total === 0) {
      check(`[章节] 第 ${i} 章滚动渐显`, true, '本章无渐显元素（首屏直接呈现）');
      continue;
    }
    let bad = 0;
    let firstBad = '';
    for (let k = 0; k < total; k++) {
      const r = await revealElement(page, i, k);
      if (!r || !r.visible || r.opacity < 0.99) {
        bad++;
        if (!firstBad) firstBad = `#${k} ${JSON.stringify(r)}`;
      }
    }
    check(`[章节] 第 ${i} 章 ${total} 个渐显元素滚入后都完整显现`, bad === 0,
      bad ? `${bad}/${total} 未显现：${firstBad}` : '');
  }
  // 全页滚完后不应有任何 .reveal 仍停在隐藏状态（滚动叙事真的把内容都带出来了）
  await scrollThroughPage(page);
  const stuck = await page.evaluate(() => {
    const all = Array.prototype.slice.call(document.querySelectorAll('.reveal'));
    const hidden = all.filter((el) => !el.classList.contains('is-visible'));
    return { total: all.length, hidden: hidden.length,
             sample: hidden.slice(0, 3).map((el) => el.className) };
  });
  check('[章节] 滚遍全页后没有内容被动画永久隐藏',
    stuck.hidden === 0, `${stuck.hidden}/${stuck.total} ${stuck.sample.join(' | ')}`);

  // 回到页首做后续检查
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(200);

  // 隐私浏览开关：开启后正文里的金额 / 日期就地打码
  const maskedBefore = await page.evaluate(() => {
    const el = document.querySelector('.pv');
    return el ? getComputedStyle(el, '::after').content : null;
  });
  await page.evaluate(() => document.getElementById('privToggle').click());
  await page.waitForTimeout(150);
  const masked = await page.evaluate(() => {
    const el = document.querySelector('.pv');
    if (!el) return null;
    return {
      content: getComputedStyle(el, '::after').content,
      color: getComputedStyle(el).color,
      pressed: document.getElementById('privToggle').getAttribute('aria-pressed'),
    };
  });
  check('[隐私浏览] 开启后并集打码标记生效',
    masked && masked.content && masked.content.indexOf('已隐藏') >= 0,
    JSON.stringify(masked));
  check('[隐私浏览] 关闭时正文没有打码内容',
    !maskedBefore || maskedBefore.indexOf('已隐藏') < 0, String(maskedBefore));
  await page.evaluate(() => document.getElementById('privToggle').click());

  // 导出 PNG 并校验
  const name1 = await downloadCard(page, pngPersonal);
  check('[导出] 文件名是 share-card.png', name1 === 'share-card.png', name1);
  const png1 = parsePng(fs.readFileSync(pngPersonal));
  check('[导出] 是合法 PNG', !!png1);
  check('[导出] 尺寸 1080x1920',
    png1 && png1.dims && png1.dims.width === 1080 && png1.dims.height === 1920,
    png1 ? JSON.stringify(png1.dims) : 'null');
  const META = ['tEXt', 'iTXt', 'zTXt', 'eXIf', 'tIME'];
  const meta = png1 ? png1.chunks.filter((c) => META.indexOf(c) >= 0) : META;
  check('[导出] PNG 不含元数据块', meta.length === 0, meta.join(','));
  check('[个人版] 顶部有个人完整版横幅',
    await page.locator('.mode-banner.personal').count() === 1);

  // ---------------------------------------------------- 长文本（抗溢出）
  const hostilePath = path.join(tmpDir, 'story-long.html');
  if (hostileCopy(fs.readFileSync(personal, 'utf8'), hostilePath)) {
    await page.goto(url(hostilePath), { waitUntil: 'load' });
    await page.waitForTimeout(250);
    const g2 = await geometry(page);
    check('[长文本 375px] 页面滚动宽度未超出视口', g2.scrollW <= g2.vw + 1,
      `scrollWidth=${g2.scrollW}`);
    check('[长文本 375px] 没有元素越出左右边界', g2.bad.length === 0, g2.bad.join(' | '));
    check('[长文本] 没有可执行事件属性注入',
      await page.evaluate(() => document.querySelectorAll('[onerror],[onmouseover]').length === 0));
    // 四项全开后导出，验证长文本不会让导出失败或改变尺寸
    await page.evaluate(() => {
      ['f-amount', 'f-store', 'f-date', 'f-generated'].forEach((id) => {
        const b = document.getElementById(id);
        if (b) { b.checked = true; b.dispatchEvent(new Event('change')); }
      });
    });
    await page.waitForTimeout(200);
    const pngLong = path.join(keepDir || tmpDir, 'share-card-long.png');
    await downloadCard(page, pngLong);
    const png2 = parsePng(fs.readFileSync(pngLong));
    check('[长文本] 导出仍是 1080x1920',
      png2 && png2.dims.width === 1080 && png2.dims.height === 1920,
      png2 ? JSON.stringify(png2.dims) : 'null');
  } else {
    check('[长文本] 已生成超长文本页面', false, '未找到内联数据块');
  }

  // ------------------------------------------------------------ 公开分享版
  if (!publicFile) {
    check('[公开版] 已提供公开版页面用于比对', false, '缺少第二个参数');
  } else {
    await page.goto(url(publicFile), { waitUntil: 'load' });
    await page.waitForTimeout(250);
    const text = await page.evaluate(() => document.body.innerText);
    check('[公开版] 页面上没有金额符号 ¥', text.indexOf('¥') === -1,
      (text.match(/¥[^\s]*/g) || []).slice(0, 3).join(' '));
    check('[公开版] 页面上没有到日日期', !/\d{4}-\d{2}-\d{2}/.test(text),
      (text.match(/\d{4}-\d{2}-\d{2}/g) || []).slice(0, 3).join(' '));
    const sw = await page.evaluate(() => {
      const all = Array.prototype.slice.call(document.querySelectorAll('.sw input'));
      return { count: all.length, disabled: all.filter((b) => b.disabled).length };
    });
    check('[公开版] 四个字段开关全部置灰', sw.count === 4 && sw.disabled === 4, JSON.stringify(sw));
    check('[公开版] 隐私浏览开关已隐藏',
      await page.evaluate(() => getComputedStyle(document.getElementById('privToggle')).display) === 'none');
    check('[公开版] 顶部有公开分享版横幅',
      await page.locator('.mode-banner.public').count() === 1);
    const g3 = await geometry(page);
    check('[公开版 375px] 没有元素越出左右边界', g3.bad.length === 0 && g3.scrollW <= g3.vw + 1,
      `scrollWidth=${g3.scrollW} ${g3.bad.join(' | ')}`);
    const pngPublic = path.join(keepDir || tmpDir, 'share-card-public.png');
    await downloadCard(page, pngPublic);
    const png3 = parsePng(fs.readFileSync(pngPublic));
    check('[公开版] 导出仍是 1080x1920',
      png3 && png3.dims.width === 1080 && png3.dims.height === 1920,
      png3 ? JSON.stringify(png3.dims) : 'null');
  }

  check('[页面] 没有脚本错误', errors.length === 0, errors.slice(0, 3).join(' | '));

  await browser.close();
  if (keepDir) console.log('\n导出样本已保存到：' + keepDir);
  console.log(failures === 0 ? '\n全部通过' : `\n${failures} 项失败`);
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => {
  console.error('浏览器验收执行失败：' + (e && e.stack ? e.stack : e));
  process.exit(1);
});
