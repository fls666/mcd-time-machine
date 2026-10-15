/* 故事页（story.html）前端冒烟测试。
 *
 * 用 jsdom 真跑一遍页面脚本，覆盖两条路径：
 *   A. 增强路径：注入 IntersectionObserver，验证渐显、章节圆点、条形图初始化；
 *   B. 降级路径：不注入，验证脚本失效时六章内容依然完整可读。
 * 两条路径都会校验分享卡片的隐私默认值：未勾选的字段不得被画进图片。
 *
 * 本机运行（需要 jsdom）：
 *   npm install jsdom
 *   node scripts/smoke_story.js <story.html 路径>
 *
 * 无 jsdom 时脚本会以退出码 2 跳过，不会让 CI 变红。
 */
const fs = require('fs');
const path = require('path');

const file = process.argv[2];
if (!file) {
  console.error('用法：node scripts/smoke_story.js <story.html 路径>');
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
    console.warn('未安装 jsdom，跳过故事页冒烟测试。安装：npm install jsdom');
    process.exit(2);
  }
}

const html = fs.readFileSync(file, 'utf8');

let failures = 0;
function check(name, cond, extra) {
  const ok = !!cond;
  if (!ok) failures++;
  console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (extra ? '  -> ' + extra : ''));
}

function makeCtx(sink) {
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === 'measureText') return (t) => ({ width: String(t).length * 18 });
      if (prop === 'createLinearGradient') return () => ({ addColorStop() {} });
      if (prop === 'canvas') return { width: 1080, height: 1920 };
      // 记录真正被「画进图片」的文字，用于验证敏感字段是否真的没进图
      if (prop === 'fillText') return (t) => { sink.push(String(t)); };
      return () => {};
    },
    set() { return true; },
  });
}

function boot(withObserver) {
  const errors = [];
  const drawn = [];
  const dom = new JSDOM(html, {
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
      window.HTMLCanvasElement.prototype.getContext = () => makeCtx(drawn);
      window.HTMLCanvasElement.prototype.toBlob = function (cb) { cb({ size: 999 }); };
      window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,AAA';
      window.URL.createObjectURL = () => 'blob:fake';
      window.URL.revokeObjectURL = () => {};
      window.matchMedia = () => ({ matches: false });
      window.addEventListener('error', (e) => errors.push('error: ' + e.message));
    },
  });
  return { dom, errors, drawn };
}

function commonChecks(doc, errors, drawn, label) {
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
      && before.some((t) => t.indexOf(s) >= 0));
  check(`[${label}] 默认状态未把敏感值画进图片`, leaked.length === 0, leaked.join(' | '));
  check(`[${label}] 图片上显示「已隐藏」`, before.indexOf('已隐藏') >= 0);

  const configOut = doc.getElementById('configOut');
  const text = configOut ? configOut.textContent : '';
  check(`[${label}] 默认三项均未开启`,
    /"amount":false/.test(text) && /"store":false/.test(text) && /"date":false/.test(text));
  check(`[${label}] 默认金额/门店/日期显示为已隐藏`,
    /消费金额：已隐藏/.test(text) && /门店名称：已隐藏/.test(text) && /消费日期：已隐藏/.test(text));

  doc.getElementById('f-amount').click();
  doc.getElementById('f-store').click();
  doc.getElementById('f-date').click();
  const on = doc.getElementById('configOut').textContent;
  check(`[${label}] 勾选后三项均开启`,
    /"amount":true/.test(on) && /"store":true/.test(on) && /"date":true/.test(on));
  check(`[${label}] 勾选后不再显示已隐藏`, !/消费日期：已隐藏/.test(on),
    (/消费日期：(.+)/.exec(on) || [, ''])[1]);
  const after = drawn.slice(before.length);
  check(`[${label}] 勾选后敏感值才进入图片`,
    after.some((t) => t.indexOf(payload.fields.date.value) >= 0), payload.fields.date.value);

  doc.getElementById('downloadCard').click();
  doc.getElementById('copyConfig').click();
  check(`[${label}] 导出/复制按钮无异常`, errors.length === 0, errors.join(' | '));
}

// ---------------------------------------------------------------- A 增强路径
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
    doc.querySelectorAll('.fill[data-w]'), (el) => el.style.width);
  check('[增强] 条形图已按 data-w 设宽', filled.length > 0, filled.length + ' 个');
  commonChecks(doc, enhanced.errors, enhanced.drawn, '增强');
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
    commonChecks(doc2, degraded.errors, degraded.drawn, '降级');
    degraded.dom.window.close();

    console.log(failures === 0 ? '\n全部通过' : `\n${failures} 项失败`);
    process.exit(failures === 0 ? 0 : 1);
  }, 400);
}, 400);
