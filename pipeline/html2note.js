#!/usr/bin/env node
/**
 * html2note.js — 从 HTML 转 PDF 再上传到云笔记（不改客户端，复用网页端上传链路）
 *
 * 目录约定（见 config.json）：
 *   output/  ：要处理的 HTML（笔记正文），每个生成同名 PDF
 *   pdf/     ：生成的 PDF
 *   .workbuddy/uploaded.json ：已上传记录（按 HTML 内容哈希去重）
 *
 * 用法：
 *   node html2note.js                     # 转换 + 上传；同一 HTML 未变则跳过
 *   node html2note.js --dry               # 只转换，不上传
 *   node html2note.js --force             # 忽略记录，强制重新上传
 *   node html2note.js --only 答案         # 只处理文件名含「答案」的 HTML
 *   node html2note.js --watch             # 常驻：监听 output/ 并周期 rescan，新 HTML 自动处理
 *   node html2note.js --watch --interval 10000   # 自定义 rescan 间隔（毫秒，默认 30000）
 *   node html2note.js --watch --check-cloud --cloud-folder <文件夹名|parentId>   # 常驻同时检查云笔记并下载（只读）
 *   node html2note.js --cloud             # 只做一轮云笔记检查 + 下载，不处理 HTML
 *   node html2note.js --cloud --cloud-all # 首次也把历史笔记全部下载（默认只建基线）
 *
 * 云笔记下载目录：cloud/<时间>_<标题>_<fileId前8>/，同一笔记内相同 URL 只落一次，
 * 另写 resources.json 记录每页每条资源的页号/名称/类型/URL/本地文件名。
 * 只发 GET（Notes/GetAll、Resources/GetByFileId、OSS 下载），不写云端。
 *
 * 常驻模式下日志写入 logs\watch-YYYY-MM-DD.log（按天切分），Ctrl+C 处理完当前文件后退出。
 *
 * 登录态：每次上传前检查 token；不足 5 分钟自动用 refreshToken 续期（通常可续 7 天）；
 *         refreshToken 也过期时用账号密码重登。凭据来源（任选其一）：
 *           config.json  → "auth": {"userName":"…","password":"…"}
 *           环境变量     → ZY_USERNAME / ZY_PASSWORD
 *         都没有时只会报错并给出提示，不会把密码写进任何文件或日志。
 *   node html2note.js --check-auth        # 只检查/续期登录态，不转换也不上传
 *
 * 依赖：本机已启动一个带 --remote-debugging-port 的 Chrome，且其中一个标签页
 *       是 toolbox（默认 http://127.0.0.1:8088/，已登录）。不新增任何 npm 依赖。
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { pathToFileURL } = require('url');

const ROOT = __dirname;
const CFG = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'));
const OUT_DIR = path.resolve(ROOT, CFG.outputDir);
const PDF_DIR = path.resolve(ROOT, CFG.pdfDir);
const STATE_FILE = path.resolve(ROOT, CFG.stateFile);
const CDP = CFG.cdpEndpoint;
const TOOLBOX_PART = CFG.toolboxUrlPart;

const ARGS = process.argv.slice(2);
const DRY = ARGS.includes('--dry');
const FORCE = ARGS.includes('--force');
const ONLY = (() => { const i = ARGS.indexOf('--only'); return i >= 0 ? (ARGS[i + 1] || '') : ''; })();
const WATCH = ARGS.includes('--watch');
const WATCH_INTERVAL = (() => {
    const i = ARGS.indexOf('--interval');
    const n = i >= 0 ? parseInt(ARGS[i + 1], 10) : NaN;
    return Number.isFinite(n) && n > 0 ? n : 30000;
})();

// 云笔记：只读检查 + 下载（复用 toolbox 页面里已有的 aesDecrypt / getCloudNoteApiPathR）
// 默认关闭，--check-cloud 才启用（--cloud 为同义旧写法）
const CLOUD = ARGS.includes('--check-cloud') || ARGS.includes('--cloud');
const CLOUD_ALL = ARGS.includes('--cloud-all');
const CLOUD_LIMIT = (() => {
    const i = ARGS.indexOf('--cloud-limit');
    const n = i >= 0 ? parseInt(ARGS[i + 1], 10) : NaN;
    if (Number.isFinite(n) && n > 0) return n;
    return (CFG.cloud && CFG.cloud.maxPerRound) || 3;
})();
// 要检查的云文件夹：--cloud-folder <文件夹名 或 parentId>；留空且未配置时只建基线不下载
const CLOUD_FOLDER = (() => {
    const i = ARGS.indexOf('--cloud-folder');
    if (i >= 0 && ARGS[i + 1]) return String(ARGS[i + 1]).trim();
    return String((CFG.cloud && CFG.cloud.folder) || '').trim();
})();
const CLOUD_DIR = path.resolve(ROOT, (CFG.cloud && CFG.cloud.dir) || 'input');
// 只下载标题含关键字的笔记（默认 "ds"；传 --cloud-title "" 可关掉这层过滤）
const CLOUD_TITLE = (() => {
    const i = ARGS.indexOf('--cloud-title');
    if (i >= 0) return String(ARGS[i + 1] || '').trim();
    const v = CFG.cloud && CFG.cloud.title;
    return String(v === undefined ? 'ds' : v).trim();
})();
const CLOUD_STATE = path.resolve(ROOT, (CFG.cloud && CFG.cloud.stateFile) || '.workbuddy/cloud-seen.json');
const CLOUD_INTERVAL = (CFG.cloud && CFG.cloud.intervalMs) || 30000;
let cloudNoFolderWarned = false;

const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ---------------- CDP ---------------- */
class Cdp {
    constructor(ws) {
        this.ws = ws; this.id = 0; this.pend = new Map(); this.handlers = new Set();
        ws.onmessage = e => {
            const m = JSON.parse(e.data);
            if (m.id && this.pend.has(m.id)) { this.pend.get(m.id)(m); this.pend.delete(m.id); return; }
            for (const h of this.handlers) h(m);
        };
    }
    static async open(url) {
        const ws = new WebSocket(url);
        await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WebSocket 连接失败: ' + url)); });
        return new Cdp(ws);
    }
    send(method, params = {}, sessionId) {
        return new Promise(res => {
            const id = ++this.id;
            this.pend.set(id, res);
            const msg = { id, method, params };
            if (sessionId) msg.sessionId = sessionId;
            this.ws.send(JSON.stringify(msg));
        });
    }
    on(fn) { this.handlers.add(fn); return () => this.handlers.delete(fn); }
    close() { try { this.ws.close(); } catch (_) { } }
}

