/**
 * PDF上传云笔记功能模块
 * 资源结构对齐「中育Toolbox 应用版」(NoteView 的 rt()/Ge())：
 * 逐页改写模板 bin 的 SHA-256 摘要链、md5 按真实内容算、resourceType 用固定语义、
 * 模板保留 <uuid>/ 子目录。早期版本照抄 core.py 的固定摘要/固定 md5，
 * 笔记能显示但无法被云笔记 App 导出或用别的应用打开。
 */

// ==================== 全局状态 ====================
let pdfImages = [];           // 存储PDF转换后的图片（blob数组）
let currentPdfFile = null;    // 当前选择的PDF文件
let templateFilesCache = null; // 缓存模板文件

// ==================== 初始化 ====================
document.addEventListener('DOMContentLoaded', function() {
    const pdfjs = window.pdfjsLib || window.pdfjs;
    if (typeof pdfjs !== 'undefined') {
        // 必须用同源 worker：跨域 new Worker 会被浏览器直接拒绝，pdf.js 退回 fake worker 后
        // 仍要去 workerSrc 抓脚本，那一步没有超时——cdnjs 一慢就永远停在“15% 正在转换PDF”。
        // 仓库根目录自带 pdf.worker.min.js（与 pdf.min.js 同版本 3.11.174）。
        pdfjs.GlobalWorkerOptions.workerSrc = 'pdf.worker.min.js';
    } else {
        console.warn('PDF.js 未加载');
    }
});

// ==================== 基础工具函数 ====================

function generateCustomFileId(prefix, length) {
    prefix = prefix || 'h';
    length = length || 32;
    const hexChars = '0123456789abcdef';
    const extraChars = 'ghijklmnopqrstuvwxyz';
    const allChars = hexChars + extraChars;
    while (true) {
        let body = '';
        for (let i = 0; i < length; i++) {
            body += allChars.charAt(Math.floor(Math.random() * allChars.length));
        }
        if (/[g-z]/.test(body)) {
            return prefix + body;
        }
    }
}

function generatePageHash() {
    // Python: int(time.time() * 1000) = 毫秒时间戳
    // JS: Date.now() 已返回毫秒，无需再乘1000
    return String(Date.now() + Math.floor(Math.random() * 1000));
}

// ==================== codeUtils.encode() JS实现 ====================

function generateAesKey() {
    var e = window.readCodeUtilsSeed();
    var t = new Date();
    var n = t.getFullYear();
    var r = t.getMonth() + 1;
    var o = t.getDate();
    var i = 33 + o * r * 33;
    var a = String.fromCharCode(i % 94 + 33);
    var s = e[o + r];
    var c = n * r * o % e.length;
    var f = (e.substring(c) + e.substring(0, c)).substring(0, 14);
    return a + f + s;
}

function codeUtilsEncode(dataStr) {
    var key = CryptoJS.enc.Utf8.parse(generateAesKey());
    return CryptoJS.AES.encrypt(dataStr, key, {
        mode: CryptoJS.mode.ECB,
        padding: CryptoJS.pad.Pkcs7
    }).toString();
}

// ==================== 模板文件加载 ====================

function base64ToBlob(b64, mime) {
    var bin = atob(b64);
    var len = bin.length;
    var bytes = new Uint8Array(len);
    for (var i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: mime || 'application/octet-stream' });
}

async function loadTemplateFiles() {
    if (templateFilesCache) return templateFilesCache;

    var base = 'example/';
    var files = [
        'page_router.bin',
        'a888b5fb-e65d-4611-a3af-1f80a0fb6ced/059848e4-1971-47fb-9e47-517266cdef05_matrix.bin',
        'a888b5fb-e65d-4611-a3af-1f80a0fb6ced/a2b4fb47-3623-45be-9fe9-57fc62e66651_file.bin',
        'a888b5fb-e65d-4611-a3af-1f80a0fb6ced/e339e39b-64d9-4de0-bfaa-dace2a3f8e7d_command.bin',
        'a888b5fb-e65d-4611-a3af-1f80a0fb6ced/header.bin',
        'a888b5fb-e65d-4611-a3af-1f80a0fb6ced/router.bin',
        // 注意：这里故意不传 'screenshot.png'。模板里那张是写着「中育Toolbox / By Loshop」的
        // 占位图，云笔记预览里显示的"水印"就是它；缩略图改由第 1 页的真实渲染生成。
        'a888b5fb-e65d-4611-a3af-1f80a0fb6ced/snapshot.bin'
    ];

    var inline = window.PDF_TEMPLATES || {};
    var inlineMime = window.PDF_TEMPLATES_MIME || {};
    var cache = {};

    for (var i = 0; i < files.length; i++) {
        var f = files[i];
        var blob = null;
        // 1) 优先读同目录文件（http 下最直接）
        try {
            var resp = await fetch(base + f);
            if (resp.ok) blob = await resp.blob();
        } catch (e) {
            // file:// 下浏览器会拒绝 fetch 本地文件，落到内嵌副本
        }
        // 2) 内嵌副本（pdf-templates.js），保证 file:// 也能用
        if (!blob && inline[f]) {
            blob = base64ToBlob(inline[f], inlineMime[f]);
        }
        if (!blob) throw new Error('加载模板文件失败: ' + f);
        cache[f] = blob;
    }

    templateFilesCache = cache;
    return templateFilesCache;
}

// ==================== OSS上传（调用index.js的uploadFile）====================

async function uploadToOss(blob, userId, nonce, filename) {
    if (typeof uploadFile !== 'function') {
        throw new Error('uploadFile 函数不可用，请确保已登录');
    }
    // uploadFile(file/blob, userId, fc, nonceInput, fileNameInput)
    return await uploadFile(blob, userId, 'note_v2', nonce, filename);
}

// ==================== PDF转图片（使用服务端 pdf2img）====================

/**
 * 取二进制：先走本地代理 / 仍存活的中转，再退直链。
 * 为什么要先代理：pdf2img 返回的图片在 OSS 上，没有 Access-Control-Allow-Origin，
 * 浏览器直连 fetch 会被 CORS 拦掉（以前靠那个已失效的公网中转绕，现在中转没了）。
 * @param {string} url
 * @param {{image?: boolean}} opts
 */
