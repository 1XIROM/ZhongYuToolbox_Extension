/* ================= 中育云笔记 · 老师分享给我的文件（只读） =================
 * 链路（从 com.friday.cloudsnote 1.13.4 逆向）：
 *   CloudNotes/api/SuperShare/{GetSharedToMeByTeacher, GetByParentId, GetResourceByFileId}
 * 调用约定与 Notes 接口一致：?<AES-ECB/PKCS7 base64>，Authorization: Bearer <token>
 *
 * 注意：Retrofit 接口的 @Query 参数名被 R8 抹掉了，无法静态确证；
 * 因此这里按候选顺序探测，命中才写入 localStorage 记忆，空结果不算命中。
 */
window.TchShare = (function () {
    'use strict';

    // v2：旧版把"字面量参数"当缓存，会把 parentId=0 复用到子目录，必须作废
    var CACHE_KEY = 'tchShare_ep_ok_v2';

    // 用于确认浏览器有没有真的加载到新代码（诊断第一行会打印）
    var VERSION = 'v8-20261024';

    var KEY_FILES = ['shareFileList', 'fileList', 'list', 'items', 'records', 'rows', 'data'];
    var KEY_TCH = ['teachers', 'teacherList', 'list', 'items', 'records', 'rows', 'data'];

    var st = {
        teacher: null, teachers: [], files: [], crumbs: [], lastLog: [], flat: false,
        lastEntryRaw: null, lastChildRaw: null, lastChildPlain: '', lastContentRaw: null,
        all: null, byParent: null
    };

    /* ---------------- 基础工具 ---------------- */

    function apiBase() {
        return String(window.API_BASE_URL || window.API_BASE_BASE_URL || '').replace(/\/+$/, '');
    }

    function token() {
        return localStorage.getItem('token') || '';
    }

    function specialPath() {
        return apiBase().indexOf('<relay-host>') >= 0;
    }

    function decData(v) {
        if (v === null || v === undefined) return null;
        if (typeof v !== 'string') return v;
        var t = '';
        try { t = window.aesDecrypt ? window.aesDecrypt(v) : ''; } catch (e) { t = ''; }
        if (t) { try { return JSON.parse(t); } catch (e) { return t; } }
        try { return JSON.parse(v); } catch (e) { return v; }
    }

    function buildUrls(ep, plain) {
        var b = apiBase();
        var qs = '';
        if (plain !== null) {
            try { qs = '?' + window.aesEncrypt(plain); } catch (e) { qs = ''; }
        }
        var out = [];
        if (specialPath()) out.push(b + '/special/' + ep + qs);
        out.push(b + '/CloudNotes/api/SuperShare/' + ep + qs);
        return out;
    }

    // 只认明确的键名：不能"随便抓第一个数组"，否则 {teachers:[…]} 会被误当成文件列表
    function pickArr(obj, keys) {
        if (!obj) return [];
        if (Array.isArray(obj)) return obj;
        var i, j, v;
        for (i = 0; i < keys.length; i++) {
            v = obj[keys[i]];
            if (Array.isArray(v)) return v;
            if (v && typeof v === 'object') {
                for (j = 0; j < keys.length; j++) {
                    if (Array.isArray(v[keys[j]])) return v[keys[j]];
                }
            }
        }
        // 兜底：字段名不在已知集合里时，按"元素长相"找列表
        // （老师列表只有 teacherId/teacherName，文件列表一定有 fileId/fileName）
        var wantFiles = (keys === KEY_FILES);
        var found = null;
        Object.keys(obj).forEach(function (k) {
            if (found) return;
            var a = obj[k];
            if (!Array.isArray(a) || !a.length) return;
            var it = a[0];
            if (!it || typeof it !== 'object') return;
            var hasFile = (it.fileId !== undefined || it.fileName !== undefined);
            var hasTch = (it.teacherId !== undefined || it.teacherName !== undefined);
            if (wantFiles && !hasFile) return;
            if (!wantFiles && !hasTch) return;
            found = a;
        });
        return found || [];
    }

    function pickNum(o) {
        if (!o) return null;
        for (var i = 1; i < arguments.length; i++) {
            var v = o[arguments[i]];
            if (v !== null && v !== undefined && v !== '') return v;
        }
        return null;
    }

    function esc(s) {
        return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function j(v) { return JSON.stringify(v, null, 2); }

    function $(id) { return document.getElementById(id); }

    /* ---------------- 节点类型（实测自真实返回） ----------------
     * type: 0=文件夹（fileUrl 为空）  3=pptx  4=docx  5=pdf  7=图片  10=xlsx
     * 文件条目自带公开直链 fileUrl，不需要再问 GetResourceByFileId
     */
    var TYPE_NAME = {
        0: '文件夹', 1: '笔记', 2: '思维导图', 3: 'PPT', 4: 'Word',
        5: 'PDF', 6: '音频', 7: '图片', 8: '视频', 9: '压缩包', 10: 'Excel', 11: '文本'
    };

    function ftype(f) {
        var v = f && f.type;
        if (v === null || v === undefined || v === '') return null;
        var n = Number(v);
        return isNaN(n) ? null : n;
    }

    function fileUrlOf(f) {
        return String(pickNum(f, 'fileUrl', 'url', 'realOssUrl', 'ossUrl') || '');
    }

    // 靠 type 判断；没有 type 时才退回"无直链且无扩展名"的启发式
    function isFolder(f) {
        var t = ftype(f);
        if (t === 0) return true;
        if (t !== null) return false;
        var name = String(pickNum(f, 'fileName', 'name', 'title') || '');
        return !fileUrlOf(f) && !/\.[a-z0-9]{1,6}\s*$/i.test(name);
    }

    // 工具箱跑在 https 页面上，OSS 直链是 http:// 会被混合内容拦掉
    function safeUrl(u) {
        return String(u || '').replace(/^http:\/\//i, 'https://');
    }

    function cached(kind) {
        try { return JSON.parse(localStorage.getItem(CACHE_KEY) || '{}')[kind] || null; } catch (e) { return null; }
    }

    function remember(kind, ep, plain, shape) {
        try {
            var all = JSON.parse(localStorage.getItem(CACHE_KEY) || '{}');
            all[kind] = { ep: ep, plain: plain, shape: shape || null };
            localStorage.setItem(CACHE_KEY, JSON.stringify(all));
        } catch (e) { /* ignore */ }
    }

    /* ---------------- 探测式请求 ----------------
     * isEmpty(data) 为 true 时视为"没命中"，继续试下一个候选端点；
     * 全部为空则退回第一个空结果，让调用方把原始返回摊开给用户看。
     *
     * 缓存规则（关键）：参数里带 parentId 的接口不能缓存字面量，
     * 只能缓存"参数形状"(shape)，否则点进子目录时会被 parentId=0 覆盖。
     */
    async function probe(kind, cands, isEmpty) {
        var log = [];
        var hit = cached(kind);
        var order = cands.slice();
        if (hit && hit.shape) {
            var pre = [], rest = [];
            order.forEach(function (c) {
                (c.shape === hit.shape ? pre : rest).push(c);
            });
            order = pre.concat(rest);
        } else if (hit && hit.ep) {
            order.unshift({ ep: hit.ep, plain: hit.plain, shape: hit.shape });
        }
        var firstEmpty = null;

        for (var i = 0; i < order.length; i++) {
            var c = order[i];
            var urls = buildUrls(c.ep, c.plain);
            for (var u = 0; u < urls.length; u++) {
                var url = urls[u];
                var short = url.split('?')[0].replace(apiBase(), '');
                try {
                    var res = await fetch(url, { headers: { Authorization: 'Bearer ' + token() } });
                    var txt = await res.text();
                    var js = null;
                    try { js = JSON.parse(txt); } catch (e) { js = null; }
                    if (!js) {
                        log.push('HTTP ' + res.status + ' 非JSON ' + short + ' bytes=' + txt.length);
                        continue;
                    }
                    if (js.code !== 0) {
                        log.push('code=' + js.code + ' ' + (js.msg || js.message || '') + '  ' + short);
                        continue;
                    }
                    var data = decData(js.data);
                    if (data === null || data === undefined || data === '') {
                        log.push('code=0 但 data 解出来是空  ' + short);
                        continue;
                    }
                    if (isEmpty && isEmpty(data)) {
                        log.push('code=0 但结果为 0 条  ' + short);
                        if (!firstEmpty) firstEmpty = { data: data, ep: c.ep, plain: c.plain, shape: c.shape };
                        continue;
                    }
                    log.push('OK ' + short + '  plain=' + (c.plain === null ? '(无参数)' : c.plain));
                    remember(kind, c.ep, c.plain, c.shape);
                    return { data: data, log: log, ep: c.ep, plain: c.plain, shape: c.shape };
                } catch (e) {
                    log.push('ERR ' + e.message + '  ' + short);
                }
            }
        }
        if (firstEmpty) {
            log.push('→ 所有候选都返回 0 条，退回展示 ' + firstEmpty.ep + ' 的原始返回');
            return { data: firstEmpty.data, log: log, ep: firstEmpty.ep, plain: firstEmpty.plain, shape: firstEmpty.shape, empty: true };
        }
        return { data: null, log: log };
    }

    /* ---------------- 渲染 ---------------- */

    function renderMsg(html) {
        var box = $('tchShareList');
        if (box) box.innerHTML = html;
    }

    function renderLoading(msg) {
        renderMsg('<div class="p-3 text-muted"><span class="spinner-border spinner-border-sm me-2"></span>' + esc(msg) + '</div>');
    }

    function renderRaw(data, tip) {
        var body = typeof data === 'string' ? data : j(data);
        renderMsg('<div class="alert alert-info m-2 mb-1">' + esc(tip) + '</div>'
            + '<pre class="small m-2" style="max-height:44vh;overflow:auto;white-space:pre-wrap">' + esc(body) + '</pre>');
    }

    function renderCrumbs() {
        var el = $('tchShareCrumb');
        if (!el) return;
        var parts = ['<li class="breadcrumb-item" style="cursor:pointer" onclick="TchShare.home()">老师列表</li>'];
        if (!st.teacher) {
            parts[0] = '<li class="breadcrumb-item active">老师列表</li>';
            el.innerHTML = parts.join('');
            return;
        }
        var tName = pickNum(st.teacher, 'teacherName', 'name', 'ownerName') || '老师';
        // 扁平表形态下根层就是"这位老师共享的全部"，但路径同样要能回退
        var rootLabel = esc(tName) + (st.flat ? '（共享）' : '');
        if (st.crumbs.length === 0) {
            parts.push('<li class="breadcrumb-item active">' + rootLabel + '</li>');
        } else {
            parts.push('<li class="breadcrumb-item" style="cursor:pointer" onclick="TchShare.reopenTeacher()">' + rootLabel + '</li>');
            st.crumbs.forEach(function (c, i) {
                var active = (i === st.crumbs.length - 1);
                parts.push('<li class="breadcrumb-item' + (active ? ' active' : '') + '"'
                    + (active ? '' : ' style="cursor:pointer" onclick="TchShare.goCrumb(' + i + ')"')
                    + '>' + esc(c.name) + '</li>');
            });
        }
        el.innerHTML = parts.join('');
    }

    function avatar(t) {
        var p = pickNum(t, 'picture', 'avatar', 'photo', 'headImage') || '';
        if (p && /^https?:/.test(p)) {
            // example.com 那批 CDN 只认空 Referer
            return '<img referrerpolicy="no-referrer" src="' + esc(p) + '" style="width:38px;height:38px;border-radius:50%;object-fit:cover;margin-right:10px;flex:0 0 auto">';
        }
        var n = String(pickNum(t, 'teacherName', 'name', 'ownerName') || '?');
        return '<span style="width:38px;height:38px;border-radius:50%;background:#e7f1ff;color:#2b6cb0;display:inline-flex;align-items:center;justify-content:center;margin-right:10px;flex:0 0 auto">'
            + esc(n.slice(-2)) + '</span>';
    }

    function renderTeachers(list) {
        if (!list.length) {
            renderMsg('<div class="text-muted p-3">没有老师给你分享文件。</div>');
            return;
        }
        var html = '';
        list.forEach(function (t, i) {
            var name = pickNum(t, 'teacherName', 'name', 'ownerName') || ('老师 ' + (i + 1));
            var sub = pickNum(t, 'updateTime', 'time', 'createTime') || '';
            html += '<a href="javascript:void(0)" class="list-group-item list-group-item-action d-flex align-items-center"'
                + ' onclick="TchShare.openTeacher(TchShare._ti(' + i + '))">'
                + avatar(t)
                + '<span class="flex-grow-1 text-truncate">' + esc(name) + '</span>'
                + (sub ? '<small class="text-muted ms-2">' + esc(String(sub).slice(0, 10)) + '</small>' : '')
                + '</a>';
        });
        renderMsg(html);
    }

    var ICON_BY_TYPE = { 0: '📁', 3: '📙', 4: '📘', 5: '📕', 7: '🖼️', 10: '📗', 8: '🎬', 6: '🎵', 9: '🗜️' };

    function iconFor(f) {
        var t = ftype(f);
        if (t !== null && ICON_BY_TYPE[t]) return ICON_BY_TYPE[t];
        var name = String(pickNum(f, 'fileName', 'name', 'title') || '');
        var m = name.toLowerCase().match(/\.([a-z0-9]+)(?:\?|$)/);
        var map = {
            pdf: '📕', doc: '📘', docx: '📘', ppt: '📙', pptx: '📙', xls: '📗', xlsx: '📗',
            png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', bmp: '🖼️',
            mp4: '🎬', mov: '🎬', m4v: '🎬', mp3: '🎵', zip: '🗜️', rar: '🗜️'
        };
        return map[m ? m[1] : ''] || '📄';
    }

    function renderFiles(list) {
        if (!list.length) {
            renderMsg('<div class="text-muted p-3">这个目录下没有内容。</div>');
            return;
        }
        var html = '';
        list.forEach(function (f, i) {
            var name = pickNum(f, 'fileName', 'name', 'title') || ('文件 ' + (i + 1));
            var when = pickNum(f, 'shareTime', 'createTime', 'updateTime') || '';
            var who = st.flat ? (pickNum(f, 'ownerName', 'teacherName') || '') : '';
            var t = ftype(f);
            var badge = (t !== null && TYPE_NAME[t]) ? TYPE_NAME[t] : '';
            var folder = isFolder(f);
            html += '<a href="javascript:void(0)" class="list-group-item list-group-item-action d-flex align-items-center"'
                + ' onclick="TchShare.openNode(TchShare._fi(' + i + '))">'
                + '<span style="margin-right:10px">' + iconFor(f) + '</span>'
                + '<span class="flex-grow-1 text-truncate">' + esc(name) + '</span>'
                + (badge ? '<small class="text-muted ms-2">' + esc(badge) + '</small>' : '')
                + (who ? '<small class="text-muted ms-2">' + esc(who) + '</small>' : '')
                + (when ? '<small class="text-muted ms-2">' + esc(String(when).slice(0, 10)) + '</small>' : '')
                + (folder ? '<span class="text-muted ms-2">›</span>' : '')
                + '</a>';
        });
        renderMsg(html);
    }

    // 文件卡片：直链直接用，不联网；图片/PDF 顺便内嵌预览
    function renderFileCard(f, url) {
        var name = pickNum(f, 'fileName', 'name', 'title') || '文件';
        var t = ftype(f);
        var kind = (t !== null && TYPE_NAME[t]) ? TYPE_NAME[t] : '文件';
        var u = safeUrl(url);
        var ext = (name.toLowerCase().match(/\.([a-z0-9]+)$/) || [])[1] || '';
        var preview = '';
        if (t === 7 || /^(png|jpe?g|gif|webp|bmp)$/.test(ext)) {
            preview = '<img referrerpolicy="no-referrer" src="' + esc(u) + '"'
                + ' style="max-width:100%;max-height:56vh;display:block;margin:8px auto;border-radius:6px">';
        } else if (t === 5 || ext === 'pdf') {
            preview = '<iframe referrerpolicy="no-referrer" src="' + esc(u) + '"'
                + ' style="width:100%;height:56vh;border:0;background:#fff"></iframe>';
        }
        renderMsg('<div class="p-3">'
            + '<div class="d-flex align-items-center mb-2">'
            + '<span style="margin-right:8px">' + iconFor(f) + '</span>'
            + '<strong class="text-truncate">' + esc(name) + '</strong>'
            + '<small class="text-muted ms-2">' + esc(kind) + '</small></div>'
            + '<div class="mb-2">'
            + '<a class="btn btn-sm btn-primary me-2" href="' + esc(u) + '" target="_blank" rel="noreferrer"'
            + ' data-u="' + esc(u) + '">打开</a>'
            + '<a class="btn btn-sm btn-outline-secondary me-2" href="' + esc(u) + '"'
            + ' download="' + esc(name) + '">下载</a>'
            + '<button class="btn btn-sm btn-outline-secondary me-2" data-u="' + esc(u) + '"'
            + ' onclick="TchShare.copyUrl(this.dataset.u)">复制链接</button>'
            + '<button class="btn btn-sm btn-outline-secondary me-2" data-u="' + esc(u) + '"'
            + ' onclick="TchShare.testUrl(this.dataset.u)">测试直链</button>'
            + '<button class="btn btn-sm btn-outline-secondary" onclick="TchShare.reopenTeacher()">返回</button>'
            + '</div>'
            + '<div class="small text-muted text-break">' + esc(u) + '</div>'
            + '<div id="tchShareProbe" class="small"></div>'
            + preview
            + '<div class="small text-muted mt-2">若点「打开」没反应，用「复制链接」粘到系统浏览器；'
            + '若下方预览空白，说明 OSS 不允许内嵌或不是公开读。</div>'
            + '</div>');
    }

    /* ---------------- 打开 / 复制 / 直链自检 ---------------- */

    function probeBox() {
        var b = $('tchShareProbe');
        if (b) return b;
        // 没打开过文件卡片时 #tchShareProbe 还不存在，自检结果就无处可写 → 按钮看起来"没用"
        var pane = $('tchShare');
        if (!pane) return null;
        b = document.createElement('div');
        b.id = 'tchShareProbe';
        b.className = 'p-2';
        pane.insertBefore(b, pane.firstChild);
        return b;
    }

    function toast(msg, cls) {
        var b = probeBox();
        if (b) b.innerHTML = '<span class="' + (cls || 'text-success') + '">' + esc(msg) + '</span>';
    }

    function openUrl(u) {
        if (!u) return;
        try {
            if (typeof window.TbOpenExternal === 'function') { window.TbOpenExternal(u); return; }
        } catch (e) { /* ignore */ }
        var w = null;
        try { w = window.open(u, '_blank', 'noopener'); } catch (e) { w = null; }
        if (!w) { try { location.href = u; } catch (e) { /* ignore */ } }
    }

    function fallbackCopy(u) {
        try {
            var ta = document.createElement('textarea');
            ta.value = u;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            toast('链接已复制');
        } catch (e) {
            toast('复制失败：请手动选中上面的链接', 'text-danger');
        }
    }

    function copyUrl(u) {
        if (!u) return;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(u).then(function () { toast('链接已复制'); },
                    function () { fallbackCopy(u); });
                return;
            }
        } catch (e) { /* ignore */ }
        fallbackCopy(u);
    }

    // 用一次普通 GET 看服务端到底回了什么；被 CORS 拦住时明确说明"这不等于链接无效"
    async function testUrl(u) {
        if (!u) return;
        var b = probeBox();
        if (b) b.innerHTML = '<span class="text-muted">正在测试直链…</span>';
        try {
            var r = await fetch(u, { method: 'GET', cache: 'no-store' });
            var ct = r.headers.get('content-type') || '(无)';
            var body = '';
            if (r.status >= 400) { try { body = (await r.text()).slice(0, 400); } catch (e) { /* ignore */ } }
            var tip = '';
            if (r.status === 403 || r.status === 401) {
                tip = '<div class="text-danger">OSS 拒绝访问 → 这个对象不是公开读，需要带签名参数（STS）才能取。</div>';
            } else if (r.status === 404) {
                tip = '<div class="text-danger">对象不存在（404）→ 可能已被删除或文件名不同。</div>';
            } else if (r.status === 200 || r.status === 206) {
                tip = '<div class="text-success">直链可用（' + esc(ct) + '）。若打不开是浏览器/播放器不支持这个格式。</div>';
            }
            if (b) b.innerHTML = '<div class="mt-2 p-2 border rounded">HTTP <b>' + r.status + '</b>　'
                + 'Content-Type <code>' + esc(ct) + '</code>'
                + (body ? '<pre class="small mt-1 mb-0" style="white-space:pre-wrap;max-height:16vh;overflow:auto">'
                    + esc(body) + '</pre>' : '')
                + tip + '</div>';
        } catch (e) {
            if (b) b.innerHTML = '<div class="mt-1 text-muted">直链测试被浏览器 CORS 拦住（'
                + esc(e.message) + '）。这<b>不</b>说明链接无效，只说明该 OSS 没给浏览器跨域读权限。</div>';
        }
    }

    /* ---------------- 网络自检：一次点出「网络不通 / 403 / CORS」 ---------------- */

    function withTimeout(p, ms) {
        return new Promise(function (resolve, reject) {
            var done = false;
            var t = setTimeout(function () {
                if (!done) { done = true; reject(new Error('本地超时 ' + ms + 'ms')); }
            }, ms);
            p.then(function (v) { if (!done) { done = true; clearTimeout(t); resolve(v); } },
                function (e) { if (!done) { done = true; clearTimeout(t); reject(e); } });
        });
    }

    function firstUrlInList() {
        try {
            var box = document.getElementById('tchShareList');
            if (!box) return '';
            var m = String(box.innerHTML || '').match(/https?:\/\/[^\s"'<>\\)]+/);
            return m ? m[0] : '';
        } catch (e) { return ''; }
    }

    async function netcheck() {
        var b = probeBox();
        if (b) b.innerHTML = '<span class="text-muted">正在自检（依次试 5 个地址）…</span>';

        var base = String(window.API_BASE_URL || '').replace(/\/+$/, '');
        var tok = localStorage.getItem('token') || '';
        var rows = [];

        function add(label, url, opt) {
            opt = opt || {};
            return withTimeout(fetch(url, {
                method: 'GET', cache: 'no-store', headers: opt.headers || {}
            }), opt.timeout || 9000).then(function (r) {
                return r.text().then(function (t) {
                    rows.push({
                        label: label, url: url, ok: true, status: r.status,
                        ct: r.headers.get('content-type') || '',
                        head: String(t || '').replace(/\s+/g, ' ').slice(0, 100)
                    });
                });
            }, function (e) {
                rows.push({ label: label, url: url, ok: false, err: (e && e.message) || String(e) });
            });
        }

        await add('API 根', base + '/');
        await add('API 不带 token', base + '/CloudNotes/api/SuperShare/RetrieveTeachers');
        await add('API 带 token（明文探测）', base + '/CloudNotes/api/SuperShare/RetrieveTeachers',
            { headers: { Authorization: 'Bearer ' + tok } });
        await add('OSS 主机', 'https://<oss-bucket>.oss-cn-shenzhen.aliyuncs.com/');

        var fileUrl = firstUrlInList();
        if (fileUrl) await add('当前文件直链', fileUrl);

        renderNet(rows);
    }

    function renderNet(rows) {
        var b = probeBox();
        if (!b) return;
        var h = '<table class="table table-sm mb-0"><thead><tr><th>目标</th><th>结果</th></tr></thead><tbody>';
        rows.forEach(function (r) {
            var res;
            if (!r.ok) {
                res = '<span class="text-danger">请求失败：' + esc(r.err) + '</span>';
            } else if (r.status === 200) {
                res = '<span class="text-success">200</span> <code>' + esc(r.ct) + '</code>';
            } else if (r.status === 401 || r.status === 403) {
                res = '<span class="text-warning">' + r.status + '</span>';
            } else {
                res = '<span class="text-muted">' + r.status + '</span> <code>' + esc(r.ct) + '</code>';
            }
            h += '<tr><td class="small">' + esc(r.label)
                + '<div class="text-muted" style="word-break:break-all">' + esc(r.url) + '</div></td>'
                + '<td class="small">' + res + (r.head ? '<div class="text-muted" style="word-break:break-all">'
                    + esc(r.head) + '</div>' : '') + '</td></tr>';
        });
        h += '</tbody></table>';
        b.innerHTML = h;
    }

    function renderContent(data, file) {
        var name = pickNum(file, 'fileName', 'name', 'title') || '文件';
        var links = [];

        (function walk(o, path) {
            if (!o || typeof o !== 'object' || path.split('.').length > 4) return;
            for (var k in o) {
                if (!Object.prototype.hasOwnProperty.call(o, k)) continue;
                var v = o[k];
                if (typeof v === 'string' && /^https?:\/\//.test(v) && v.length > 12) {
                    links.push({ key: path ? path + '.' + k : k, url: v });
                } else if (v && typeof v === 'object') {
                    walk(v, path ? path + '.' + k : k);
                }
            }
        })(data, '');

        var text = '';
        ['content', 'html', 'text', 'body', 'richText'].forEach(function (k) {
            if (!text && typeof data[k] === 'string') text = data[k];
        });

        var btns = links.map(function (l) {
            return '<a class="btn btn-sm btn-outline-primary me-1 mb-1" target="_blank" rel="noopener noreferrer" href="'
                + esc(l.url) + '">打开 ' + esc(l.key) + '</a>';
        }).join('');

        renderMsg('<div class="p-2">'
            + '<div class="d-flex align-items-center mb-2"><span style="margin-right:8px">' + iconFor(file) + '</span>'
            + '<strong class="text-truncate">' + esc(name) + '</strong></div>'
            + (btns ? '<div class="mb-2">' + btns + '</div>'
                : '<div class="text-muted small mb-2">没有直接下载链接，下面是接口原始返回。</div>')
            + (text ? '<div class="border rounded p-2" style="max-height:45vh;overflow:auto">'
                + (text.length > 20000 ? esc(text.slice(0, 20000)) + '…' : esc(text)) + '</div>' : '')
            + '<details class="mt-2"><summary class="small text-muted">原始 JSON</summary>'
            + '<pre class="small" style="max-height:30vh;overflow:auto">' + esc(j(data)) + '</pre></details>'
            + '</div>');
    }

    /* ---------------- 数据加载 ---------------- */

    async function loadEntry() {
        st.teacher = null; st.crumbs = []; st.flat = false; st.files = [];
        renderCrumbs();
        renderLoading('正在读取老师分享给你的文件…');

        var r = await probe('teachers', [
            { ep: 'RetrieveTeachers', plain: '' },                  // 实测命中（example-school 部署，空参）
            { ep: 'RetrieveTeachers', plain: '0' },
            { ep: 'GetSharedToMeByTeacher', plain: '' },
            { ep: 'GetSharedToMeByTeacher', plain: 'type=1' },
            { ep: 'GetSharedToMeByTeacher', plain: 'pageIndex=1&pageSize=200' },
            { ep: 'GetSharedToMeByTeacher', plain: 'SkipCount=0&MaxResultCount=200' },
            { ep: 'GetSharedToMeByTeacher', plain: 'teacherId=0' },
            { ep: 'GetSharedToMeByTeacher', plain: 'shareType=0' },
            { ep: 'GetSharedToMeByTeacher', plain: null }
        ], function (d) {
            return pickArr(d, KEY_FILES).length === 0 && pickArr(d, KEY_TCH).length === 0;
        });
        st.lastLog = r.log;
        st.lastEntryRaw = r.data;

        if (!r.data) {
            renderMsg('<div class="alert alert-warning m-2">所有候选端点都失败了。点「诊断」看每个端点的真实返回。</div>');
            return;
        }

        // 形态一：直接返回文件列表
        var files = pickArr(r.data, KEY_FILES);
        if (files.length) {
            // 顶层数组有歧义：元素带 teacherName 而没有 fileId/fileName 时，其实是老师列表
            var first = files[0];
            if (first && typeof first === 'object'
                && !pickNum(first, 'fileId', 'fileName', 'fileUrl', 'shareId')
                && pickNum(first, 'teacherName', 'teacherId')) {
                st.teachers = files;
                renderTeachers(files);
                return;
            }
            st.flat = true;
            st.files = files;
            renderCrumbs();
            renderFiles(files);
            return;
        }

        // 形态二：返回老师列表
        var ts = pickArr(r.data, KEY_TCH);
        if (ts.length) {
            st.teachers = ts;
            renderTeachers(ts);
            return;
        }

        var total = (r.data && typeof r.data === 'object' && 'totalCount' in r.data)
            ? '（服务端 totalCount = ' + r.data.totalCount + '）' : '';
        renderRaw(r.data, '服务端明确返回 0 条' + total + '。不是解析失败，而是这个账号在当前接口上确实没有'
            + '「老师分享给我」的记录。若你在 App 里能看到这些文件，说明 App 走的是另一条入口。');
    }

    /* ---------- 老师的"全部共享"入口 ----------
     * 实测（徐元华 ownerId=58）：
     *   GetByParentId?parentId=0&ownerId=58&isRoot=false  -> 只有根目录 8 个文件，无文件夹
     *   GetSharedToMeByTeacher?teacherId=58               -> 37 条，type 分布 {0:21, 4:9, 5:4, 7:1, 10:2}
     * 也就是说：文件夹不在 parentId=0 这一层，而在这个"全量共享"接口里。
     * 该接口返回的是扁平表，每条自带 parentId，所以整棵树可以在前端本地拼出来，
     * 不用为每个文件夹再发一次请求。
     */
    function entryCands(oid) {
        var c = [];
        if (oid !== null) {
            c.push({ ep: 'GetSharedToMeByTeacher', shape: 'byTeacher', plain: 'teacherId=' + oid });
            c.push({ ep: 'GetByParentId', shape: 'oid-false', plain: 'parentId=0&ownerId=' + oid + '&isRoot=false' });
        }
        c.push({ ep: 'GetByParentId', shape: 'bare', plain: 'parentId=0&isRoot=false' });
        return c;
    }

    function pidOf(x) {
        var v = pickNum(x, 'parentId', 'parentid', 'parentFileId', 'pid');
        return (v === null || v === undefined) ? null : String(v);
    }

    // 本地建索引：parentId -> [子节点]
    function indexTree(items) {
        var map = {};
        (items || []).forEach(function (it) {
            var p = pidOf(it);
            if (p === null) p = '0';
            (map[p] = map[p] || []).push(it);
        });
        st.byParent = map;
    }

    function childrenOf(pid) {
        if (!st.byParent) return [];
        return st.byParent[String(pid)] || [];
    }

    // 根节点：parentId 为 0 / 空 / 不在本表里的（父文件夹本身没被共享时，子项就成了孤儿）
    function rootChildren() {
        var all = st.all || [];
        if (!st.byParent) return all;
        var ids = {};
        all.forEach(function (it) {
            var id = pickNum(it, 'fileId', 'id', 'resourceId');
            if (id !== null) ids[String(id)] = 1;
        });
        return all.filter(function (it) {
            var p = pidOf(it);
            if (p === null || p === '0' || p === '') return true;
            return !ids[p];   // 父节点不在本表 → 当成根
        });
    }

    function sortFoldersFirst(list) {
        return (list || []).slice().sort(function (a, b) {
            var fa = isFolder(a) ? 0 : 1, fb = isFolder(b) ? 0 : 1;
            if (fa !== fb) return fa - fb;
            return String(pickNum(a, 'fileName', 'name', 'title') || '')
                .localeCompare(String(pickNum(b, 'fileName', 'name', 'title') || ''), 'zh');
        });
    }

    function childCands(parentId) {
        // 实测：GetByParentId 的第二个参数名是 ownerId（传 teacherId 会 code=1001 请求参数有误）
        var oid = pickNum(st.teacher, 'ownerId', 'teacherId', 'id', 'userId');
        var pid = (parentId === null || parentId === undefined) ? '0' : String(parentId);
        var c = [];
        if (oid !== null) {
            c.push({ ep: 'GetByParentId', shape: 'oid-false', plain: 'parentId=' + pid + '&ownerId=' + oid + '&isRoot=false' });
            c.push({ ep: 'GetByParentId', shape: 'oid-true', plain: 'parentId=' + pid + '&ownerId=' + oid + '&isRoot=true' });
            c.push({ ep: 'GetByParentId', shape: 'oid-lower', plain: 'parentid=' + pid + '&ownerid=' + oid + '&isRoot=false' });
            c.push({ ep: 'GetByParentId', shape: 'tid', plain: 'parentId=' + pid + '&teacherId=' + oid + '&isRoot=false' });
        }
        c.push({ ep: 'GetByParentId', shape: 'bare', plain: 'parentId=' + pid + '&isRoot=false' });
        c.push({ ep: 'GetByParentId', shape: 'bareNote', plain: 'parentid=' + pid + '&isNoteNode=false' });
        return c;
    }

    function ownerOf(f) {
        return pickNum(f, 'ownerId', 'teacherId', 'ownerUserId')
            || pickNum(st.teacher, 'ownerId', 'teacherId', 'id');
    }

    async function openTeacher(t) {
        if (!t) return;
        st.teacher = t; st.crumbs = []; st.flat = false;
        st.all = null; st.byParent = null;
        renderCrumbs();
        renderLoading('正在读取该老师分享的文件…');
        var oid = pickNum(t, 'ownerId', 'teacherId', 'id', 'userId');
        // 缓存键按老师区分：入口端点与"列目录"端点不是同一个接口，
        // 共用 children 这个键会把之前命中 8 条散文件的 GetByParentId 顶到最前面，
        // 导致每次都只看到 8 个文件、永远看不到文件夹（v8 修复）。
        var r = await probe('entry:' + (oid === null ? 'x' : oid), entryCands(oid), isEmptyFiles);
        st.lastLog = r.log;
        st.lastChildRaw = r.data;
        st.lastChildPlain = r.plain || '';
        if (!r.data) {
            renderMsg('<div class="alert alert-warning m-2">这位老师的文件列表读不到。'
                + '<div class="small mt-1">' + (r.log || []).map(esc).join('<br>') + '</div></div>');
            return;
        }
        var all = pickArr(r.data, KEY_FILES);
        st.all = all;
        st.flat = (r.shape === 'byTeacher');
        indexTree(all);
        st.files = sortFoldersFirst(rootChildren());
        st.lastEntryRaw = { totalCount: all.length, shape: r.shape, plain: r.plain, root: st.files.length };
        if (!st.files.length) {
            renderRaw(r.data, '接口通了但没解析出文件：本次参数 = ' + (r.plain || '(无)')
                + '；探测记录 ' + (r.log || []).length + ' 条。原始返回：');
            return;
        }
        if (st.flat) st.lastLog.push('[entry] ' + r.plain + ' → 扁平表 ' + all.length
            + ' 条，本地拼树后根层 ' + st.files.length + ' 项（文件夹 '
            + st.files.filter(isFolder).length + ' 个）');
        renderFiles(st.files);
    }

    function reopenTeacher() { return openTeacher(st.teacher); }

    function isEmptyFiles(d) {
        return pickArr(d, KEY_FILES).length === 0;
    }

    async function openFolder(f) {
        if (!f) return;
        var fid = pickNum(f, 'fileId', 'id', 'resourceId');
        var fname = pickNum(f, 'fileName', 'name', 'title') || '文件夹';
        if (fid === null) {
            renderMsg('<div class="alert alert-warning m-2">这个条目没有 fileId，无法进入。</div>');
            return;
        }
        // 优先用本地索引：GetSharedToMeByTeacher 返回的扁平表里每条自带 parentId，
        // 整棵树在前端就能拼出来，不用为每个文件夹再发一次请求。
        var local = childrenOf(fid);
        if (local.length) {
            st.crumbs.push({ id: String(fid), name: fname });
            st.files = sortFoldersFirst(local);
            renderCrumbs();
            renderFiles(st.files);
            return;
        }
        renderLoading('正在打开「' + fname + '」…');
        var r = await probe('children', childCands(fid), isEmptyFiles);
        st.lastLog = r.log;
        st.lastChildRaw = r.data;
        st.lastChildPlain = r.plain || '';
        var list = r.data ? pickArr(r.data, KEY_FILES) : [];
        st.crumbs.push({ id: String(fid), name: fname });
        st.files = sortFoldersFirst(list);
        renderCrumbs();
        if (!list.length) {
            renderRaw(r.data, '「' + fname + '」接口已通，但没有内容数组。原始返回如下：');
            return;
        }
        renderFiles(st.files);
    }

    async function openNode(f) {
        if (!f) return;

        // 文件夹：按 fileId 取子节点
        if (isFolder(f)) return openFolder(f);

        // 文件：条目自带公开直链，直接给，不必再请求服务端
        var url = fileUrlOf(f);
        if (url) return renderFileCard(f, url);

        // 只有没直链时才去问内容接口
        var fid = pickNum(f, 'fileId', 'id', 'resourceId');
        var ft = ftype(f);
        if (fid !== null) {
            renderLoading('正在打开文件…');
            var oid = ownerOf(f);
            var c = [];
            if (oid !== null) {
                c.push({ ep: 'GetResourceByFileId', plain: 'fileId=' + fid + '&ownerId=' + oid });
                c.push({ ep: 'GetShareFileById', plain: 'fileId=' + fid + '&ownerId=' + oid });
            }
            c.push({ ep: 'GetResourceByFileId', plain: 'fileId=' + fid });
            c.push({ ep: 'GetResourceByFileId', plain: 'fileId=' + fid + (ft !== null ? '&type=' + ft : '') });
            c.push({ ep: 'GetShareFileById', plain: 'fileId=' + fid + '&shareId=' + (pickNum(f, 'shareId') || '') });
            var r = await probe('content', c);
            st.lastLog = r.log;
            st.lastContentRaw = r.data;
            if (r.data) { renderContent(r.data, f); return; }
        }
        renderContent(f, f);
    }

    async function goCrumb(i) {
        st.crumbs = st.crumbs.slice(0, i + 1);
        var target = st.crumbs[st.crumbs.length - 1];
        renderCrumbs();
        // 本地索引优先
        if (st.byParent) {
            var local = target ? childrenOf(target.id) : rootChildren();
            if (local.length) {
                st.files = sortFoldersFirst(local);
                renderFiles(st.files);
                return;
            }
        }
        renderLoading('正在读取…');
        var r = await probe('children', childCands(target ? target.id : 0), isEmptyFiles);
        st.lastLog = r.log;
        st.lastChildRaw = r.data;
        st.lastChildPlain = r.plain || '';
        if (!r.data) { renderMsg('<div class="alert alert-warning m-2">读取失败，点「诊断」。</div>'); return; }
        st.files = sortFoldersFirst(pickArr(r.data, KEY_FILES));
        if (!st.files.length) { renderRaw(r.data, '该目录接口已通，但没有文件数组。原始返回如下：'); return; }
        renderFiles(st.files);
    }

    function keysOf(d) {
        if (d === null || d === undefined) return '(null)';
        if (typeof d !== 'object') return '(' + typeof d + ' 原始值)';
        if (Array.isArray(d)) return '[顶层就是数组，长度 ' + d.length + ']';
        return Object.keys(d).join(', ');
    }

    function diag() {
        var box = $('tchShareDiag');
        if (!box) return;
        box.textContent = '模块版本      = ' + VERSION + '\n'
            + 'API_BASE_URL = ' + apiBase() + '\n'
            + 'special 路径  = ' + specialPath() + '\n'
            + 'token 是否存在 = ' + (token() ? '是（长度 ' + token().length + '）' : '否') + '\n'
            + '已记忆端点    = ' + (localStorage.getItem(CACHE_KEY) || '{}') + '\n'
            + '界面形态      = ' + (st.flat ? '平铺文件列表' : '老师列表 → 文件') + '\n'
            + '\n入口返回 顶层键 = ' + keysOf(st.lastEntryRaw)
            + '  | 识别老师 ' + (st.teachers || []).length + ' 位'
            + '\n目录返回 顶层键 = ' + keysOf(st.lastChildRaw)
            + '  | 参数 ' + (st.lastChildPlain || '(无)')
            + '  | 识别条目 ' + (st.files || []).length
            + '\n内容返回 顶层键 = ' + keysOf(st.lastContentRaw)
            + '\n---- 最近一次探测 ----\n'
            + (st.lastLog.length ? st.lastLog.join('\n') : '（还没探测过，先点「刷新」）');
        box.style.display = 'block';
    }

    // 把三层原始返回 + 第一条条目的字段名一次摊开，便于对照真实字段
    function dump() {
        var parts = [];
        parts.push('===== 入口（老师列表）=====\n' + j(st.lastEntryRaw));
        parts.push('===== 目录（老师 '
            + (pickNum(st.teacher, 'teacherName', 'name', 'ownerName') || '-')
            + '）参数 ' + (st.lastChildPlain || '(无)') + ' =====\n' + j(st.lastChildRaw));
        if (st.lastContentRaw) parts.push('===== 文件内容 =====\n' + j(st.lastContentRaw));
        var first = (st.files && st.files.length) ? st.files[0]
            : ((st.teachers && st.teachers.length) ? st.teachers[0] : null);
        if (first && typeof first === 'object') {
            parts.push('===== 第一条条目的字段名 =====\n' + Object.keys(first).join('\n')
                + '\n\n===== 第一条条目原样 =====\n' + j(first));
        }
        renderMsg('<div class="alert alert-warning m-2 mb-1">把下面整段复制发我</div>'
            + '<pre class="small m-2" style="max-height:52vh;overflow:auto;white-space:pre-wrap">'
            + esc(parts.join('\n\n')) + '</pre>');
    }

    /* ---------------- 挖文件夹：把 parentId=0 的所有候选全跑一遍 ---------------- */

    async function rawFetch(url) {
        var r = await fetch(url, { headers: { 'Authorization': 'Bearer ' + token() } });
        var txt = await r.text();
        var data = null;
        try {
            var o = JSON.parse(txt);
            data = decData(o.data !== undefined ? o.data : o.result);
        } catch (e) { data = null; }
        return { status: r.status, data: data };
    }

    // 与 openTeacher 的区别：probe() 命中第一个非空就停，
    // 所以 isRoot=true / 别的 parentId 根本没机会被试。这里逐条全跑，逐个报“条数 + type 分布”。
    async function scan(t) {
        var box = $('tchShareDiag');
        t = t || st.teacher;
        if (!t) {
            renderMsg('<div class="alert alert-warning m-2">先在老师列表里点一位老师，再点「挖文件夹」。</div>');
            return;
        }
        var name = pickNum(t, 'teacherName', 'name', 'ownerName') || '?';
        var oid = pickNum(t, 'ownerId', 'teacherId', 'id', 'userId');
        var lines = ['老师 = ' + name + '   ownerId = ' + oid, ''];

        var cands = childCands(0).concat([
            { ep: 'GetByParentId', shape: 'pid=oid', plain: 'parentId=' + oid + '&ownerId=' + oid + '&isRoot=false' },
            { ep: 'GetByParentId', shape: 'pid=-1', plain: 'parentId=-1&ownerId=' + oid + '&isRoot=false' },
            { ep: 'GetByParentId', shape: 'noteNode', plain: 'parentId=0&ownerId=' + oid + '&isNoteNode=true' },
            { ep: 'GetSharedToMe', shape: 'byTeacher', plain: 'teacherId=' + oid },
            { ep: 'GetSharedToMeByTeacher', shape: 'teacherId', plain: 'teacherId=' + oid }
        ]);

        for (var i = 0; i < cands.length; i++) {
            var c = cands[i];
            var urls = buildUrls(c.ep, c.plain);
            for (var u = 0; u < urls.length; u++) {
                var res;
                try {
                    res = await rawFetch(urls[u]);
                } catch (e) {
                    lines.push('[' + c.shape + '] ' + c.ep + ' -> ERR ' + (e && e.message));
                    continue;
                }
                var arr = pickArr(res.data, KEY_FILES);
                var types = {};
                arr.forEach(function (f) {
                    var k = (f && f.type !== undefined) ? String(f.type) : '?';
                    types[k] = (types[k] || 0) + 1;
                });
                lines.push('[' + c.shape + '] ' + c.ep
                    + ' HTTP ' + res.status
                    + ' | ' + c.plain
                    + ' | 条数 ' + arr.length
                    + ' | type ' + JSON.stringify(types)
                    + (arr.length ? ' | 首条 ' + (pickNum(arr[0], 'fileName', 'name', 'title') || '') : ''));
            }
        }

        lines.push('');
        lines.push('把以上整段贴回来。「条数>0 且 type 里有 0」的那一行就是文件夹入口。');
        var text = lines.join('\n');
        if (box) { box.textContent = text; box.style.display = 'block'; }
        console.log(text);
    }

    /* ---------------- 对外 ---------------- */

    return {
        home: loadEntry,
        reload: function () { return loadEntry(); },
        openTeacher: openTeacher,
        reopenTeacher: reopenTeacher,
        openFolder: openFolder,
        openNode: openNode,
        goCrumb: goCrumb,
        diag: diag,
        dump: dump,
        scan: scan,
        openUrl: openUrl,
        copyUrl: copyUrl,
        testUrl: testUrl,
        netcheck: netcheck,
        _t: function () { return st.teacher; },
        _ti: function (i) { return (st.teachers || [])[i]; },
        _fi: function (i) { return (st.files || [])[i]; },
        resetCache: function () { localStorage.removeItem(CACHE_KEY); diag(); },
        forceAll: function () { localStorage.removeItem(CACHE_KEY); return loadEntry(); }
    };
})();