async function evalIn(cdp, expression, sessionId, awaitPromise = false) {
    const r = await cdp.send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true }, sessionId);
    if (r.error) throw new Error('Runtime.evaluate 失败: ' + JSON.stringify(r.error));
    const d = r.result;
    if (d.exceptionDetails) {
        const ex = d.exceptionDetails.exception || {};
        throw new Error('页面异常: ' + String(ex.description || d.exceptionDetails.text || '').slice(0, 400));
    }
    return d.result ? d.result.value : undefined;
}

/* ---------------- 转换：HTML ?PDF ---------------- */
function countPdfPages(buf) {
    // Chrome 输出?PDF 里每页都?"/Type /Page"（非 "/Pages"），据此做自棢
    const s = buf.toString('latin1');
    return (s.match(/\/Type\s*\/Page(?![s])/g) || []).length;
}

async function convertOne(browser, htmlPath, pdfPath) {
    const url = pathToFileURL(htmlPath).href;
    const created = await browser.send('Target.createTarget', { url: 'about:blank', background: true });
    if (created.error) throw new Error('Target.createTarget 失败: ' + JSON.stringify(created.error));
    const targetId = created.result.targetId;
    try {
        const att = await browser.send('Target.attachToTarget', { targetId, flatten: true });
        if (att.error) throw new Error('attachToTarget 失败: ' + JSON.stringify(att.error));
        const sessionId = att.result.sessionId;
        await browser.send('Page.enable', {}, sessionId);
        await browser.send('Runtime.enable', {}, sessionId);

        const nav = await browser.send('Page.navigate', { url }, sessionId);
        if (nav.error) throw new Error('Page.navigate 失败: ' + JSON.stringify(nav.error));

        let ready = '';
        for (let i = 0; i < 200; i++) {
            ready = await evalIn(browser, 'document.readyState + "|" + location.href', sessionId);
            if (String(ready).startsWith('complete|file:')) break;
            await sleep(100);
        }
        if (!String(ready).startsWith('complete|')) throw new Error('页面加载超时: ' + ready);
        await sleep(500); // 等字体与布局稳定

        // 自检页数：优?.page 个数（严格相等）；否则用「强制分页元素个数（允许 ±1?        // 因为首元素的分页符会被浏览器忽略）；两都没有时不做严格校验，只记录实际页数?
        const shape = JSON.parse(await evalIn(browser, `(function(){
            var pages = document.querySelectorAll('.page').length;
            var breaks = 0;
            try {
                var all = document.querySelectorAll('body *');
                for (var i = 0; i < all.length; i++) {
                    var cs = getComputedStyle(all[i]);
                    if (cs.breakBefore === 'page' || cs.pageBreakBefore === 'always') breaks++;
                }
            } catch (e) {}
            return JSON.stringify({pages: pages, breaks: breaks});
        })()`, sessionId));
        const strict = shape.pages > 0;
        const expectPages = strict ? shape.pages : (shape.breaks > 0 ? shape.breaks : 0);
        const okPages = n => !expectPages || (strict ? n === expectPages : Math.abs(n - expectPages) <= 1);

        let buf = null, pages = 0, tries = 0;
        for (; tries < 3; tries++) {
            // 强制?print 媒体，否?body 的屏幕样式（padding/阴影）会把一页挤?2×2 ?
            await browser.send('Emulation.setEmulatedMedia', { media: 'print' }, sessionId);
            await sleep(250);
            const pr = await browser.send('Page.printToPDF', {
                preferCSSPageSize: true,
                printBackground: true,
                marginTop: 0, marginBottom: 0, marginLeft: 0, marginRight: 0
            }, sessionId);
            if (pr.error) throw new Error('Page.printToPDF 失败: ' + JSON.stringify(pr.error));
            buf = Buffer.from(pr.result.data, 'base64');
            pages = countPdfPages(buf);
            if (okPages(pages)) break;
            await sleep(700);
        }
        if (!okPages(pages)) {
            // 内容本身超出一页（长题干/长解析）属正常，按实际页数继续；少于期望才是真异常（页面丢了）
            if (expectPages && pages > expectPages) {
                console.log(`[警告] ${path.basename(htmlPath)}：内容超出分页符（期望 ${expectPages} 页，实际 ${pages} 页），按 ${pages} 页继续`);
            } else {
                throw new Error(`PDF 页数不符：期望 ${expectPages} 页（依据 ${strict ? '.page 元素' : '强制分页元素'}），实际 ${pages} 页（已重试 3 次）`);
            }
        }
        if (buf.length < 1000) throw new Error('printToPDF 输出异常，仅 ' + buf.length + ' 字节');
        fs.mkdirSync(path.dirname(pdfPath), { recursive: true });
        fs.writeFileSync(pdfPath, buf);
        return { bytes: buf.length, url, pages, expectPages };
    } finally {
        await browser.send('Target.closeTarget', { targetId });
    }
}

/* ---------------- 上传：复用网页端 uploadPdfAsNote ---------------- */
async function findToolboxPage() {
    const list = await (await fetch(CDP + '/json/list')).json();
    const t = list.find(x => x.type === 'page' && String(x.url).includes(TOOLBOX_PART));
    if (!t) throw new Error('找不到 toolbox 页面（' + TOOLBOX_PART + '），请确认 Chrome 已打开该页面');
    return t;
}

async function getAllNotes(cdp) {
    const v = await evalIn(cdp, `(async()=>{
        const r = await fetch(window.API_BASE_URL + '/CloudNotes/api/Notes/GetAll', {headers:{Authorization:'Bearer '+localStorage.getItem('token')}});
        const j = await r.json();
        if (!j || !j.data) return JSON.stringify({__err:'GetAll 返回异常: ' + JSON.stringify(j).slice(0,200)});
        const list = JSON.parse(aesDecrypt(j.data));
        return JSON.stringify((list.noteList || []).filter(n => n.type == 12).map(n => ({fileId:n.fileId, fileName:n.fileName, createTime:n.createTime})));
    })()`, null, true);
    const arr = JSON.parse(v);
    if (arr.__err) throw new Error(arr.__err);
    return arr;
}