async function fetchViaProxy(url, opts) {
    var wantImage = !!(opts && opts.image);
    var tries = [];
    var push = (u) => { if (u && tries.indexOf(u) === -1) tries.push(u); };

    // 1) 同源 /proxy/（页面本身就是 http 服务时一定有，不依赖探活时序）
    try {
        if (location.protocol === 'http:' || location.protocol === 'https:') {
            push(location.origin + '/proxy/' + url);
        }
    } catch (e) { /* ignore */ }

    // 2) index.js 选出来的代理（本地 5005 / 仍存活的公网中转）
    try {
        if (typeof window.proxyUrl === 'function') {
            push(window.proxyUrl(url));
        }
    } catch (e) { /* ignore */ }

    // 3) 最后才直链（OSS 上多数图片没有 CORS 头，通常会失败）
    push(url);

    var lastErr = null;
    for (var i = 0; i < tries.length; i++) {
        try {
            var resp = await fetch(tries[i]);
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var ct = (resp.headers.get('content-type') || '').toLowerCase();
            if (wantImage && ct && ct.indexOf('image/') !== 0) {
                throw new Error('返回 ' + ct + '，不是图片');
            }
            return await resp.blob();
        } catch (e) {
            lastErr = e;
        }
    }
    throw (lastErr || new Error('取二进制失败'));
}

// ==================== PDF 转图片 ====================
// 有 pdf.js 就本地渲染：不联网、没有 CORS 问题，file:// 打开也能用；
// 只有拿不到 pdf.js 时才退回服务端 pdf2img（那条路依赖同源/本地代理取图）。

async function convertPdfToImages(pdfFile, onProgress, autoRotate) {
    if (window.pdfjsLib && typeof window.pdfjsLib.getDocument === 'function') {
        return await renderPdfLocally(pdfFile, onProgress, autoRotate);
    }
    // 服务端转换（pdf2img）拿不到页面朝向控制，autoRotate 在本地渲染链路才生效
    return await convertPdfToImagesServer(pdfFile, onProgress);
}

// 笔记页画布尺寸：取自模板 example/res/image/*.webp 两张样例，均为 2880x1800
var CANVAS_W = 2880;
var CANVAS_H = 1800;

// ---------------- 自动旋转 ----------------
// 画布固定 16:10 横版（2880x1800），PDF 页面可能横竖混排。
// 老版本按“转 0°/90° 谁把画布填得更满”判定，结果任何竖版页（如 A4 595x842）
// 都会被转 90°，App 里看到的就是整页文字侧躺——旋转功能反而是坏的。
// 现在只看页面内容本身，只有“内容确实横躺着”才转：
//   1) 有文字层 → 统计文字基线在屏幕上的朝向，把页面转正（0/90/180/270）；
//   2) 无文字层（扫描/纯图片页）→ 看非白像素包围盒：竖版页里只有一条横向内容带时转 90°；
//   3) 判断不了就不转（宁可少转，不可转错）。
// 旋转走 pdf.js 的 viewport rotation（重新排版再渲染），不是把位图二次插值。
var ROT_ANGLE_TOLERANCE = 20;   // 文字角度离 0/90/180/270 在多少度内仍算该朝向
var ROT_MIN_ASPECT = 1.15;      // 扫描页包围盒的长宽比阈值
var ROT_SCAN_SAMPLE = 480;      // 扫描页检测时的取样画布长边（像素）

// 屏幕上文字看起来转了 a 度（顺时针）时，需要补多少度才能转正
function uprightRotation(screenAngle) {
    var a = ((Math.round(screenAngle / 90) * 90) % 360 + 360) % 360;
    return (360 - a) % 360;
}

// 读文字层，返回页面在“屏幕上”的文字主朝向（0/90/180/270）；读不到返回 null
async function textScreenAngle(page, baseRot) {
    var tc = null;
    try {
        tc = await page.getTextContent();
    } catch (e) {
        return null;
    }
    var items = (tc && tc.items) || [];
    var score = { 0: 0, 90: 0, 180: 0, 270: 0 };
    var used = 0;
    for (var i = 0; i < items.length; i++) {
        var t = items[i].transform;
        if (!t || t.length < 4) continue;
        if (!(Math.abs(t[0]) + Math.abs(t[1]) > 0)) continue;
        // 文字基线角度 + 页面自带 /Rotate = 显示角度（pdf.js 的 rotation 为顺时针）
        var deg = Math.atan2(t[1], t[0]) * 180 / Math.PI + (baseRot || 0);
        var snap = ((Math.round(deg / 90) * 90) % 360 + 360) % 360;
        if (Math.abs(((deg - snap + 540) % 360) - 180) > ROT_ANGLE_TOLERANCE) continue;
        score[snap] += (items[i].str ? items[i].str.length : 1) || 1;
        used++;
    }
    if (!used) return null;
    // 只有水印（一行 8pt 小字）的页面不能拿它定朝向：统计字符数与文字面积占比，
    // 占比不足 1% 视为“没有可用的文字层”，交给下面的像素兜底（scan/贴图页常见）。
    var chars = 0, area = 0;
    for (var j = 0; j < items.length; j++) {
        var it = items[j];
        if (!it || !it.str) continue;
        chars += it.str.length;
        area += Math.abs(it.width || 0) * Math.abs(it.height || 0);
    }
    var view = page.view || [0, 0, 612, 792];
    var pageArea = Math.abs((view[2] - view[0]) * (view[3] - view[1])) || 1;
    if (chars < 8 || area / pageArea < 0.01) return null;
    var best = 0, bestScore = -1;
    for (var k in score) {
        if (score[k] > bestScore) { bestScore = score[k]; best = +k; }
    }
    return best;
}

// 无文字层时：量非白像素包围盒，判断内容是不是横躺在竖版页里
async function inkScreenAngle(page, baseRot) {
    var probe = page.getViewport({ scale: 1, rotation: baseRot || 0 });
    var scale = ROT_SCAN_SAMPLE / Math.max(probe.width, probe.height);
    var vp = page.getViewport({ scale: scale, rotation: baseRot || 0 });
    var canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.ceil(vp.width));
    canvas.height = Math.max(1, Math.ceil(vp.height));
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    try {
        await page.render({ canvasContext: ctx, viewport: vp }).promise;
    } catch (e) {
        return 0;
    }
    var data;
    try {
        data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    } catch (e) {
        return 0;
    }
    var minX = canvas.width, minY = canvas.height, maxX = -1, maxY = -1;
    for (var y = 0; y < canvas.height; y++) {
        for (var x = 0; x < canvas.width; x++) {
            var o = (y * canvas.width + x) * 4;
            if (data[o] < 240 || data[o + 1] < 240 || data[o + 2] < 240) {
                if (x < minX) minX = x;
                if (x > maxX) maxX = x;
                if (y < minY) minY = y;
                if (y > maxY) maxY = y;
            }
        }
    }
    if (maxX < 0 || maxY < 0) return 0;   // 空白页
    var bw = maxX - minX + 1, bh = maxY - minY + 1;
    if (canvas.height > canvas.width && bw / bh >= ROT_MIN_ASPECT) return 90;
    return 0;
}

