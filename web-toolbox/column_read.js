/*
 * column_read.js —— 在线专栏原生接口探测 / 读取
 *
 * 背景：SPA 的「在线专栏」一级导航（/#/index/onlineColumns）对学生角色不注册路由，
 * 访问会落到 404 页。专栏的数据面其实是一族 REST 接口：
 *   /api/services/app/appWebSite/{GetAllColListAsync,GetSubscribeListAsync,
 *     GetAllPublicPagesAsync,GetPageListByPagedAsync,GetPageAsync,GetSpecialPageListAsync}
 * 这些接口的参数名被 R8/minify 抹掉后无法从包体静态确定，因此先做探测矩阵：
 * 用多组候选参数各打一次，看哪个组合返回 code===0 且有数据。
 *
 * 用法：在线专栏页 → 「专栏接口诊断」按钮 → 把弹出来的文本整段贴回来。
 */
(() => {
    'use strict';

    const OUT_ID = 'col_diag_out';

    // 学校代码 → 老 host。官方客户端里 sxz 是硬编码默认值。
    function schoolHost(code) {
        return 'http://' + String(code || 'sxz').trim() + '.api.example.com';
    }

    function base() {
        // 专栏接口只认老 host（http://<学校代码>.api.example.com）。
        // 新 host（<api-host>）一律 403/404，测它没有意义。
        const stored = localStorage.getItem('navApiHost');
        if (stored && /^https?:\/\//.test(stored)) return stored.replace(/\/+$/, '');
        return schoolHost(localStorage.getItem('colSchool') || 'sxz');
    }

    function token() {
        // 同上：专栏接口要老 host 的 token
        return localStorage.getItem('navToken') || localStorage.getItem('token') || '';
    }

    function out(text) {
        const el = document.getElementById(OUT_ID);
        if (el) {
            el.style.display = 'block';
            el.textContent = text;
        }
        console.log(text);
    }

    // 只读端点；参数形状来自 SPA 源码（navPage chunk 里 /list 页的真实调用）：
    //   getPageListByPagedAsync({ colId, skipCount: (page-1)*size, maxResultCount: size })
    //   getAllPublicPages({ skipCount, maxResultCount })
    // 注意：是 skipCount 不是 pageIndex！之前用 pageIndex 探测全是 404。
    const ENDPOINTS = [
        { name: 'GetSubscribeListAsync', variants: ['', 'skipCount=0&maxResultCount=20', 'pageIndex=1&maxResultCount=20'] },
        { name: 'GetAllColListAsync', variants: ['skipCount=0&maxResultCount=20', ''] },
        { name: 'GetAllPublicPagesAsync', variants: ['skipCount=0&maxResultCount=20', ''] },
        { name: 'GetPageListByPagedAsync', variants: ['colId=1&skipCount=0&maxResultCount=20', 'skipCount=0&maxResultCount=20'] },
        // 之前 GET 这个返回 405 —— 405 的意思是「路由存在但动词不对」，所以它八成是 POST。
        { name: 'GetSpecialPageListAsync', method: 'POST', variants: [''], body: { skipCount: 0, maxResultCount: 20 } },
        { name: 'GetSpecialPageListAsync', method: 'POST', variants: [''] , body: { colId: 1, skipCount: 0, maxResultCount: 20 } },
        { name: 'GetPageAsync', variants: ['id=1'] },
        // 下面这族是「新式」公开读接口：返回 {success,error} 而不是 ABP 的 {result}。
        // GetPageAsync 已经证明 appWebSite/GetPageAsync 走的就是这套信封，
        // 说明真正给学生的读接口在 SpecialPageRegulation 控制器下，之前完全没测。
        { name: 'GetPagedSpecialPageListAsync', ctrl: 'SpecialPageRegulation', method: 'POST', variants: [''], body: { skipCount: 0, maxResultCount: 20 } },
        { name: 'GetPageAsync', ctrl: 'SpecialPageRegulation', variants: ['id=1'] }
    ];

    function qs(extra) {
        const parts = [];
        if (extra) parts.push(extra);
        parts.push('ran=' + Math.random().toString(36).slice(2));
        return parts.join('&');
    }

    function shape(txt) {
        // 给一段可读的"骨架"：顶层键 + 每个键的类型/长度
        try {
            const j = JSON.parse(txt);
            const keys = Object.keys(j).map(k => {
                const v = j[k];
                if (Array.isArray(v)) return `${k}[${v.length}]`;
                if (v && typeof v === 'object') return `${k}{${Object.keys(v).slice(0, 4).join(',')}}`;
                return `${k}=${String(v).slice(0, 24)}`;
            });
            return keys.join(' ');
        } catch (e) {
            return txt.slice(0, 160).replace(/\s+/g, ' ');
        }
    }

    function firstItem(txt) {
        try {
            const j = JSON.parse(txt);
            const data = j.result !== undefined ? j.result : (j.data !== undefined ? j.data : j);
            const findArr = (o) => {
                if (Array.isArray(o)) return o;
                if (!o || typeof o !== 'object') return null;
                for (const k of ['items', 'list', 'colList', 'pageList', 'shareFileList', 'records', 'data']) {
                    if (Array.isArray(o[k])) return o[k];
                }
                for (const k of Object.keys(o)) {
                    const r = findArr(o[k]);
                    if (r) return r;
                }
                return null;
            };
            const arr = findArr(data);
            if (!arr || !arr.length) return '(empty)';
            return JSON.stringify(arr[0]).slice(0, 400);
        } catch (e) {
            return '(unparsable)';
        }
    }

    async function probe() {
        const b = base();
        const t = token();
        const navTok = localStorage.getItem('navToken') || '';
        if (!navTok) {
            out('还没有「专栏 token」。\n\n' +
                '请先在页面顶部填 学校代码/用户名/密码，点「专栏登录」，再点这个诊断。\n\n' +
                '原因：专栏接口只认老 host（http://<api-host>）签发的 token；\n' +
                '你正常登录拿的是新 host（<api-host>）token，打专栏接口必然 403/404，\n' +
                '所以这个诊断必须先有专栏 token 才有意义。');
            return;
        }
        if (!t) {
            out('没有 token：请先点「专栏登录」。');
            return;
        }
        const lines = [];
        lines.push(`base   = ${b}`);
        lines.push(`凭据   = 专栏 token（navToken，${navTok.length} 字符）`);
        lines.push(`navApiHost = ${localStorage.getItem('navApiHost') || '(空)'}   colSchool = ${localStorage.getItem('colSchool') || '(空)'}`);
        lines.push(`token  = len ${t.length}, head ${t.slice(0, 12)}…`);
        lines.push('');

        for (const ep of ENDPOINTS) {
            const ctrl = ep.ctrl || 'appWebSite';
            for (const v of ep.variants) {
                const url = `${b}/api/services/app/${ctrl}/${ep.name}?${qs(v)}`;
                const t0 = performance.now();
                try {
                    const opt = {
                        method: ep.method || 'GET',
                        headers: {
                            'Authorization': 'Bearer ' + t,
                            'Accept': 'application/json'
                        }
                    };
                    if (ep.body) {
                        opt.headers['Content-Type'] = 'application/json';
                        opt.body = JSON.stringify(ep.body);
                    }
                    const r = await fetch(url, opt);
                    const txt = await r.text();
                    const ms = Math.round(performance.now() - t0);
                    let code = '?';
                    try { const j = JSON.parse(txt); code = (j.result && j.result.code !== undefined) ? j.result.code : (j.code !== undefined ? j.code : code); } catch (e) { }
                    lines.push(`[${ep.name}] ${v || '(no params)'} -> HTTP ${r.status}, code=${code}, ${txt.length}B, ${ms}ms`);
                    lines.push(`    shape: ${shape(txt)}`);
                    if (r.status === 200) {
                        lines.push(`    item0: ${firstItem(txt)}`);
                        lines.push(`    raw : ${String(txt).slice(0, 700).replace(/\s+/g, ' ')}`);
                    }
                } catch (err) {
                    lines.push(`[${ep.name}] ${v || '(no params)'} -> ERR ${err && err.message}`);
                }
            }
        }

        lines.push('');
        lines.push('把以上整段贴回来即可。');
        out(lines.join('\n'));
    }

    function bind() {
        const btn = document.getElementById('col_diag');
        if (btn && !btn.__bound) {
            btn.__bound = true;
            btn.addEventListener('click', async (e) => {
                e.preventDefault();
                out('探测中…');
                try { await probe(); } catch (err) { out('探测失败: ' + (err && err.message)); }
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }

    window.ColDiag = { probe, base, token };
})();

/* =====================================================================
 * 专栏登录（A 方案）
 *
 * 背景（从官方客户端 v0.0.8 的 app.asar 里读出，并用裸请求验证过）：
 *   - 学校代码 sxz 对应  server  = http://<api-host>
 *                        webServer = http://sxz.school.example.com
 *   - 登录：POST {server}/api/TokenAuth/Login  body {userName, password, clientType:1}
 *           → result.accessToken
 *   - 老专栏页：{webServer}/navPage.html?apiHost={server}&apiToken={accessToken}#/list?messageType=pager
 *   - 该 host 对所有响应都带 Access-Control-Allow-Origin: *（含 Origin: null / 127.0.0.1），
 *     所以从本页直连即可，不需要本地代理。
 *
 * 拿到的老 token 存在 localStorage["navToken"]，只给 navPage 用；
 * 云笔记 / 老师分享仍用你正常登录的新 token，互不影响。
 * 密码只在本次点击时用一次，不写入任何存储。
 * ===================================================================== */
window.ColLogin = (function () {
    var SCHOOL_KEY = 'colSchool';
    var API_KEY = 'navApiHost';
    var WEB_KEY = 'navPageBase';

    var DEFAULTS = {
        sxz: { server: 'http://<api-host>', webServer: 'http://sxz.school.example.com' }
    };

    function $(id) { return document.getElementById(id); }

    function msg(text, isErr) {
        var el = $('col_login_msg');
        if (el) {
            el.textContent = text || '';
            el.className = 'small ' + (isErr ? 'text-danger' : 'text-success');
        }
        if (isErr) console.error('[ColLogin]', text);
        else console.log('[ColLogin]', text);
    }

    function hostOf(code) {
        var t = DEFAULTS[code];
        if (t) return Promise.resolve(t);
        // 未知学校代码：走官方发现接口（客户端同款）
        return fetch('https://hagateway.example.com/api/discovery/' + encodeURIComponent(code))
            .then(function (r) {
                if (!r.ok) throw new Error('学校代码无效（HTTP ' + r.status + '）');
                return r.json();
            })
            .then(function (j) {
                if (!j || !j.server) throw new Error('发现接口没有返回 server');
                return { server: j.server, webServer: j.webServer || '' };
            });
    }

    async function login() {
        var code = String(($('col_school') || {}).value || 'sxz').trim() || 'sxz';
        var user = String(($('col_user') || {}).value || '').trim();
        var pwd = String(($('col_pwd') || {}).value || '');
        if (!user || !pwd) { msg('请先填用户名和密码', true); return; }

        var btn = $('col_login_btn');
        if (btn) btn.disabled = true;
        msg('正在解析学校代码…');
        try {
            var hosts = await hostOf(code);
            localStorage.setItem(SCHOOL_KEY, code);
            localStorage.setItem(API_KEY, hosts.server);
            if (hosts.webServer) localStorage.setItem(WEB_KEY, hosts.webServer.replace(/\/+$/, '') + '/navPage.html');
            window.NAV_HOST = localStorage.getItem(API_KEY);
            window.NAV_PAGE_BASE = localStorage.getItem(WEB_KEY);

            msg('正在向 ' + hosts.server + ' 认证…');
            var res = await fetch(hosts.server + '/api/TokenAuth/Login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ userName: user, password: pwd, clientType: 1 })
            });
            var text = await res.text();
            var json = null;
            try { json = JSON.parse(text); } catch (e) { /* 非 JSON */ }

            if (!json) { msg('登录返回非 JSON（HTTP ' + res.status + '）：' + text.slice(0, 120), true); return; }
            if (json.error) { msg('登录失败：' + (json.error.message || '未知错误'), true); return; }
            var result = json.result || json;
            var token = result && (result.accessToken || result.token);
            if (!token) { msg('登录成功但没有 accessToken：' + text.slice(0, 160), true); return; }

            localStorage.setItem('navToken', token);
            localStorage.setItem('navTokenUser', user);
            localStorage.removeItem('navTokenAt');
            localStorage.setItem('navTokenAt', String(Date.now()));
            msg('已拿到专栏 token（' + token.length + ' 字符），正在重新载入专栏页…');

            var frame = $('zxzl_iframe');
            if (frame && typeof window.navSrc === 'function') frame.src = window.navSrc();
        } catch (e) {
            var m = (e && e.message) || String(e);
            if (/Failed to fetch|NetworkError/i.test(m)) {
                msg('网络被拦（可能是浏览器扩展/CSP）：' + m, true);
            } else {
                msg('出错：' + m, true);
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function logout() {
        localStorage.removeItem('navToken');
        localStorage.removeItem('navTokenUser');
        var frame = $('zxzl_iframe');
        if (frame) frame.src = 'about:blank';
        msg('已清除专栏 token（下次打开仍会回退到新 host token）');
    }

    function status() {
        var t = localStorage.getItem('navToken') || '';
        var at = Number(localStorage.getItem('navTokenAt') || 0);
        return {
            user: localStorage.getItem('navTokenUser') || '',
            tokenLen: t.length,
            at: at ? new Date(at).toLocaleString() : '',
            apiHost: localStorage.getItem(API_KEY) || window.NAV_HOST,
            webHost: localStorage.getItem(WEB_KEY) || window.NAV_PAGE_BASE,
            src: typeof window.navSrc === 'function' ? window.navSrc() : ''
        };
    }

    return { login: login, logout: logout, status: status };
})();