/* ---------------- 登录态：临期自动刷新，必要时用账号密码重?---------------- */
const fmtTime = ms => (ms ? new Date(ms).toLocaleString('zh-CN', { hour12: false }) : '未知');
const AUTH_MARGIN_MS = 5 * 60 * 1000;   // 剩余不足 5 分钟就当作要续期

async function readAuthState(cdp) {
    const v = await evalIn(cdp, `(function(){
        var t = localStorage.getItem('token') || '';
        var exp = 0;
        try { exp = JSON.parse(atob(t.split('.')[1].replace(/-/g,'+').replace(/_/g,'/'))).exp * 1000; } catch (e) {}
        return JSON.stringify({
            token: t, exp: exp,
            refreshToken: localStorage.getItem('refreshToken') || '',
            refreshExp: parseInt(localStorage.getItem('refreshTokenExpire') || '0', 10) || 0,
            api: localStorage.getItem('apiBaseUrl') || ''
        });
    })()`);
    return JSON.parse(v);
}

// 把登?刷新结果写回 localStorage（与网页?login_btn 的写法保持一致）
async function storeAuth(cdp, res) {
    const exp = Date.now() + (res.expireInSeconds || 0) * 1000;
    const rexp = Date.now() + (res.refreshExpireInSeconds || 0) * 1000;
    await evalIn(cdp, `(function(){
        localStorage.setItem('token', ${JSON.stringify(res.accessToken || '')});
        localStorage.setItem('refreshToken', ${JSON.stringify(res.refreshToken || '')});
        localStorage.setItem('tokenExpire', ${JSON.stringify(String(exp))});
        localStorage.setItem('refreshTokenExpire', ${JSON.stringify(String(rexp))});
        if (localStorage.getItem('apiBaseUrl')) window.API_BASE_URL = localStorage.getItem('apiBaseUrl');
        return 'ok';
    })()`);
    return { exp, rexp };
}

/**
 * 确保页面里有可用 token。顺序：现成 token ?refreshToken 续期 ?账号密码登录? * 返回 { via: 'token'|'refresh'|'login', exp, refreshed }
 */
async function ensureAuth(cdp, { silent = false } = {}) {
    const say = m => { if (!silent) console.log(m); };
    let st = await readAuthState(cdp);

    if (st.token && st.exp - Date.now() > AUTH_MARGIN_MS) {
        say(`[登录] token 有效，剩余 ${Math.round((st.exp - Date.now()) / 60000)} 分钟`);
        return { via: 'token', exp: st.exp, refreshed: false };
    }
    const why = st.token
        ? `token 将于 ${fmtTime(st.exp)} 过期`
        : '页面里没有 token';
    say(`[登录] ${why}，尝试自动续期…`);

    // 1) refreshToken：不霢要密码，通常有效?7 ?
    if (st.refreshToken && st.refreshExp - Date.now() > 60000) {
        const rj = JSON.parse(await evalIn(cdp, `(async()=>{
            try {
                var api = localStorage.getItem('apiBaseUrl');
                var resp = await fetch(api + '/api/TokenAuth/RefreshToken', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'refreshtoken': localStorage.getItem('refreshToken'),
                        'Authorization': 'Bearer ' + localStorage.getItem('token')
                    }
                });
                var j = await resp.json();
                return JSON.stringify({ status: resp.status, body: j });
            } catch (e) { return JSON.stringify({ status: 0, body: { __err: String(e && e.message || e) } }); }
        })()`, null, true));
        if (rj.body && rj.body.result && rj.body.result.accessToken) {
            const r = await storeAuth(cdp, rj.body.result);
            say(`[登录] ✅ 已用 refreshToken 续期，新 token 至 ${fmtTime(r.exp)}`);
            return { via: 'refresh', exp: r.exp, refreshed: true };
        }
        say(`[登录] refreshToken 续期失败（HTTP ${rj.status}: ${JSON.stringify(rj.body && (rj.body.error || rj.body.__err || rj.body)).slice(0, 200)}）`);
        // 页面自带的定时器可能同时刷新过，重新读一次
        const st2 = await readAuthState(cdp);
        if (st2.token && st2.exp - Date.now() > AUTH_MARGIN_MS) {
            say(`[登录] 页面自身已刷新 token，继续（至 ${fmtTime(st2.exp)}）`);
            return { via: 'token', exp: st2.exp, refreshed: false };
        }
    } else {
        say(`[登录] refreshToken 不可用（有效期至 ${fmtTime(st.refreshExp)}）`);
    }

    // 2) 账号密码登录（凭据只?config.json ?auth 或环境变量读，不写进日志?
    const user = (CFG.auth && CFG.auth.userName) || process.env.ZY_USERNAME || '';
    const pass = (CFG.auth && CFG.auth.password) || process.env.ZY_PASSWORD || '';
    if (!user || !pass) {
        // 兜底：页面自身可能刚刚刷新过
        const st3 = await readAuthState(cdp);
        if (st3.token && st3.exp - Date.now() > AUTH_MARGIN_MS) {
            say(`[登录] 页面自身已刷新 token，继续（至 ${fmtTime(st3.exp)}）`);
            return { via: 'token', exp: st3.exp, refreshed: false };
        }
        throw new Error(
            `登录已过期且无法续期（token 至 ${fmtTime(st.exp)}，refreshToken 至 ${fmtTime(st.refreshExp)}）` +
            `可选：① 在浏览器里重新登录一次；② 在 config.json 里加 "auth": {"userName":"…","password":"…"}（或设环境变量 ZY_USERNAME/ZY_PASSWORD）以便自动重登。`
        );
    }
    const lj = JSON.parse(await evalIn(cdp, `(async()=>{
        try {
            var api = localStorage.getItem('apiBaseUrl') || window.API_BASE_URL;
            var resp = await fetch(api + '/api/TokenAuth/Login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ userName: ${JSON.stringify(user)}, password: ${JSON.stringify(pass)}, clientType: 1 })
            });
            var j = await resp.json();
            return JSON.stringify({ status: resp.status, body: j });
        } catch (e) { return JSON.stringify({ status: 0, body: { __err: String(e && e.message || e) } }); }
    })()`, null, true));
    if (!lj.body || !lj.body.result || !lj.body.result.accessToken) {
        const msg = (lj.body && lj.body.error && lj.body.error.message) || (lj.body && lj.body.__err) || JSON.stringify(lj.body).slice(0, 200);
        throw new Error(`账号密码登录失败（HTTP ${lj.status}）：${msg}`);
    }
    const r = await storeAuth(cdp, lj.body.result);
    say(`[登录] ✅ 已用账号 ${user} 重新登录，新 token 至 ${fmtTime(r.exp)}`);
    return { via: 'login', exp: r.exp, refreshed: true };
}