// 返回该页需要额外旋转的角度（0/90/180/270）
async function pickPageRotation(page, w, h, autoRotate, baseRot) {
    if (!autoRotate) return 0;
    var longSide = Math.max(w, h), shortSide = Math.min(w, h);
    if (!(shortSide > 0)) return 0;
    if (longSide / shortSide < ROT_MIN_ASPECT) return 0;
    var screenAngle = await textScreenAngle(page, baseRot);
    if (screenAngle === null) screenAngle = await inkScreenAngle(page, baseRot);
    return screenAngle ? uprightRotation(screenAngle) : 0;
}

// 上传界面上的开关；只读本次点击状态，不落盘。找不到元素时按“开”处理（与 UI 默认一致）
function isAutoRotateOn() {
    var el = document.getElementById('pdf_autorotate');
    return el ? !!el.checked : true;
}

// 勾选状态变化：重新按新策略算一遍默认角度（会丢弃已手动改过的角度）
async function onPdfAutoRotateToggle() {
    if (currentPdfFile) {
        await preparePdfRotation(currentPdfFile);
    }
}

// ---------------- 渲染超时 ----------------
// pdf.js 的 page.render() 在没有可用 worker 时可能永远不 resolve（没有 reject、没有事件），
// 单靠 try/catch 抓不到。这里给渲染加一个硬超时，失败时能明确告诉用户卡在哪一页。
var PDF_RENDER_TIMEOUT_MS = 60000;

function withTimeout(promise, ms, what) {
    return new Promise(function (resolve, reject) {
        var settled = false;
        var timer = setTimeout(function () {
            if (settled) return;
            settled = true;
            reject(new Error(what + '超过 ' + Math.round(ms / 1000) + ' 秒没有响应。'
                + 'PDF.js 渲染被卡住，常见原因：窗口在后台被浏览器降频（最小化）、'
                + 'worker 脚本没加载成功、或该页内容过大。请把窗口切到前台后重试。'));
        }, ms);
        Promise.resolve(promise).then(function (v) {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            resolve(v);
        }, function (e) {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            reject(e);
        });
    });
}

// ---------------- 逐页手动旋转 ----------------
// 自动判定只给默认值：纯图片页（扫描件、手机拍的、PPT 贴图）没有文字层，
// 朝向是烧进像素里的，任何启发式都可能判错，所以把最终决定权交给用户。
// 缩略图就是“上传后长什么样”，点箭头改角度，上传时按当前角度渲染。
var PDF_ROT_MAX_PAGES = 40;      // 缩略图最多给多少页（再多就没法一屏翻了）
var PDF_ROT_THUMB_HEIGHT = 88;   // 缩略图高度（px）
var pdfRotState = null;          // { file, total, doc, auto:[], angles:[], thumbs:[] }
var pdfRotBusy = false;          // 同一页并发 render 会被 pdf.js 拒绝，串行化

function disposePdfRotState() {
    if (pdfRotState && pdfRotState.doc) {
        try { pdfRotState.doc.destroy(); } catch (e) { /* ignore */ }
    }
    pdfRotState = null;
    var panel = document.getElementById('pdfRotPanel');
    if (panel) panel.style.display = 'none';
    var strip = document.getElementById('pdfRotStrip');
    if (strip) strip.innerHTML = '';
    var status = document.getElementById('pdfRotStatus');
    if (status) status.textContent = '';
}

async function preparePdfRotation(file) {
    disposePdfRotState();
    if (!file || !window.pdfjsLib) return null;
    var panel = document.getElementById('pdfRotPanel');
    var status = document.getElementById('pdfRotStatus');
    if (panel) panel.style.display = '';
    if (status) status.textContent = '正在生成页面缩略图...';
    try {
        var data = await file.arrayBuffer();
        var doc = await withTimeout(window.pdfjsLib.getDocument({
            data: data,
            isEvalSupported: false,
            useWorkerFetch: false
        }).promise, PDF_RENDER_TIMEOUT_MS, '打开 PDF ');
        var shown = Math.min(doc.numPages, PDF_ROT_MAX_PAGES);
        var st = {
            file: file,
            total: doc.numPages,
            doc: doc,
            auto: new Array(shown).fill(0),
            angles: new Array(shown).fill(0),
            thumbs: new Array(shown).fill('')
        };
        pdfRotState = st;
        var autoOn = isAutoRotateOn();
        for (var i = 0; i < shown; i++) {
            var page = await doc.getPage(i + 1);
            var raw = page.getViewport({ scale: 1 });
            var baseRot = (typeof page.rotate === 'number') ? page.rotate : 0;
            st.auto[i] = await pickPageRotation(page, raw.width, raw.height, autoOn, baseRot);
            st.angles[i] = st.auto[i];
            st.thumbs[i] = await renderPdfThumb(page, baseRot, st.angles[i]);
            if (status) status.textContent = '正在生成页面缩略图... ' + (i + 1) + '/' + shown;
        }
        renderPdfRotStrip();
        if (status) {
            status.textContent = doc.numPages > shown
                ? ('共 ' + doc.numPages + ' 页，仅前 ' + shown + ' 页可手动调整，其余按自动判定')
                : ('共 ' + doc.numPages + ' 页');
        }
        return st;
    } catch (e) {
        if (status) status.textContent = '缩略图生成失败：' + (e && e.message ? e.message : e);
        console.warn('[pdf-rot]', e);
        return null;
    }
}

