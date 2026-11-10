/* =====================================================================
 * 在线专栏：一键下载 / 批量下载
 *
 * 为什么这样做：
 *   专栏页面 navPage.html 在 sxz.school.example.com，与本页不同源，父页面读不到
 *   iframe 里的 DOM，所以没法"点它里面的按钮"。但专栏本身就是这个 SPA 用
 *   appWebSite/* 接口渲染出来的，于是直接复刻它的取数链路：
 *
 *     列列表  GetAllColListAsync（失败则 GetSubscribeListAsync）
 *     页列表  GetPageListByPagedAsync?colId=&skipCount=&maxResultCount=
 *     页详情  GetPageAsync?id=
 *
 *   拿到的 JSON 里逐层扫字符串，凡是长得像文件地址的（带扩展名，或 OSS 域名）
 *   都收进下载队列。这样不依赖字段名（服务端改字段名也不影响），也不需要知道
 *   "页里到底哪个字段是附件"。
 *
 *   用老 host 的 token（navToken，专栏登录拿到的），不是新 host 的 token。
 * ===================================================================== */
window.ColBatch = (function () {
    var API_DEFAULT = 'http://<api-host>';
    var DOC_EXT = /\.(pdf|docx?|pptx?|xlsx?|txt|md|csv|zip|rar|7z|mp4|mp3|wav|png|jpe?g|gif|webp|svg)(\?|#|$)/i;
    var MAX_PAGES = 300;      // 单个专栏最多取多少页
    var POOL = 4;             // 并发下载数

    function $(id) { return document.getElementById(id); }

    function msg(text, cls) {
        var el = $('col_batch_msg');
        if (el) {
            el.textContent = text || '';
            el.className = 'small ' + (cls || 'text-muted');
        }
        console.log('[ColBatch]', text);
    }

    function log(lines) {
        var pre = $('col_batch_out');
        if (!pre) return;
        pre.style.display = 'block';
        pre.textContent = lines.join('\n');
    }

    function apiBase() {
        var stored = localStorage.getItem('navApiHost');
        if (stored && /^https?:\/\//.test(stored)) return stored.replace(/\/+$/, '');
        var code = localStorage.getItem('colSchool') || 'sxz';
        return 'http://' + String(code).trim() + '.api.example.com';
    }

    function token() {
        return localStorage.getItem('navToken') || '';
    }

    async function call(path, params) {
        var url = apiBase() + '/api/services/app/appWebSite/' + path;
        if (params && Object.keys(params).length) {
            url += (url.indexOf('?') >= 0 ? '&' : '?') +
                Object.keys(params).map(function (k) {
                    return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
                }).join('&');
        }
        var res = await fetch(url, {
            headers: {
                'Authorization': 'Bearer ' + token(),
                'Accept': 'application/json'
            }
        });
        var text = await res.text();
        var json = null;
        try { json = JSON.parse(text); } catch (e) { /* 非 JSON */ }
        return { status: res.status, url: url, json: json, text: text };
    }

    // ABP 常见包装：{result:{...}} / {result:{items:[...]}} / 直接数组
    function unwrap(json) {
        if (!json) return null;
        if (json.result !== undefined) return json.result;
        return json;
    }

    function pickArray(v, depth) {
        depth = depth || 0;
        if (!v || depth > 4) return null;
        if (Array.isArray(v)) return v;
        if (typeof v !== 'object') return null;
        var preferred = ['items', 'list', 'data', 'colList', 'pageList', 'records', 'rows'];
        for (var i = 0; i < preferred.length; i++) {
            if (Array.isArray(v[preferred[i]])) return v[preferred[i]];
        }
        for (var k in v) {
            if (!Object.prototype.hasOwnProperty.call(v, k)) continue;
            var got = pickArray(v[k], depth + 1);
            if (got && got.length) return got;
        }
        return null;
    }

    // 递归收集 URL 字符串
    function harvest(v, out, depth) {
        out = out || [];
        depth = depth || 0;
        if (depth > 8 || v == null) return out;
        if (typeof v === 'string') {
            var s = v.trim();
            if (/^\/\//.test(s)) s = 'http:' + s;   // 协议相对地址
            if (/^https?:\/\//i.test(s)) {
                if (DOC_EXT.test(s) || /\/oss\//i.test(s) || /aliyuncs\.com|example\.com/i.test(s)) out.push(s);
            } else if (/<a[^>]+href=/i.test(s) || /<img[^>]+src=/i.test(s)) {
                var re = /(?:href|src)\s*=\s*["']([^"']+)["']/gi, m;
                while ((m = re.exec(s))) {
                    var u = m[1];
                    if (/^\/\//.test(u)) u = 'http:' + u;
                    if (/^https?:\/\//i.test(u) && DOC_EXT.test(u)) out.push(u);
                }
            }
            return out;
        }
        if (Array.isArray(v)) {
            for (var i = 0; i < v.length && i < 500; i++) harvest(v[i], out, depth + 1);
            return out;
        }
        if (typeof v === 'object') {
            for (var k in v) {
                if (Object.prototype.hasOwnProperty.call(v, k)) harvest(v[k], out, depth + 1);
            }
        }
        return out;
    }

    function fileNameOf(url, fallback) {
        try {
            var u = new URL(url, location.href);
            var name = decodeURIComponent(u.pathname.split('/').pop() || '');
            if (!name || !/\./.test(name)) name = (fallback || 'file') + (DOC_EXT.exec(u.pathname) ? DOC_EXT.exec(u.pathname)[1].length ? '.' + DOC_EXT.exec(u.pathname)[1].toLowerCase() : '' : '');
            return name.replace(/[\\/:*?"<>|]/g, '_').slice(0, 120);
        } catch (e) {
            return (fallback || 'file').slice(0, 80);
        }
    }

    function saveBlob(blob, filename) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () {
            try { URL.revokeObjectURL(url); } catch (e) {}
        }, 4000);
    }

    /* ---------------- 主流程 ---------------- */

    async function collectColumns(trace) {
        var r1 = await call('GetAllColListAsync', {});
        trace.push('GetAllColListAsync -> ' + r1.status + ' ' + (r1.json ? 'json' : 'text'));
        var cols = pickArray(unwrap(r1.json));
        if (!cols || !cols.length) {
            var r2 = await call('GetSubscribeListAsync', {});
            trace.push('GetSubscribeListAsync -> ' + r2.status + ' ' + (r2.json ? 'json' : 'text'));
            cols = pickArray(unwrap(r2.json));
        }
        return cols || [];
    }

    async function collectPublicPages(trace) {
        // 学生角色通常只有「公开专栏」，SPA 里对应 right==="public" 这条分支
        var r = await call('GetAllPublicPagesAsync', { skipCount: 0, maxResultCount: MAX_PAGES });
        trace.push('GetAllPublicPagesAsync -> ' + r.status + ' ' + (r.json ? 'json' : 'text'));
        return pickArray(unwrap(r.json)) || [];
    }

    async function collectPages(col, trace) {
        var colId = col.id || col.colId || col.Id;
        if (!colId) return [];
        var r = await call('GetPageListByPagedAsync', {
            colId: colId, skipCount: 0, maxResultCount: MAX_PAGES
        });
        trace.push('GetPageListByPagedAsync(colId=' + colId + ') -> ' + r.status);
        return pickArray(unwrap(r.json)) || [];
    }

    async function run() {
        if (!token()) { msg('先点上面的「专栏登录」拿到专栏 token', 'text-danger'); return; }
        var btn = $('col_batch_btn');
        if (btn) btn.disabled = true;
        var trace = [];
        try {
            msg('正在取专栏列表…');
            var cols = await collectColumns(trace);
            trace.push('专栏数 = ' + cols.length);
            if (!cols.length) {
                // 回退：学生角色常见的是「公开专栏」= 直接给页列表
                var pub = await collectPublicPages(trace);
                trace.push('公开页数 = ' + pub.length);
                if (pub.length) cols = [{ colName: '公开专栏', __pages: pub }];
            }
            if (!cols.length) {
                msg('没取到专栏列表（可能是权限或接口变更）', 'text-danger');
                log(trace.concat(['', '提示：请点「专栏接口诊断」把结果发我。']));
                return;
            }

            var files = {};   // url -> {url, name, col}
            for (var i = 0; i < cols.length; i++) {
                var col = cols[i];
                var colName = col.colName || col.name || col.title || ('专栏' + (i + 1));
                msg('正在取「' + colName + '」的页列表…（' + (i + 1) + '/' + cols.length + '）');
                var pages = col.__pages ? col.__pages : await collectPages(col, trace);
                trace.push('  ' + colName + ' : ' + pages.length + ' 页');

                for (var j = 0; j < pages.length; j++) {
                    var p = pages[j];
                    var urls = harvest(p);
                    // 页本身没带地址 → 取详情再扫
                    if (!urls.length) {
                        var pid = p.id || p.pageId || p.Id;
                        if (pid) {
                            var rd = await call('GetPageAsync', { id: pid });
                            if (rd.json) urls = harvest(unwrap(rd.json));
                        }
                    }
                    for (var u = 0; u < urls.length; u++) {
                        if (!files[urls[u]]) {
                            files[urls[u]] = {
                                url: urls[u],
                                col: colName,
                                name: fileNameOf(urls[u], (p.title || p.pageTitle || colName) + '_' + (j + 1))
                            };
                        }
                    }
                }
            }

            var list = Object.keys(files).map(function (k) { return files[k]; });
            trace.push('', '共发现可下载文件 ' + list.length + ' 个');
            if (!list.length) {
                msg('没在专栏数据里找到文件地址', 'text-danger');
                log(trace.concat(['', '这一版是按「JSON 里长得像文件的地址」抓的；如果确实是空白，说明文件地址不在列表/详情接口里，需要再抓一次真实下载请求。']));
                return;
            }

            var ok = 0, fail = 0;
            var zip = (window.JSZip && list.length > 1) ? new JSZip() : null;

            // 简单并发池
            var idx = 0;
            async function worker() {
                while (idx < list.length) {
                    var cur = list[idx++];
                    msg('下载 ' + (idx) + '/' + list.length + '：' + cur.name);
                    try {
                        var res = await fetch(cur.url);
                        if (!res.ok) throw new Error('HTTP ' + res.status);
                        var blob = await res.blob();
                        if (zip) zip.file((cur.col + '/' + cur.name).replace(/[\\/:*?"<>|]/g, '_'), blob);
                        else saveBlob(blob, cur.name);
                        ok++;
                    } catch (e) {
                        fail++;
                        trace.push('  失败 ' + cur.url + ' : ' + (e && e.message));
                    }
                }
            }
            var workers = [];
            for (var w = 0; w < Math.min(POOL, list.length); w++) workers.push(worker());
            await Promise.all(workers);

            if (zip && ok) {
                msg('正在打包…');
                var content = await zip.generateAsync(
                    { type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 6 } },
                    function (meta) { msg('打包 ' + (meta.percent | 0) + '%'); }
                );
                saveBlob(content, '在线专栏_' + new Date().toISOString().slice(0, 10) + '.zip');
            }
            msg('完成：成功 ' + ok + '，失败 ' + fail, fail ? 'text-warning' : 'text-success');
            log(trace);
        } catch (e) {
            msg('出错：' + ((e && e.message) || e), 'text-danger');
            log(trace.concat(['', String(e && e.stack || e)]));
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    // 只看不下载：把取到的结构与字段名打出来，便于定位
    async function diag() {
        if (!token()) { msg('先点「专栏登录」', 'text-danger'); return; }
        var trace = [];
        var cols = await collectColumns(trace);
        trace.push('专栏数 = ' + cols.length);
        if (cols.length) {
            trace.push('第一个专栏的字段： ' + Object.keys(cols[0]).join(', '));
            var pages = await collectPages(cols[0], trace);
            trace.push('第一页数 = ' + pages.length);
            if (pages.length) {
                trace.push('页字段： ' + Object.keys(pages[0]).join(', '));
                trace.push('页样例： ' + JSON.stringify(pages[0]).slice(0, 800));
                var urls = harvest(pages[0]);
                trace.push('该页扫到地址 = ' + urls.length);
                urls.slice(0, 5).forEach(function (u) { trace.push('  ' + u); });
            }
        }
        log(trace);
    }

    function bind() {
        var b1 = $('col_batch_btn');
        if (b1 && !b1.__bound) {
            b1.__bound = true;
            b1.addEventListener('click', function (e) { e.preventDefault(); run(); });
        }
        var b2 = $('col_batch_diag');
        if (b2 && !b2.__bound) {
            b2.__bound = true;
            b2.addEventListener('click', function (e) { e.preventDefault(); diag(); });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }

    return { run: run, diag: diag, harvest: harvest };
})();