async function uploadPdf(cdp, pdfPath, noteTitle) {
    // 防后台降频：标签页被?hidden ?pdf.js 渲染可能永久卡住
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    // 0) 登录态（过期会自动刷?重登?
    await ensureAuth(cdp);

    const before = await getAllNotes(cdp);
    const seen = new Set(before.map(n => n.fileId));

    // 1) 注入 File 并触?change
    const b64 = fs.readFileSync(pdfPath).toString('base64');
    const fileName = path.basename(pdfPath);
    const info = await evalIn(cdp, `(async()=>{
        const bin = atob(${JSON.stringify(b64)});
        const u = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
        const f = new File([u], ${JSON.stringify(fileName)}, {type:'application/pdf'});
        let pages = 0;
        try { const doc = await pdfjsLib.getDocument({data: u.slice(0)}).promise; pages = doc.numPages; } catch (e) { pages = -1; }
        const dt = new DataTransfer(); dt.items.add(f);
        const input = document.getElementById('pdfFileInput');
        input.files = dt.files;
        input.dispatchEvent(new Event('change', {bubbles:true}));
        return JSON.stringify({pages, bytes: u.length});
    })()`, null, true);
    const { pages, bytes } = JSON.parse(info);

    // 2) 等缩略图就绪
    let strip = '';
    for (let i = 0; i < 60; i++) {
        await sleep(1000);
        strip = await evalIn(cdp, `(document.getElementById('pdfRotStatus')||{}).textContent||''`);
        if (/共\s*\d+\s*页/.test(String(strip))) break;
    }
    if (!/共\s*\d+\s*页/.test(String(strip))) throw new Error('缩略图准备超时（pdfRotStatus=' + strip + '），页面可能处于后台被降频，请把窗口切到前台');

    // 3) 写标题 + 点击上传
    const setName = await evalIn(cdp, `(function(){var el=document.getElementById('pdfNoteName'); if(!el) return 'no input'; el.value=${JSON.stringify(noteTitle)}; return el.value;})()`);
    if (setName !== noteTitle) throw new Error('设置笔记标题失败: ' + setName);

    const clicked = await evalIn(cdp, `(function(){var b=document.getElementById('btnUploadPdf'); if(!b) return 'no btn'; if(b.disabled) return 'disabled'; b.click(); return 'clicked';})()`);
    if (clicked !== 'clicked') throw new Error('点击上传失败: ' + clicked);

    // 4) 轮询进度
    let progress = '';
    for (let i = 0; i < 180; i++) {
        await sleep(1000);
        progress = await evalIn(cdp, `(document.getElementById('pdfProgressText')||{}).textContent||''`);
        if (/上传完成|失败|错误/.test(String(progress))) break;
    }

    await sleep(1500);
    const after = await getAllNotes(cdp);
    const fresh = after.filter(n => !seen.has(n.fileId));

    if (/失败|错误/.test(String(progress))) {
        throw new Error('上传失败（页面提示：' + String(progress).trim() + '）');
    }
    if (!/上传完成/.test(String(progress))) {
        throw new Error('上传未完成（最终状态：' + String(progress || '(空)').trim() + '）');
    }
    const hit = fresh.find(n => n.fileName === noteTitle) || fresh[0];
    if (!hit) throw new Error('页面显示上传完成，但 GetAll 里找不到新笔记');
    return { fileId: hit.fileId, title: hit.fileName, createTime: hit.createTime, pages, bytes, progress: String(progress).trim() };
}

/* ---------------- 工具 ---------------- */
function sha256File(p) {
    return crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
}
function loadState() {
    try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); } catch (_) { return {}; }
}
function saveState(s) {
    fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
    fs.writeFileSync(STATE_FILE, JSON.stringify(s, null, 2), 'utf8');
}
function titleNow() {
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    return `${d.getMonth() + 1}月${d.getDate()}日${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
const KB = n => (n / 1024).toFixed(1) + ' KB';

/* ---------------- 日志（仅 --watch 时落盘，按天切分?---------------- */
const LOG_DIR = path.resolve(ROOT, 'logs');
const pad2 = n => String(n).padStart(2, '0');
function appendLog(text) {
    try {
        const now = new Date();
        const ts = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())} ${pad2(now.getHours())}:${pad2(now.getMinutes())}:${pad2(now.getSeconds())}`;
        fs.mkdirSync(LOG_DIR, { recursive: true });
        const file = path.join(LOG_DIR, `watch-${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}.log`);
        const line = `[${ts}] ${text}\n`;
        // 新建文件时写 UTF-8 BOM，避?PowerShell 5.1 / 记事本按 ANSI 读成乱码
        if (!fs.existsSync(file)) fs.writeFileSync(file, '\ufeff' + line, 'utf8');
        else fs.appendFileSync(file, line, 'utf8');
    } catch (_) { /* 日志写失败不影响主流程 */ }
}

/* ---------------- 云笔记：棢?+ 下载（只读，不写云端?---------------- */
function loadCloudState() {
    try {
        const s = JSON.parse(fs.readFileSync(CLOUD_STATE, 'utf8'));
        if (!s || typeof s !== 'object') return { baselineAt: null, seen: {} };
        if (!s.seen || typeof s.seen !== 'object') s.seen = {};
        return s;
    } catch (_) {
        return { baselineAt: null, seen: {} };
    }
}