async function renderPdfThumb(page, baseRot, angle) {
    var base = page.getViewport({ scale: 1, rotation: baseRot });
    var scale = PDF_ROT_THUMB_HEIGHT / Math.max(1, base.height);
    var vp = page.getViewport({ scale: scale, rotation: baseRot + (angle || 0) });
    var canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.ceil(vp.width));
    canvas.height = Math.max(1, Math.ceil(vp.height));
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await withTimeout(page.render({ canvasContext: ctx, viewport: vp }).promise,
        PDF_RENDER_TIMEOUT_MS, '渲染缩略图');
    return canvas.toDataURL('image/jpeg', 0.8);
}

function renderPdfRotStrip() {
    var strip = document.getElementById('pdfRotStrip');
    if (!strip) return;
    if (!pdfRotState) { strip.innerHTML = ''; return; }
    var html = '';
    for (var i = 0; i < pdfRotState.angles.length; i++) {
        var deg = pdfRotState.angles[i] || 0;
        var isAuto = deg === (pdfRotState.auto[i] || 0);
        html += '<div style="flex:0 0 auto;text-align:center;width:96px;">'
            + '<img data-rot-thumb="' + i + '" src="' + pdfRotState.thumbs[i] + '" '
            + 'style="height:' + PDF_ROT_THUMB_HEIGHT + 'px;max-width:96px;border:1px solid #ccc;'
            + 'border-radius:4px;background:#fff;cursor:pointer;" title="点击左转 90°" '
            + 'onclick="rotatePdfPage(' + i + ',-90)">'
            + '<div class="small text-muted" style="margin-top:2px;">第 ' + (i + 1) + ' 页'
            + (isAuto ? '' : ' · 手动') + '</div>'
            + '<div style="white-space:nowrap;">'
            + '<button type="button" class="btn btn-sm btn-outline-secondary" style="padding:0 5px;" '
            + 'onclick="rotatePdfPage(' + i + ',-90)">↺</button>'
            + '<span class="small" style="display:inline-block;width:34px;">' + deg + '°</span>'
            + '<button type="button" class="btn btn-sm btn-outline-secondary" style="padding:0 5px;" '
            + 'onclick="rotatePdfPage(' + i + ',90)">↻</button>'
            + '</div></div>';
    }
    strip.innerHTML = html;
}

async function rotatePdfPage(index, delta) {
    if (!pdfRotState || !pdfRotState.angles.length) return;
    if (index < 0 || index >= pdfRotState.angles.length) return;
    if (pdfRotBusy) return;                      // 连点时不并发渲染同一文档
    var next = ((pdfRotState.angles[index] + (delta || 0)) % 360 + 360) % 360;
    pdfRotState.angles[index] = next;
    pdfRotBusy = true;
    try {
        var page = await pdfRotState.doc.getPage(index + 1);
        var baseRot = (typeof page.rotate === 'number') ? page.rotate : 0;
        pdfRotState.thumbs[index] = await renderPdfThumb(page, baseRot, next);
    } catch (e) {
        console.warn('[pdf-rot]', e);
    } finally {
        pdfRotBusy = false;
    }
    renderPdfRotStrip();
}

function rotateAllPdfPages(delta) {
    if (!pdfRotState || !pdfRotState.angles.length) return;
    if (pdfRotBusy) return;
    pdfRotBusy = true;
    (async function () {
        for (var i = 0; i < pdfRotState.angles.length; i++) {
            pdfRotState.angles[i] = ((pdfRotState.angles[i] + (delta || 0)) % 360 + 360) % 360;
            try {
                var page = await pdfRotState.doc.getPage(i + 1);
                var baseRot = (typeof page.rotate === 'number') ? page.rotate : 0;
                pdfRotState.thumbs[i] = await renderPdfThumb(page, baseRot, pdfRotState.angles[i]);
            } catch (e) { /* 单页失败不影响其它页 */ }
        }
        pdfRotBusy = false;
        renderPdfRotStrip();
    })().catch(function () { pdfRotBusy = false; });
}

function resetPdfPageRotations() {
    if (!pdfRotState) return;
    if (pdfRotBusy) return;
    pdfRotBusy = true;
    (async function () {
        for (var i = 0; i < pdfRotState.angles.length; i++) {
            pdfRotState.angles[i] = pdfRotState.auto[i] || 0;
            try {
                var page = await pdfRotState.doc.getPage(i + 1);
                var baseRot = (typeof page.rotate === 'number') ? page.rotate : 0;
                pdfRotState.thumbs[i] = await renderPdfThumb(page, baseRot, pdfRotState.angles[i]);
            } catch (e) { /* ignore */ }
        }
        pdfRotBusy = false;
        renderPdfRotStrip();
    })().catch(function () { pdfRotBusy = false; });
}

// 上传时取该页角度：用户手动调过就用用户的，否则返回 null 走自动判定
function manualRotationFor(pageNum, pdfFile) {
    if (!pdfRotState || pdfRotState.file !== pdfFile) return null;
    var a = pdfRotState.angles[pageNum - 1];
    return (typeof a === 'number') ? a : null;
}

async function renderPdfLocally(pdfFile, onProgress, autoRotate) {
    if (autoRotate === undefined) autoRotate = true;
    var data = await pdfFile.arrayBuffer();
    var pdf = await withTimeout(window.pdfjsLib.getDocument({
        data: data,
        isEvalSupported: false,
        useWorkerFetch: false
    }).promise, PDF_RENDER_TIMEOUT_MS, '打开 PDF ');

    var total = pdf.numPages;
    var images = [];
    var rotatedPages = [];

    try {
        for (var p = 1; p <= total; p++) {
            var page = await pdf.getPage(p);
            var raw = page.getViewport({ scale: 1 });

            // 关键：笔记页的画布是固定的 2880x1800（模板里两张样例截图都是这个尺寸），
            // 图片超出部分会被裁掉。所以整页要等比缩放后完整放进画布，居中留白。
            var canvas = document.createElement('canvas');
            canvas.width = CANVAS_W;
            canvas.height = CANVAS_H;
            var ctx = canvas.getContext('2d');
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, canvas.width, canvas.height);

            // 自动旋转：raw 已经包含 PDF 自带的 /Rotate，这里按内容判朝向，只叠加 0/90/180/270
            var baseRot = (typeof page.rotate === 'number') ? page.rotate : 0;
            // 用户在上传面板里逐页调过的角度优先；没调过才用自动判定
            var extraRot = manualRotationFor(p, pdfFile);
            if (extraRot === null) {
                extraRot = await pickPageRotation(page, raw.width, raw.height, autoRotate, baseRot);
            }
            // 转 90/270 时长宽互换，缩放比例要按转完之后的方向算，否则页面会缩得很小
            var fitW = (extraRot % 180) ? raw.height : raw.width;
            var fitH = (extraRot % 180) ? raw.width : raw.height;
            var fit = Math.min(CANVAS_W / fitW, CANVAS_H / fitH);
            var viewport = extraRot
                ? page.getViewport({ scale: fit, rotation: baseRot + extraRot })
                : page.getViewport({ scale: fit });
            if (extraRot) rotatedPages.push(p);
            var dx = Math.floor((CANVAS_W - viewport.width) / 2);
            var dy = Math.floor((CANVAS_H - viewport.height) / 2);
            if (dx < 0) { dx = 0; }
            if (dy < 0) { dy = 0; }

            var renderOpts = {
                canvasContext: ctx,
                viewport: viewport,
                transform: (dx || dy) ? [1, 0, 0, 1, dx, dy] : null
            };
            if (!renderOpts.transform) { delete renderOpts.transform; }

            await withTimeout(page.render(renderOpts).promise, PDF_RENDER_TIMEOUT_MS, '渲染第 ' + p + ' 页');

            var blob = await new Promise(function (resolve) {
                canvas.toBlob(function (b) { resolve(b); }, 'image/webp', 0.92);
            });
            if (!blob) {
                blob = await new Promise(function (resolve) {
                    canvas.toBlob(function (b) { resolve(b); }, 'image/jpeg', 0.92);
                });
            }
            if (!blob) throw new Error('第 ' + p + ' 页渲染失败');

            // 缩略图用 PNG（预览里显示的就是它）
            var pngBlob = await new Promise(function (resolve) {
                canvas.toBlob(function (b) { resolve(b); }, 'image/png');
            });

            images.push({ pageNum: p, blob: blob, pngBlob: pngBlob, url: 'local://page/' + p });
            if (onProgress) onProgress(p / total * 0.95, p, total);
        }
    } finally {
        try { pdf.destroy(); } catch (e) { /* ignore */ }
    }

    if (onProgress) onProgress(1.0, total, total);
    // 附带本次的旋转情况，便于日志/提示（数组上挂属性，不改变 images 的数组形态）
    images.rotatedPages = rotatedPages;
    images.autoRotate = autoRotate;
    return images;
}

// 生成一张“无网址”的占位缩略图（仅在拿不到第 1 页 PNG 时兜底）。
// 模板自带的 screenshot.png 左下角有 https://<web-host> 与署名，
// 这里改用 canvas 现画，内容只保留一句说明，不引用任何外部资源。
function makePlaceholderPng() {
    return new Promise(function (resolve) {
        var W = 2200, H = 1309;
        var canvas = document.createElement('canvas');
        canvas.width = W;
        canvas.height = H;
        var ctx = canvas.getContext('2d');
        ctx.fillStyle = '#808080';
        ctx.fillRect(0, 0, W, H);
        ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = 'bold 62px "Microsoft YaHei", "PingFang SC", sans-serif';
        ctx.fillText('这一页的缩略图未能生成', W / 2, H / 2 - 70);
        ctx.font = '44px "Microsoft YaHei", "PingFang SC", sans-serif';
        ctx.fillText('打开笔记后逐页加载即可正常显示', W / 2, H / 2 + 40);
        canvas.toBlob(function (b) { resolve(b); }, 'image/png');
    });
}

async function convertPdfToImagesServer(pdfFile, onProgress) {
    // 方案：直接上传 PDF 到 <pdf2img-host>，获取转换后的图片 URL 列表
    var formData = new FormData();
    formData.append('file', pdfFile, pdfFile.name);

    if (onProgress) {
        onProgress(0.1, 0, 1);
    }

    var resp = await fetch('https://<pdf2img-host>/upload', {
        method: 'POST',
        body: formData
    });

    if (!resp.ok) {
        throw new Error('PDF转图片失败: HTTP ' + resp.status);
    }

    var result = await resp.json();
    // 返回格式: { imgPaths: ["url1", "url2", ...] }
    if (!result.imgPaths || !Array.isArray(result.imgPaths)) {
        throw new Error('PDF转图片返回格式错误: ' + JSON.stringify(result));
    }

    var imgPaths = result.imgPaths;
    if (!imgPaths.length) {
        throw new Error('转换服务没有返回任何页面图片（可能是 PDF 内容为空或该服务异常）');
    }
    var images = [];

    for (var i = 0; i < imgPaths.length; i++) {
        if (onProgress) {
            onProgress((i + 1) / imgPaths.length * 0.9, i + 1, imgPaths.length);
        }

        // OSS 上的页面图没有 CORS 头，必须走代理（本地 5005 优先）
        var blob;
        try {
            blob = await fetchViaProxy(imgPaths[i], { image: true });
        } catch (e) {
            throw new Error('下载第 ' + (i + 1) + ' 页图片失败：' + ((e && e.message) || e));
        }

        // 转为 webp（如果原图不是 webp）
        if (blob.type !== 'image/webp') {
            blob = await convertBlobToWebp(blob);
        }

        images.push({
            pageNum: i + 1,
            blob: blob,
            url: imgPaths[i]
        });
    }

    if (onProgress) {
        onProgress(1.0, imgPaths.length, imgPaths.length);
    }

    return images;
}

/**
 * 把任意图片 blob 重新编码成 PNG（用于生成笔记缩略图，替代模板占位图）
 */
async function toPngBlob(blob) {
    var url = URL.createObjectURL(blob);
    try {
        var img = await new Promise(function (resolve, reject) {
            var i = new Image();
            i.onload = function () { resolve(i); };
            i.onerror = function () { reject(new Error('图片解码失败')); };
            i.src = url;
        });
        var canvas = document.createElement('canvas');
        canvas.width = img.width;
        canvas.height = img.height;
        canvas.getContext('2d').drawImage(img, 0, 0);
        return await new Promise(function (resolve) {
            canvas.toBlob(function (b) { resolve(b); }, 'image/png');
        });
    } finally {
        URL.revokeObjectURL(url);
    }
}

/**
 * 将图片 blob 转为 webp 格式
 */