function saveCloudState(s) {
    fs.mkdirSync(path.dirname(CLOUD_STATE), { recursive: true });
    fs.writeFileSync(CLOUD_STATE, JSON.stringify(s, null, 2), 'utf8');
}

function safeName(s, max) {
    const t = String(s == null ? '' : s).replace(/[\\/:*?"<>|\r\n\t]/g, '_').replace(/\s+/g, ' ').trim();
    return (t || 'untitled').slice(0, max || 80);
}

// 云端全量列表（type=12 笔记、type=0 文件夹都要，用来按文件夹过滤?
async function fetchCloudNotes(cdp) {
    const raw = await evalIn(cdp, `(async () => {
        const r = await fetch(window.API_BASE_URL + '/CloudNotes/api/Notes/GetAll', { headers: { Authorization: 'Bearer ' + localStorage.getItem('token') } });
        const j = await r.json();
        if (!j || !j.data) return JSON.stringify({ __err: 'GetAll 返回异常: ' + JSON.stringify(j).slice(0, 200) });
        const d = JSON.parse(aesDecrypt(j.data)) || {};
        const list = d.noteList || [];
        return JSON.stringify(list.map(x => ({
            fileId: x.fileId, fileName: x.fileName, createTime: x.createTime, parentId: x.parentId, type: String(x.type)
        })));
    })()`, null, true);
    let arr;
    try { arr = JSON.parse(raw || '[]'); } catch (_) { throw new Error('GetAll 返回无法解析: ' + String(raw).slice(0, 200)); }
    if (arr && arr.__err) throw new Error(arr.__err);
    if (!Array.isArray(arr)) throw new Error('GetAll 返回不是数组');
    return arr;
}

// ?--cloud-folder 的解析成 parentId：既支持直接?id，也支持给文件夹?
function resolveCloudScope(all, spec) {
    const s = String(spec || '').trim();
    // 空 / '0' / 'root' 都表示云端根目录（GetAll 里根目录的 parentId 就是 '0'）
    if (!s || s === '0' || /^root$/i.test(s)) return { parentId: '0', spec: s, desc: '根目录' };

    const byId = all.find(n => String(n.fileId) === s);
    if (byId) return { parentId: byId.fileId, spec: s, desc: `${byId.fileName}(${byId.fileId})` };

    const named = all.filter(n => String(n.fileName) === s);
    const folder = named.find(n => n.type === '0') || named[0];
    if (folder) return { parentId: folder.fileId, spec: s, desc: `${folder.fileName}(${folder.fileId})` };

    return { parentId: null, spec: s, missing: s, desc: s };
}

// 单条笔记的资源清单（页面内解密，只取霢要的字段?
async function fetchResourceList(cdp, fileId) {
    const raw = await evalIn(cdp, `(async () => {
        const url = getCloudNoteApiPathR('GetByFileId', aesEncrypt('fileId=' + ${JSON.stringify(String(fileId))}));
        const r = await fetch(url, { headers: { Authorization: 'Bearer ' + localStorage.getItem('token') } });
        const j = await r.json();
        if (!j || !j.data) return JSON.stringify({ __err: 'GetByFileId 返回异常: ' + JSON.stringify(j).slice(0, 200) });
        const list = (JSON.parse(aesDecrypt(j.data)).resourceList) || [];
        return JSON.stringify(list.map(x => ({
            page: x.pageIndex, name: x.pageName, type: x.resourceType, url: x.ossImageUrl, md5: x.md5
        })));
    })()`, null, true);
    let arr;
    try { arr = JSON.parse(raw || '[]'); } catch (_) { throw new Error('GetByFileId 返回无法解析: ' + String(raw).slice(0, 200)); }
    if (arr && arr.__err) throw new Error(arr.__err);
    return Array.isArray(arr) ? arr : [];
}

// 取一?OSS 对象的字节：?Node 直连（带 Referer），失败再回逢到页面内 fetch
async function fetchOssBuffer(cdp, url) {
    try {
        const r = await fetch(url, { headers: { Referer: 'https://www.example.com/' } });
        if (r.ok) return Buffer.from(await r.arrayBuffer());
    } catch (_) { /* 落到页面内 fetch */ }
    const b64 = await evalIn(cdp, `(async () => {
        const r = await fetch(${JSON.stringify(url)});
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const b = new Uint8Array(await r.arrayBuffer());
        let s = ''; const CH = 0x8000;
        for (let i = 0; i < b.length; i += CH) s += String.fromCharCode.apply(null, b.subarray(i, i + CH));
        return btoa(s);
    })()`, null, true);
    if (!b64) throw new Error('页面内 fetch 未取到数据');
    return Buffer.from(b64, 'base64');
}

// 下载丢条笔记的全部资源?cloud/<时间>_<标题>_<fileId8>/
async function downloadOne(cdp, note) {
    const rows = await fetchResourceList(cdp, note.fileId);
    if (!rows.length) throw new Error('资源清单为空');

    const stamp = String(note.createTime || '').replace(/[^\d]/g, '').slice(0, 14) || 'nodate';
    const dir = path.join(CLOUD_DIR, `${stamp}_${safeName(note.fileName, 40)}_${String(note.fileId).slice(0, 8)}`);
    fs.mkdirSync(dir, { recursive: true });

    const seenUrl = new Map();   // 模板/缩略图跨页复用，同一对象只落一份
    const usedName = new Map();  // local 文件名 -> 计数，避免同名覆盖
    const pageDir = new Map();   // pageIndex -> 该页对象所在的 OSS 目录（补相对 URL）
    const manifest = [];
    let bytes = 0, files = 0;
    const pages = new Set();

    for (const r of rows) {
        pages.add(r.page);
        // 个别资源（如 page_router.bin）的 ossImageUrl 只有文件名，用同页其它资源的目录补齐
        if (r.url && /^https?:\/\//i.test(r.url)) {
            pageDir.set(r.page, String(r.url).slice(0, String(r.url).lastIndexOf('/') + 1));
        }
        if (r.url && !/^https?:\/\//i.test(r.url) && pageDir.has(r.page)) {
            r.url = pageDir.get(r.page) + String(r.url).replace(/^\/+/, '');
        }
        if (!r.url) { manifest.push({ ...r, local: null, reason: '无 URL' }); continue; }
        if (seenUrl.has(r.url)) { manifest.push({ ...r, local: seenUrl.get(r.url), reason: '重复对象' }); continue; }

        // 文件名取 URL 末段（screenshot.png / snapshot.bin / xxx_matrix.bin …）?        // pageName 是笔记目录路径，同页多个资源会重?
        let base = 'file';
        try { base = decodeURIComponent(String(r.url).split('?')[0].split('/').pop() || 'file'); } catch (_) { }
        base = safeName(base, 200);
        // 长文件名截断时保留扩展名，方便双击打弢
        {
            const dot = base.lastIndexOf('.');
            const ext = dot > 0 ? base.slice(dot) : '';
            if (base.length > 74) base = base.slice(0, Math.max(20, 74 - ext.length)) + ext;
        }
        const stem = `${String(r.page).padStart(2, '0')}_${base}`;
        const n = (usedName.get(stem) || 0) + 1;
        usedName.set(stem, n);
        const local = n === 1 ? stem : stem.replace(/(\.[^.]*)?$/, `_${n}$1`);
        try {
            const buf = await fetchOssBuffer(cdp, r.url);
            fs.writeFileSync(path.join(dir, local), buf);
            seenUrl.set(r.url, local);
            manifest.push({ ...r, local, bytes: buf.length });
            bytes += buf.length; files++;
        } catch (e) {
            manifest.push({ ...r, local: null, reason: e.message });
        }
    }

    fs.writeFileSync(path.join(dir, 'resources.json'), JSON.stringify({
        fileId: note.fileId, fileName: note.fileName, createTime: note.createTime,
        pages: pages.size, rows: rows.length, files, bytes,
        resources: manifest
    }, null, 2), 'utf8');

    return { dir, pages: pages.size, rows: rows.length, files, bytes };
}

// 丢轮云笔记棢查：首次只建基线；之后只处理「目标文件夹内的新增
async function cloudRound(cdp) {
    const cst = loadCloudState();
    const all = await fetchCloudNotes(cdp);

    const scope = resolveCloudScope(all, CLOUD_FOLDER);
    if (scope.missing) {
        console.log(`[云笔记] 找不到文件夹「${scope.spec}」，本轮跳过（用 --cloud-folder <文件夹名|parentId> 指定目标）`);
        return { added: 0, downloaded: 0, failed: 0 };
    }
    const kw = CLOUD_TITLE.toLowerCase();
    const notes = all.filter(n => n.type === '12'
        && String(n.parentId) === String(scope.parentId)
        && (!kw || String(n.fileName || '').toLowerCase().includes(kw)));
    const flt = `范围=${scope.desc}${kw ? `，标题含「${CLOUD_TITLE}」` : ''}`;

    if (!CLOUD_ALL && !cst.baselineAt) {
        cst.baselineAt = new Date().toISOString();
        for (const n of notes) cst.seen[n.fileId] = { at: cst.baselineAt, title: n.fileName, ver: n.version, upd: n.updateTime, baseline: true };
        saveCloudState(cst);
        console.log(`[云笔记] 建立基线：${flt}，共 ${notes.length} 条笔记（不回补历史；要回补加 --cloud-all）`);
        return { added: 0, downloaded: 0, failed: 0 };
    }

    // 没见过 = 新增；见过但标题/版本变了 = 有变化（改标题、改内容都会命中）
    const changeOf = n => {
        const rec = cst.seen[n.fileId];
        if (!rec) return '新增';
        if (rec.title !== undefined && rec.title !== n.fileName) return '标题变化';
        if (rec.ver !== undefined && n.version !== undefined && String(rec.ver) !== String(n.version)) return '版本变化';
        return null;
    };
    const reasons = new Map();
    const fresh = notes.filter(n => { const r = changeOf(n); if (r) reasons.set(n.fileId, r); return !!r; });
    if (!fresh.length) {
        console.log(`[云笔记] 无变化（${flt}，共 ${notes.length} 条）`);
        return { added: 0, downloaded: 0, failed: 0 };
    }

    const take = fresh.slice(0, CLOUD_LIMIT);
    console.log(`[云笔记] 待处理 ${fresh.length} 条（${flt}），本轮下载 ${take.length} 条：` +
        take.map(n => `${reasons.get(n.fileId)}「${n.fileName}」`).join('、'));
    let ok = 0, bad = 0;
    for (const n of take) {
        try {
            const r = await downloadOne(cdp, n);
            console.log(`[云笔记] ✅ ${n.fileName}  ${r.pages} 页 / ${r.rows} 条 / 落盘 ${r.files} 个（${(r.bytes / 1048576).toFixed(2)} MB）→ ${path.relative(ROOT, r.dir)}`);
            cst.seen[n.fileId] = { at: new Date().toISOString(), title: n.fileName, ver: n.version, upd: n.updateTime, dir: path.relative(ROOT, r.dir), files: r.files, bytes: r.bytes };
            saveCloudState(cst);
            ok++;
        } catch (e) {
            console.log(`[云笔记] ❌ ${n.fileName}（${n.fileId}）: ${e.message}（保持未处理，下轮再试）`);
            bad++;
        }
    }
    return { added: fresh.length, downloaded: ok, failed: bad };
}

/* ---------------- 单文件处理（单次运行?watch 共用?---------------- */
async function processOne(browser, cdpPage, h, state) {
    const htmlPath = path.join(OUT_DIR, h);
    const base = h.replace(/\.html?$/i, '');
    const pdfPath = path.join(PDF_DIR, base + '.pdf');
    const rel = path.relative(ROOT, htmlPath).replace(/\\/g, '/');
    const sha = sha256File(htmlPath);
    const prev = state[rel];

    if (!FORCE && prev && prev.htmlSha === sha && prev.note && prev.note.fileId) {
        console.log(`[跳过] ${h}  ⟵ 已上传过（fileId=${prev.note.fileId}），HTML 未变。要重传加 --force`);
        return { html: h, pdf: prev.pdf, pages: prev.pages, bytes: prev.pdfBytes, note: prev.note, skipped: true };
    }

    const t0 = Date.now();
    let conv;
    try {
        conv = await convertOne(browser, htmlPath, pdfPath);
    } catch (e) {
        console.log(`[失败-转换] ${h}\n    文件: ${htmlPath}\n    函数: convertOne → Page.printToPDF\n    错误: ${e.message}`);
        return { html: h, failed: 'convert', error: e.message };
    }
    console.log(`[转换] ${h} → ${path.basename(pdfPath)}  ${KB(conv.bytes)}  ${Date.now() - t0}ms`);

    if (DRY) return { html: h, pdf: path.relative(ROOT, pdfPath), bytes: conv.bytes, pages: conv.pages, note: null };

    const title = titleNow();
    try {
        const note = await uploadPdf(cdpPage, pdfPath, title);
        state[rel] = { htmlSha: sha, pdf: path.relative(ROOT, pdfPath).replace(/\\/g, '/'), pages: note.pages, pdfBytes: note.bytes, note: { fileId: note.fileId, title: note.title, createTime: note.createTime }, at: new Date().toISOString() };
        saveState(state);
        console.log(`[上传] ✅ 笔记「${note.title}」  页=${note.pages}  PDF=${KB(note.bytes)}  fileId=${note.fileId}  ${Date.now() - t0}ms`);
        return { html: h, pdf: path.relative(ROOT, pdfPath), pages: note.pages, bytes: note.bytes, note };
    } catch (e) {
        console.log(`[失败-上传] ${h}\n    文件: ${pdfPath}\n    函数: uploadPdf → uploadPdfAsNote\n    错误: ${e.message}`);
        return { html: h, failed: 'upload', error: e.message };
    }
}

async function openTargets() {
    const browser = await Cdp.open((await (await fetch(CDP + '/json/version')).json()).webSocketDebuggerUrl);
    const cdpPage = await Cdp.open((await findToolboxPage()).webSocketDebuggerUrl);
    return { browser, cdpPage };
}

function printRows(rows) {
    console.log('\n===== 结果清单 =====');
    for (const r of rows) {
        if (r.failed) console.log(`${r.html}\t(失败:${r.failed})\t${r.error}`);
        else if (r.skipped) console.log(`${r.html}\t(跳过)\t页数=${r.pages ?? '?'}\t${KB(r.bytes || 0)}\tfileId=${r.note.fileId}`);
        else if (r.note) console.log(`${r.html}\t${r.pdf}\t页数=${r.pages}\t${KB(r.bytes)}\t笔记「${r.note.title}」\tfileId=${r.note.fileId}`);
        else console.log(`${r.html}\t${r.pdf}\t${KB(r.bytes)}\t(未上传)`);
    }
}

/* ---------------- 单次运行 ---------------- */
async function runOnce() {
    const htmls = listHtml();
    if (!htmls.length) { console.log('没有待处理的 HTML（目录：' + OUT_DIR + '）'); return 0; }
    console.log(`HTML 目录: ${OUT_DIR}`);
    console.log(`PDF 目录 : ${PDF_DIR}`);
    console.log(`待处理  : ${htmls.length} 个${DRY ? '（--dry 只转换）' : ''}${FORCE ? '（--force 强制重传）' : ''}\n`);

    const state = loadState();
    const { browser, cdpPage } = await openTargets();
    const rows = []; let failed = 0;
    try {
        for (const h of htmls) {
            const r = await processOne(browser, DRY ? null : cdpPage, h, state);
            rows.push(r);
            if (r.failed) failed++;
        }
    } finally {
        browser.close();
        if (cdpPage) cdpPage.close();
    }
    printRows(rows);
    console.log(`\n合计: 处理 ${rows.length} 个，失败 ${failed} 个`);
    return failed;
}

/* ---------------- 常驻：目录监?+ 周期 rescan ---------------- */
function listHtml() {
    if (!fs.existsSync(OUT_DIR)) return [];
    let htmls = fs.readdirSync(OUT_DIR).filter(f => /\.html?$/i.test(f)).sort();
    if (ONLY) htmls = htmls.filter(f => f.includes(ONLY));
    return htmls;
}

// 是否值得处理：没记录过或 HTML 内容变了?-force 时一律处理）
function needsWork(h, state) {
    if (FORCE) return true;
    const htmlPath = path.join(OUT_DIR, h);
    const rel = path.relative(ROOT, htmlPath).replace(/\\/g, '/');
    const prev = state[rel];
    if (!prev || !prev.note || !prev.note.fileId) return true;
    try { return sha256File(htmlPath) !== prev.htmlSha; } catch (_) { return false; }
}

async function runWatch() {
    fs.mkdirSync(PDF_DIR, { recursive: true });

    // 进入常驻后，控制台输出同时写入按天切分的日志
    const origLog = console.log.bind(console), origErr = console.error.bind(console);
    console.log = (...a) => { origLog(...a); appendLog(a.join(' ')); };
    console.error = (...a) => { origErr(...a); appendLog('[ERR] ' + a.join(' ')); };

    if (FORCE) console.log('[提示] --watch 下忽略 --force（避免同一个文件被无限重传），需要重传请先退出再单跑 --force');

    const state = loadState();
    const queue = [];
    const done = new Set();          // 已处理过的「文件@内容哈希」，内容变了会重新处理
    const failCount = new Map();     // 连续失败次数（按 文件@哈希）
    let pumping = null;
    let stopping = false;

    const keyOf = h => {
        try { return h + '@' + sha256File(path.join(OUT_DIR, h)); } catch (_) { return h; }
    };

    // 只把「需要处理的新文件入队，返回新入队数?
    const enqueue = list => {
        let n = 0;
        for (const h of list) {
            const k = keyOf(h);
            if (done.has(k) || queue.includes(h)) continue;
            if (!needsWork(h, state)) {
                console.log(`[跳过] ${h}  ⟵ 已上传过，HTML 未变`);
                done.add(k);
                continue;
            }
            queue.push(h); n++;
        }
        return n;
    };

    function pump() {
        if (pumping || stopping) return pumping;
        const p = (async () => {
            while (queue.length && !stopping) {
                const h = queue.shift();
                if (!fs.existsSync(path.join(OUT_DIR, h))) { console.log(`[跳过] ${h} 已不存在`); done.add(h); continue; }
                let t = null;
                try { t = await openTargets(); } catch (e) { console.log(`[等待] 无法连接浏览器/页面：${e.message}（30 秒后重试）`); queue.unshift(h); await sleep(30000); continue; }
                let r = null;
                try {
                    r = await processOne(t.browser, DRY ? null : t.cdpPage, h, state);
                } catch (e) {
                    console.log(`[异常] ${h}: ${e.message}`);
                    r = { failed: 'exception', error: e.message };
                } finally {
                    t.browser.close();
                    if (t.cdpPage) t.cdpPage.close();
                }
                const k = keyOf(h);
                if (r && r.failed) {
                    const c = (failCount.get(k) || 0) + 1;
                    failCount.set(k, c);
                    if (c >= 3) { console.log(`[跳过] ${h} 已连续失败 ${c} 次，等 HTML 变化后再试`); done.add(k); }
                } else {
                    failCount.delete(k);
                    done.add(k);
                }
            }
        })();
        pumping = p;
        // 必须在微任务里清空：若队列一弢始就是空的，async 函数体会同步跑完
        p.then(() => { if (pumping === p) pumping = null; }, () => { if (pumping === p) pumping = null; });
        return p;
    }

    const n0 = enqueue(listHtml());
    console.log(`[启动] 监听 ${OUT_DIR}`);
    console.log(`[启动] 周期 rescan 间隔 ${WATCH_INTERVAL} ms；日志: ${path.join(LOG_DIR, 'watch-YYYY-MM-DD.log')}`);
    console.log(`[启动] 当前待处理 ${n0} 个${DRY ? '（--dry 只转换）' : ''}`);

    // 启动时先确认登录态，避免等第丢个文件才发现过期（开?--cloud 时由云笔记那丢轮负责）
    if (!CLOUD) {
        try {
            const t = await openTargets();
            try { await ensureAuth(t.cdpPage); } finally { t.browser.close(); t.cdpPage.close(); }
        } catch (e) {
            console.log(`[登录] 检查失败：${e.message}（有文件要上传时会再试）`);
        }
    }

    // 云笔记：只读棢?+ 下载。启动先跑一轮，之后挂在 rescan 分支里按 CLOUD_INTERVAL 节流
    let cloudNextAt = 0;
    let cloudBusy = false;
    const runCloud = async (why) => {
        if (stopping || cloudBusy) return;
        cloudBusy = true;
        let t = null;
        try {
            t = await openTargets();
            // 每 30 秒一轮，token 还够用就别刷「登录」日志；快过期时才走续期
            const st0 = await readAuthState(t.cdpPage);
            if (!st0.token || st0.exp - Date.now() <= AUTH_MARGIN_MS) {
                await ensureAuth(t.cdpPage);
            }
            await cloudRound(t.cdpPage);
        } catch (e) {
            console.log(`[云笔记] 本轮出错：${e.message}`);
        } finally {
            cloudBusy = false;
            cloudNextAt = Date.now() + CLOUD_INTERVAL;
            if (t) {
                try { t.browser.close(); } catch (_) { }
                try { t.cdpPage.close(); } catch (_) { }
            }
        }
    };
    if (CLOUD) {
        await runCloud('启动');
        console.log(`[启动] 云笔记检查已启动：每 ${Math.round(CLOUD_INTERVAL / 1000)} 秒一轮，范围=${CLOUD_FOLDER || '根目录'}，下载标题含「${CLOUD_TITLE || '(不限)'}」的笔记至 ${CLOUD_DIR}`);
    }

    // fs.watch：文件系统事?
    let debounce = null;
    const watcher = fs.watch(OUT_DIR, { persistent: true }, (evt, fn) => {
        if (fn && !/\.html?$/i.test(String(fn))) return;
        clearTimeout(debounce);
        debounce = setTimeout(() => {
            const n = enqueue(listHtml());
            if (n) console.log(`[事件] ${evt} ${fn || '(未知)'} → 入队 ${n} 个`);
            pump();
        }, 1000);
    });
    watcher.on('error', e => console.log('[监听错误] ' + e.message));

    // 周期 rescan：兜?fs.watch 漏事件；弢?--check-cloud 时顺带做云笔记检查（?CLOUD_INTERVAL 节流?
    const timer = setInterval(() => {
        const n = enqueue(listHtml());
        if (n) console.log(`[扫描] 发现 ${n} 个待处理文件（目录内共 ${listHtml().length} 个 HTML）`);
        pump();
        if (CLOUD && !stopping && !cloudBusy && Date.now() >= cloudNextAt) {
            runCloud('rescan');
        }
    }, WATCH_INTERVAL);

    const shutdown = sig => {
        if (stopping) return;
        stopping = true;
        clearInterval(timer);
        clearTimeout(debounce);
        try { watcher.close(); } catch (_) { }
        console.log(`[退出] 收到 ${sig}，处理完当前文件后?出?`);
        const wait = setInterval(() => {
            if (!pumping) { clearInterval(wait); console.log('[退出] 已停止'); process.exit(0); }
        }, 200);
        setTimeout(() => { console.log('[退出] 超时强制退出'); process.exit(0); }, 120000);
    };
    process.on('SIGINT', () => shutdown('SIGINT'));
    process.on('SIGTERM', () => shutdown('SIGTERM'));

    pump();
}

/* ---------------- 入口 ---------------- */
(async () => {
    if (!fs.existsSync(OUT_DIR)) throw new Error('HTML 目录不存在: ' + OUT_DIR);

    // 只检?续期登录态，不做任何转换与上?
    if (ARGS.includes('--check-auth')) {
        const { cdpPage, browser } = await openTargets();
        try {
            const r = await ensureAuth(cdpPage);
            console.log(`[登录] 结束：来源=${r.via}，token 至 ${fmtTime(r.exp)}${r.refreshed ? '（本次已续期）' : ''}`);
        } finally {
            browser.close(); cdpPage.close();
        }
        return;
    }

    if (WATCH) { await runWatch(); return; }

    // 单跑：只做一轮云笔记棢?+ 下载
    if (CLOUD) {
        const t = await openTargets();
        try {
            await ensureAuth(t.cdpPage);
            await cloudRound(t.cdpPage);
        } finally {
            t.browser.close(); t.cdpPage.close();
        }
        return;
    }

    const failed = await runOnce();
    if (failed) process.exitCode = 1;
})().catch(e => { console.error('致命错误:', e.message || e); process.exit(1); });