async function convertBlobToWebp(blob) {
    return new Promise(function(resolve, reject) {
        var img = new Image();
        var url = URL.createObjectURL(blob);

        img.onload = function() {
            var canvas = document.createElement('canvas');
            canvas.width = img.width;
            canvas.height = img.height;
            var ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0);

            canvas.toBlob(function(webpBlob) {
                URL.revokeObjectURL(url);
                if (webpBlob && webpBlob.type === 'image/webp') {
                    resolve(webpBlob);
                } else {
                    // webp 不支持，返回原图
                    resolve(blob);
                }
            }, 'image/webp', 0.85);
        };

        img.onerror = function() {
            URL.revokeObjectURL(url);
            reject(new Error('图片解码失败'));
        };

        img.src = url;
    });
}

// ==================== MD5计算 ====================

async function blobToMd5(blob) {
    var buffer = await blob.arrayBuffer();
    var wordArray = CryptoJS.lib.WordArray.create(buffer);
    return CryptoJS.MD5(wordArray).toString().toUpperCase();
}

// ==================== 笔记模板的资源摘要链（对齐「中育Toolbox 应用版」） ====================
//
// 模板是二进制结构，出厂时写死了示例资源的 SHA-256：
//   router.bin → 记着 file.bin / matrix.bin / command.bin 的摘要
//   file.bin   → 记着该页图片的摘要
// 把模板原样上传，App 按摘要找到的仍是模板里那张示例图，于是笔记能「显示」，
// 却无法被导出或用别的应用打开。应用版（NoteView 的 rt()/Ge()）在每页上传前
// 都会把这条链改写成当页真实图片的摘要，这里照做。
var NOTE_TEMPLATE_UUID = 'a888b5fb-e65d-4611-a3af-1f80a0fb6ced';
var NOTE_FILE_BIN = NOTE_TEMPLATE_UUID + '/a2b4fb47-3623-45be-9fe9-57fc62e66651_file.bin';
var NOTE_ROUTER_BIN = NOTE_TEMPLATE_UUID + '/router.bin';
var NOTE_PAGE_IMAGE = 'B466246B6F67160E63431159941CD9A9screenCaptureb59d24b6-00fa-4f53-bc4f-1df255a5101a.webp';

async function sha256Hex(blob) {
    var buffer = await blob.arrayBuffer();
    if (!(window.crypto && window.crypto.subtle)) {
        // 个别环境（某些 file:// 场景）没有 SubtleCrypto，退回页面已加载的 CryptoJS
        return CryptoJS.SHA256(CryptoJS.lib.WordArray.create(new Uint8Array(buffer))).toString();
    }
    var digest = await window.crypto.subtle.digest('SHA-256', buffer);
    return Array.from(new Uint8Array(digest)).map(function (b) {
        return ('0' + b.toString(16)).slice(-2);
    }).join('');
}

// 替换 bin 中唯一一处 64 位十六进制摘要。oldHash 用来在 router.bin 的
// 三个摘要里精确定位「file.bin」那一个（其余两个属于 matrix/command）。
async function replaceTemplateHash(blob, newHash, oldHash) {
    var bytes = new Uint8Array(await blob.arrayBuffer());
    var text = '';
    for (var i = 0; i < bytes.length; i++) text += String.fromCharCode(bytes[i]);
    var re = oldHash ? new RegExp(oldHash, 'g') : /[0-9a-f]{64}/g;
    var hits = Array.from(text.matchAll(re));
    if (hits.length !== 1) {
        throw new Error('笔记模板中的资源摘要结构无效（匹配 ' + hits.length + ' 处）');
    }
    var patched = bytes.slice();
    patched.set(new TextEncoder().encode(newHash), hits[0].index);
    return new Blob([patched], { type: blob.type || 'application/octet-stream' });
}

async function patchTemplatesForPage(templates, pageImageBlob) {
    var out = Object.assign({}, templates);
    var oldFileHash = await sha256Hex(out[NOTE_FILE_BIN]);
    out[NOTE_FILE_BIN] = await replaceTemplateHash(
        out[NOTE_FILE_BIN], await sha256Hex(pageImageBlob));
    out[NOTE_ROUTER_BIN] = await replaceTemplateHash(
        out[NOTE_ROUTER_BIN], await sha256Hex(out[NOTE_FILE_BIN]), oldFileHash);
    return out;
}

// ==================== 文件选择处理 ====================

async function handlePdfSelect(event) {
    var file = event.target.files[0];
    if (!file) return;
    if (!file.type.includes('pdf') && !file.name.toLowerCase().endsWith('.pdf')) {
        swal('请选择PDF文件');
        return;
    }
    if (file.size > 50 * 1024 * 1024) {
        swal('PDF文件不能超过50MB');
        return;
    }
    currentPdfFile = file;
    var nameInput = document.getElementById('pdfNoteName');
    if (nameInput && !nameInput.value) {
        nameInput.value = file.name.replace(/\.pdf$/i, '');
    }
    var fileInfo = document.getElementById('pdfFileInfo');
    if (fileInfo) {
        fileInfo.innerHTML = '<div class="alert alert-info mt-2"><strong>已选择：</strong>' + file.name + '<br><strong>大小：</strong>' + formatFileSize(file.size) + '</div>';
    }
    // 生成逐页缩略图：自动判定只给默认角度，用户可以在上传前逐页改
    try {
        await preparePdfRotation(file);
    } catch (e) {
        console.warn('[pdf-rot] 缩略图准备失败', e);
    }
}

// ==================== Token验证 ====================

function isTokenValid() {
    var token = localStorage.getItem('token');
    if (!token) return false;
    try {
        var payload = JSON.parse(atob(token.split('.')[1]));
        return payload.exp && payload.exp > (Date.now() / 1000 + 300);
    } catch (e) { return false; }
}

async function getUserId() {
    var token = localStorage.getItem('token');
    try {
        var payload = JSON.parse(atob(token.split('.')[1]));
        return payload.sub || payload.nameid || payload.userId || '';
    } catch (e) { return ''; }
}

// ==================== 主流程 ====================

async function uploadPdfAsNote() {
    if (!currentPdfFile) { swal('请先选择PDF文件'); return; }

    var noteName = document.getElementById('pdfNoteName').value.trim();
    if (!noteName) { swal('请输入笔记名称'); return; }

    var token = localStorage.getItem('token');
    if (!token) { swal('请先登录'); return; }
    if (!isTokenValid()) { swal('登录已过期，请重新登录'); return; }

    try {
        // 步骤1：加载模板文件
        updatePdfProgress(5, '正在加载模板文件...');
        var templates = await loadTemplateFiles();

        // 步骤2：PDF转图片
        var autoRotate = isAutoRotateOn();
        updatePdfProgress(15, autoRotate ? '正在转换PDF（自动旋转已开启）...' : '正在转换PDF...');
        pdfImages = await convertPdfToImages(currentPdfFile, function(p, c, t) {
            updatePdfProgress(15 + p * 30, '转换PDF：第 ' + c + '/' + t + ' 页');
        }, autoRotate);
        if (pdfImages.rotatedPages && pdfImages.rotatedPages.length) {
            updatePdfProgress(46, '已旋转 ' + pdfImages.rotatedPages.length + ' 页：第 '
                + pdfImages.rotatedPages.join('、') + ' 页');
        }

        var userId = await getUserId();
        var customFileId = generateCustomFileId();
        var todayStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');
        var timestamp = new Date().toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
        }).replace(/\//g, '-').replace(/ /g, ' ');

        // 步骤3：上传模板文件到OSS
        updatePdfProgress(50, '正在上传模板文件...');
        
        // 先生成 ossPageHash（用于模板文件路径）
        var ossPageHash = generatePageHash();
        var uuid = 'a888b5fb-e65d-4611-a3af-1f80a0fb6ced';
        
        // App 的约定：每一页的资源都放在「笔记目录/页目录/文件名」下，
        // 因此模板文件要按页各存一份（它们很小），不能共用 ossPageHash 目录。
        var templateBlobs = Object.assign({}, templates);

        // 先确保 OSS 基础 URL 可用（通过实际上传一个测试文件来获取）
        if (!window.ossBaseUrl) {
            updatePdfProgress(52, '正在获取OSS配置...');
            // 上传第一个模板文件，从返回的文件URL中提取OSS基础URL
            var testFileUrl = await uploadToOss(templates['page_router.bin'], userId, customFileId, ossPageHash + '/page_router.bin');
            // 从文件URL中提取OSS基础URL（即域名部分+斜杠）
            // 文件URL格式：https://bucket.region.aliyuncs.com/note_v2/res/userId/date/nonce/filename
            // 我们需要提取：https://bucket.region.aliyuncs.com/
            var urlObj = new URL(testFileUrl);
            window.ossBaseUrl = urlObj.protocol + '//' + urlObj.host + '/';
            
            // 不要从 templates 里删 page_router.bin：loadTemplateFiles() 返回的是缓存对象，
            // 删掉后同一个页面会话里第二次上传就会缺这个文件（App 打开笔记会报「获取文件位置失败」）。
        }

        // 模板文件不再共用一份，改为在下面的按页循环里逐页上传

        // 步骤4：上传每页图片并构建 resourceList
        updatePdfProgress(65, '正在上传图片...');
        var resourceList = [];
        // uploadFile 可能因服务端策略的日期口径不同而改用 UTC 日期，
        // 这里跟随它实际写入的日期，否则 ossBase / 笔记 fileUrl 会和对象路径对不上。
        if (window.ossDateStr) { todayStr = window.ossDateStr; }
        // ossBase 不包含 ossPageHash，模板文件和图片文件的路径不同
        // 使用前面已获取的 window.ossBaseUrl
        var ossBase = window.ossBaseUrl + 'note_v2/res/' + userId + '/' + todayStr + '/' + customFileId;

        // 缩略图：每一页都要有自己的 screenshot.png（App 按 页目录/文件名 逐页取），
        // 用该页的 PNG 渲染结果，md5 也按该页实际内容算。
        var shotMd5 = null;

        for (var pageIndex = 0; pageIndex < pdfImages.length; pageIndex++) {
            var pageHash = generatePageHash();
            var pageBase = '/storage/emulated/0/Android/data/com.friday.cloudsnote/userNote/' + userId + '/note/' + customFileId + '/' + pageHash;

            // 上传当前页图片（webp 格式，文件名固定）
            var imgFilename = pageHash + '/' + NOTE_PAGE_IMAGE;
            var imgBlob = pdfImages[pageIndex].blob;

            // 每页先按当页图片改写模板里的 SHA-256 摘要链，再上传模板。
            // 改的是这一页的副本，不会污染缓存中的出厂模板。
            var pageTemplates = await patchTemplatesForPage(templateBlobs, imgBlob);
            await uploadToOss(imgBlob, userId, customFileId, imgFilename);

            // 模板按应用版的目录结构逐页各存一份：page_router.bin 在页目录根，
            // 其余在 <uuid>/ 下（page_router.bin 内部就引用了这个 uuid 目录）。
            // 页目录根另留一份扁平副本，兼容此前「fileUrl + 页目录 + 文件名」的取法。
            var templateNames = Object.keys(pageTemplates);
            for (var tk = 0; tk < templateNames.length; tk++) {
                var tName = templateNames[tk];
                var tBase = tName.split('/').pop();
                await uploadToOss(pageTemplates[tName], userId, customFileId, pageHash + '/' + tName);
                if (tName !== tBase) {
                    await uploadToOss(pageTemplates[tName], userId, customFileId, pageHash + '/' + tBase);
                }
            }

            // 缩略图：每页都用自己的真实渲染结果；<uuid>/ 下与页目录根各一份。
            var shotBlob = pdfImages[pageIndex].pngBlob || null;
            if (!shotBlob) {
                try { shotBlob = await toPngBlob(imgBlob); } catch (e) { shotBlob = null; }
            }
            if (!shotBlob) {
                try { shotBlob = await makePlaceholderPng(); } catch (e) { shotBlob = null; }
            }
            if (!shotBlob) shotBlob = imgBlob;   // 兜底：至少保证这条资源有对象可下
            await uploadToOss(shotBlob, userId, customFileId,
                pageHash + '/' + NOTE_TEMPLATE_UUID + '/screenshot.png');
            await uploadToOss(shotBlob, userId, customFileId, pageHash + '/screenshot.png');

            // 9 条资源行，与应用版一致：id 是 App 本地路径、ossImageUrl 是 OSS 路径、
            // md5 全部按真实字节算、resourceType 用固定语义（1=模板 bin / 2=缩略图 / 0=页面图）。
            var pageEntries = [
                { rel: 'page_router.bin', blob: pageTemplates['page_router.bin'], type: 1, ossRel: 'page_router.bin' },
                { rel: NOTE_TEMPLATE_UUID + '/059848e4-1971-47fb-9e47-517266cdef05_matrix.bin', type: 1 },
                { rel: NOTE_FILE_BIN, type: 1 },
                { rel: NOTE_TEMPLATE_UUID + '/e339e39b-64d9-4de0-bfaa-dace2a3f8e7d_command.bin', type: 1 },
                { rel: NOTE_TEMPLATE_UUID + '/header.bin', type: 1 },
                { rel: NOTE_ROUTER_BIN, type: 1 },
                { rel: NOTE_TEMPLATE_UUID + '/screenshot.png', blob: shotBlob, type: 2 },
                { rel: NOTE_TEMPLATE_UUID + '/snapshot.bin', type: 1 },
                { rel: 'res/image/' + NOTE_PAGE_IMAGE, blob: imgBlob, type: 0, ossRel: NOTE_PAGE_IMAGE }
            ];
            for (var ei = 0; ei < pageEntries.length; ei++) {
                var e = pageEntries[ei];
                var eBlob = e.blob || pageTemplates[e.rel];
                if (!eBlob) throw new Error('缺少模板资源: ' + e.rel);
                resourceList.push({
                    id: pageBase + '/' + e.rel,
                    fileId: customFileId,
                    pageName: pageBase,
                    pageIndex: pageIndex,
                    md5: await blobToMd5(eBlob),
                    resourceType: e.type,
                    ossImageUrl: ossBase + '/' + pageHash + '/' + (e.ossRel || e.rel),
                    createTimeStamp: timestamp,
                    updateTimeStamp: timestamp,
                    toBeUploaded: false,
                    wasDeleted: false
                });
            }

            updatePdfProgress(65 + (pageIndex + 1) / pdfImages.length * 25,
                '已上传第 ' + (pageIndex + 1) + '/' + pdfImages.length + ' 页');
        }

        // 步骤5：保存资源
        updatePdfProgress(92, '正在保存资源...');
        await saveResourceList(userId, resourceList);

        // 步骤6：保存笔记
        updatePdfProgress(97, '正在保存笔记...');
        await saveNote(userId, customFileId, noteName, todayStr);

        updatePdfProgress(100, '上传完成！');
        swal('成功', '笔记"' + noteName + '"已保存，共 ' + pdfImages.length + ' 页', 'success');

    } catch (error) {
        console.error(error);
        updatePdfProgress(0, '失败：' + error.message);
        swal('失败', error.message, 'error');
    }
}

// ==================== 保存资源/笔记 ====================

async function saveResourceList(userId, resourceList) {
    var token = localStorage.getItem('token');
    // 与 api.py addOrUpdateResource 一致：直接加密数组
    var plainData = JSON.stringify(resourceList);
    var data = codeUtilsEncode(plainData);

    var resp = await fetch(window.API_BASE_URL + '/CloudNotes/api/Resources/AddOrUpdate', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json; charset=UTF-8',
            'Connection': 'Keep-Alive'
        },
        body: data
    });
    var result = await resp.json();
    if (result.code !== 0) throw new Error('保存资源失败: ' + JSON.stringify(result));
    return result;
}

async function saveNote(userId, customFileId, fileName, todayStr) {
    var token = localStorage.getItem('token');
    // fileUrl 必须指向对象真正所在的桶（此前写死 example-oss-a 主机，而对象都在 example-oss-b，
    // App 端按 fileUrl + 页目录 + 文件名 取图会全部落空，报“获取文件位置失败”）。
    var ossRoot = window.ossBaseUrl || 'http://<oss-bucket>.oss-cn-shenzhen.aliyuncs.com/';
    var fileUrl = ossRoot + 'note_v2/res/' + userId + '/' + todayStr + '/' + customFileId + '/';
    var plainData = JSON.stringify({
        fileId: customFileId,
        fileName: fileName,
        parentId: '0',
        type: '12',
        fileUrl: fileUrl
    });
    var data = codeUtilsEncode(plainData);

    var resp = await fetch(window.API_BASE_URL + '/CloudNotes/api/Notes/AddOrUpdate', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json; charset=UTF-8',
            'Connection': 'Keep-Alive'
        },
        body: data
    });
    var result = await resp.json();
    if (result.code !== 0) throw new Error('保存笔记失败: ' + JSON.stringify(result));
    return result;
}

// ==================== 工具函数 ====================

function updatePdfProgress(percent, text) {
    var bar = document.getElementById('pdfProgress');
    var txt = document.getElementById('pdfProgressText');
    if (bar) {
        bar.style.width = percent + '%';
        bar.className = percent < 100
            ? 'progress-bar progress-bar-striped progress-bar-animated'
            : 'progress-bar bg-success';
    }
    if (txt) txt.textContent = text || (percent + '%');
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(2) + ' KB';
    return (bytes / 1048576).toFixed(2) + ' MB';
}

// ==================== 预览 ====================

async function previewPdfImages() {
    var container = document.getElementById('pdfPreviewContainer');
    if (!container || pdfImages.length === 0) return;
    container.innerHTML = '';
    var count = Math.min(5, pdfImages.length);
    for (var i = 0; i < count; i++) {
        var url = URL.createObjectURL(pdfImages[i].blob);
        var img = document.createElement('img');
        img.src = url;
        img.className = 'img-fluid mb-2';
        img.style.maxHeight = '200px';
        img.alt = '第' + (i + 1) + '页';
        var div = document.createElement('div');
        div.className = 'text-center mb-2';
        div.innerHTML = '<small>第' + (i + 1) + '页</small><br>';
        div.appendChild(img);
        container.appendChild(div);
    }
    if (pdfImages.length > 5) {
        var p = document.createElement('p');
        p.className = 'text-muted';
        p.textContent = '...还有' + (pdfImages.length - 5) + '页';
        container.appendChild(p);
    }
}

// ==================== 下载 ====================

function downloadPdfImages() {
    if (pdfImages.length === 0) { swal('没有可下载的文件'); return; }
    if (typeof JSZip === 'undefined') { swal('JSZip未加载'); return; }
    var zip = new JSZip();
    for (var i = 0; i < pdfImages.length; i++) {
        var fname = 'page_' + String(i + 1).padStart(3, '0') + '.jpg';
        zip.file(fname, pdfImages[i].blob);
    }
    zip.generateAsync({ type: 'blob' }).then(function(blob) {
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = (document.getElementById('pdfNoteName').value.trim() || 'pdf_note') + '.zip';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    });
}
