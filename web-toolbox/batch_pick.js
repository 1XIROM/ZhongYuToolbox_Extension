/* ============================================================================
 * batch_pick.js —— 中育 ToolBox「批量选择 / 全选 / 一键下载」增强
 * ----------------------------------------------------------------------------
 * 覆盖范围：
 *   1) 图库（#pictureTable）与 图库回收站（#recycleTable）
 *   2) 云笔记：全部笔记（#noteList2）与 文件夹视图（#noteList）
 *   3) 在线专栏 / 课程章节（#cid_card 章节树）里的附件（视频/PPT/PDF/图片/任意对象）
 *
 * 设计原则：
 *   - 不修改 index.js 内部逻辑，只在其渲染完成后对 DOM 做装饰；
 *   - 数据尽量从 DOM 反推（图库的下载链接、笔记的 onclick），拿不到时用全局变量兜底；
 *   - 所有外部依赖（proxyUrl / aesEncrypt / aesDecrypt / JSZip / jQuery）都做存在性判断；
 *   - 单个文件失败不影响整批，最后汇总失败清单。
 * ==========================================================================*/

(function () {
  'use strict';

  if (window.__BP_LOADED__) return;
  window.__BP_LOADED__ = true;

  // 内部命名空间。此前漏了这行声明，导致 IIFE 走到 BP.refreshCounts= 时抛
  // ReferenceError: BP is not defined，init() 永远不执行 → 页面上没有任何多选 UI。
  const BP = {};
  const PENDING = [];   // 初始化时尚不存在的容器，后续补挂观察者

  // ============================ 全局状态 ============================
  const S = {
    gallery: { normal: new Map(), recycle: new Map() }, // url -> {name}
    notes: new Map(),                                    // fileId -> {name}
    chapters: new Map(),                                 // catalogId -> {title}
    busy: false
  };

  // ============================ 小工具 ============================
  const $id = (id) => document.getElementById(id);

  function safeName(s, max) {
    let n = String(s == null ? '' : s)
      .replace(/[\\/:*?"<>|\r\n\t]/g, '_')
      .replace(/[\u0000-\u001f]/g, '')
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/^\.+/, '');
    if (!n) n = 'unnamed';
    const limit = max || 120;
    return n.length > limit ? n.slice(0, limit) : n;
  }

  function stamp() {
    const d = new Date();
    const p = (x) => String(x).padStart(2, '0');
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }

  function extOf(url) {
    const m = String(url || '').split('?')[0].match(/\.([A-Za-z0-9]{1,8})$/);
    return m ? '.' + m[1].toLowerCase() : '';
  }

  /** OSS 相对路径补全为完整 URL */
  function absUrl(u) {
    if (!u) return '';
    u = String(u).trim();
    if (/^https?:\/\//i.test(u)) return u;
    if (/^\/\//.test(u)) return 'https:' + u;
    return 'http://friday-note.oss-cn-hangzhou.aliyuncs.com/' + u.replace(/^\/+/, '');
  }

  /** 走本地代理（index.js 里的 proxyUrl），失败则用原地址 */
  function viaProxy(u) {
    const full = absUrl(u);
    try {
      if (typeof window.proxyUrl === 'function') return window.proxyUrl(full);
    } catch (e) { /* ignore */ }
    return full;
  }

  /**
   * 从 <a href> 里还原出「真实文件地址」。
   * 兼容三种写法：
   *   1) 旧中转  https://zytbdownloadagent.../download/<编码后的URL>
   *   2) 新代理  http://127.0.0.1:5005/proxy/<URL>
   *   3) 纯直链  http://<oss-bucket>.oss-cn-.../xxx
   * 中转失效那次事故就是因为只认第 1 种，href 变成直链后解析出空串，
   * 于是把「本地代理首页」当成图片存了下来。
   */
  function absFromHref(raw) {
    if (!raw || typeof raw !== 'string') return '';
    const pick = (marker) => {
      const i = raw.indexOf(marker);
      if (i < 0) return '';
      let rest = raw.slice(i + marker.length);
      try { rest = decodeURIComponent(rest); } catch (e) { /* 保持原样 */ }
      return /^https?:\/\//i.test(rest) ? rest : '';
    };
    const fromDownload = pick('/download/');
    if (fromDownload) return fromDownload;
    const fromProxy = pick('/proxy/');
    if (fromProxy) return fromProxy;
    if (/^https?:\/\//i.test(raw)) return raw;
    return '';
  }

  function token() {
    try { return localStorage.getItem('token') || ''; } catch (e) { return ''; }
  }

  function zipLib() {
    return window.JSZip || window.jszip || null;
  }

  /** index.js 里 aesEncrypt / aesDecrypt 是条件挂到 window 上的，这里做显式校验 */
  function aesFns() {
    const enc = window.aesEncrypt, dec = window.aesDecrypt;
    if (typeof enc !== 'function' || typeof dec !== 'function') {
      throw new Error('加密工具未就绪（window.aesEncrypt / aesDecrypt 未定义）');
    }
    return { enc: enc, dec: dec };
  }

  // ============================ 进度面板 ============================
  const Prog = {
    el: null,
    ensure() {
      if (this.el && document.body.contains(this.el)) return this.el;
      const d = document.createElement('div');
      d.id = 'bp-progress';
      d.style.cssText = [
        'position:fixed', 'right:18px', 'bottom:18px', 'z-index:20000',
        'min-width:280px', 'max-width:420px', 'background:#fff',
        'border:1px solid #dee2e6', 'border-radius:10px',
        'box-shadow:0 6px 24px rgba(0,0,0,.18)', 'padding:12px 14px',
        'font-size:13px', 'display:none'
      ].join(';');
      d.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
          <strong id="bp-progress-title">批量下载</strong>
          <span id="bp-progress-count" class="text-muted"></span>
        </div>
        <div class="progress mt-2" style="height:8px;">
          <div id="bp-progress-bar" class="progress-bar progress-bar-striped progress-bar-animated"
               role="progressbar" style="width:0%"></div>
        </div>
        <div id="bp-progress-msg" class="text-muted mt-2"
             style="max-height:72px;overflow:auto;word-break:break-all;"></div>`;
      document.body.appendChild(d);
      this.el = d;
      return d;
    },
    show(title) {
      const d = this.ensure();
      d.style.display = 'block';
      const t = $id('bp-progress-title'); if (t) t.textContent = title || '批量下载';
      this.set(0, '', '');
    },
    set(percent, countText, msg) {
      const bar = $id('bp-progress-bar');
      if (bar) bar.style.width = Math.max(0, Math.min(100, percent | 0)) + '%';
      const c = $id('bp-progress-count');
      if (c && countText != null) c.textContent = countText;
      const m = $id('bp-progress-msg');
      if (m && msg != null) m.innerHTML = msg;
    },
    append(msg) {
      const m = $id('bp-progress-msg');
      if (m) m.innerHTML = (m.innerHTML ? m.innerHTML + '<br>' : '') + msg;
    },
    done(msg) {
      this.set(100, '完成', msg || '');
      const bar = $id('bp-progress-bar');
      if (bar) bar.classList.remove('progress-bar-animated');
      setTimeout(() => { if (this.el) this.el.style.display = 'none'; }, 6000);
    },
    hide() {
      if (this.el) this.el.style.display = 'none';
    }
  };

  // ============================ 下载基础 ============================
  /**
   * 取二进制。opts.image=true 时要求响应确实是图片：
   * 公网中转失效时会返回 200 + text/html，不校验就会把 HTML 当 jpg 存下来。
   * 直链失败（多为 CORS）时自动退到代理再试一次。
   */
  async function fetchBlob(url, opts) {
    const wantImage = !!(opts && opts.image);
    const tryOnce = async (u) => {
      const resp = await fetch(u, { credentials: 'omit' });
      const ct = (resp.headers.get('content-type') || '').toLowerCase();
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      if (wantImage && !ct.startsWith('image/')) {
        throw new Error('返回 ' + (ct || '未知类型') + '，不是图片（中转服务可能已停用）');
      }
      return await resp.blob();
    };
    try {
      return await tryOnce(url);
    } catch (e) {
      let alt = null;
      try {
        if (typeof window.proxyUrl === 'function') alt = window.proxyUrl(url);
      } catch (err) { /* ignore */ }
      if (alt && alt !== url) return await tryOnce(alt);
      throw e;
    }
  }

  /** 限并发执行；worker(item, index) */
  async function pool(items, limit, worker, onTick) {
    const results = new Array(items.length);
    let next = 0, done = 0;
    const n = Math.max(1, limit | 0 || 3);

    async function runner() {
      while (true) {
        const i = next++;
        if (i >= items.length) return;
        try {
          results[i] = { ok: true, value: await worker(items[i], i) };
        } catch (e) {
          results[i] = { ok: false, error: (e && e.message) || String(e), item: items[i] };
        }
        done++;
        if (onTick) { try { onTick(done, items.length, results[i]); } catch (e) {} }
      }
    }

    const runners = [];
    for (let k = 0; k < Math.min(n, items.length); k++) runners.push(runner());
    await Promise.all(runners);
    return results;
  }

  function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      try { document.body.removeChild(a); } catch (e) {}
      try { URL.revokeObjectURL(url); } catch (e) {}
    }, 2000);
  }

  async function saveZip(zip, filename) {
    const content = await zip.generateAsync(
      { type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 6 } },
      (meta) => { Prog.set(meta.percent | 0, '打包中', ''); }
    );
    saveBlob(content, filename);
  }

  // ============================ UI 构件 ============================
  function makeToolbar(id, label) {
    const bar = document.createElement('div');
    bar.id = id;
    bar.className = 'bp-toolbar d-flex flex-wrap align-items-center gap-2 my-2';
    bar.style.cssText = 'padding:6px 8px;background:#f8f9fa;border:1px solid #e9ecef;border-radius:8px;';
    bar.innerHTML = `
      <label class="form-check-label mb-0 me-1" style="font-size:13px;cursor:pointer;">
        <input type="checkbox" class="bp-all" style="cursor:pointer;"> 全选本页
      </label>
      <button type="button" class="btn btn-sm btn-outline-secondary bp-all-pages" title="遍历所有分页后全选">全选全部</button>
      <button type="button" class="btn btn-sm btn-outline-secondary bp-clear">清空</button>
      <span class="badge bg-secondary bp-count">已选 0</span>
      <button type="button" class="btn btn-sm btn-primary bp-download">一键下载</button>`;
    return bar;
  }

  function updateCount(bar, n) {
    const c = bar && bar.querySelector('.bp-count');
    if (!c) return;
    // 必须比较后再写：textContent 赋值即使是相同字符串也会替换文本节点，
    // 从而产生 childList 变动，若工具条在观察范围内就会形成死循环。
    const txt = '已选 ' + n;
    if (c.textContent !== txt) c.textContent = txt;
  }

  function attachCheckbox(container, cb) {
    const td = document.createElement('td');
    td.style.cssText = 'width:36px;vertical-align:middle;';
    td.appendChild(cb);
    container.insertBefore(td, container.firstChild);
  }

  // ============================ ① 图库 / 回收站 ============================
  const GALLERY = {
    maxCount: 100,

    api(isRecycle, skip) {
      const url = `${window.API_BASE_URL}/api/services/app/PictureLibrary/GetAllPicturesFromLibrary` +
        `?SkipCount=${skip}&MaxResultCount=${GALLERY.maxCount}&IsRecycleBin=${isRecycle}`;
      return fetch(url, { headers: { 'Authorization': 'Bearer ' + token() } })
        .then(r => r.json());
    },

    async fetchAll(isRecycle, onTick) {
      const out = [];
      let skip = 0, total = Infinity;
      while (skip < total) {
        const data = await GALLERY.api(isRecycle, skip);
        const result = (data && data.result) || {};
        const items = result.items || [];
        total = result.totalCount || 0;
        if (!items.length) break;
        for (const it of items) out.push(it);
        skip += items.length;
        if (onTick) onTick(out.length, total);
        if (skip === 0) break;
      }
      return out;
    },

    /** 从已渲染表格里反推 {url,name}；拿不到 name 就用 url 末段 */
    readRows(table) {
      const rows = [];
      const trs = table.tBodies && table.tBodies[0] ? table.tBodies[0].rows : [];
      for (const tr of trs) {
        const a = tr.querySelector('a[href]');
        let url = a ? absFromHref(a.getAttribute('href') || '') : '';
        if (!url) {
          const img = tr.querySelector('img[src]');
          if (img) url = absFromHref(img.getAttribute('src') || '');
        }
        // 名称列：跳过含 input/img 的单元格（装饰前后布局不同，用内容判断更稳）
        let name = '';
        for (const td of tr.cells) {
          if (td.querySelector('input, img')) continue;
          const t = (td.textContent || '').trim();
          if (t) { name = t; break; }
        }
        rows.push({ url: url, name: name });
      }
      return rows;
    },

    decorate(kind) {
      const box = $id(kind === 'recycle' ? 'recycleTable' : 'pictureTable');
      if (!box) return;
      const table = box.querySelector('table');
      if (!table) return;

      const store = S.gallery[kind];
      const rows = GALLERY.readRows(table);

      // 表头
      const headRow = table.tHead && table.tHead.rows[0];
      if (headRow && !headRow.querySelector('th.bp-th')) {
        const th = document.createElement('th');
        th.className = 'bp-th';
        th.style.width = '36px';
        th.textContent = '选';
        headRow.insertBefore(th, headRow.firstChild);
      }

      // 逐行
      const trs = table.tBodies[0] ? Array.from(table.tBodies[0].rows) : [];
      trs.forEach((tr, i) => {
        if (tr.querySelector('input.bp-cb')) return;
        const info = rows[i] || {};
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'bp-cb';
        cb.style.cursor = 'pointer';
        cb.dataset.url = info.url || '';
        cb.dataset.name = info.name || '';
        cb.checked = !!store.has(info.url);
        cb.addEventListener('change', () => {
          if (cb.checked) store.set(cb.dataset.url, { name: cb.dataset.name });
          else store.delete(cb.dataset.url);
          BP.refreshCounts();
        });
        attachCheckbox(tr, cb);
      });

      GALLERY.ensureToolbar(kind, box);
      BP.refreshCounts();
    },

    ensureToolbar(kind, box) {
      const barId = 'bp-bar-' + kind;
      let bar = $id(barId);
      if (!bar) {
        bar = makeToolbar(barId);
        // 放在被观察容器之外：工具条内部（按钮文字、徽标）的变动不会再触发观察者
        box.parentNode.insertBefore(bar, box);

        bar.querySelector('.bp-all').addEventListener('change', function () {
          const on = this.checked;
          box.querySelectorAll('input.bp-cb').forEach(cb => {
            cb.checked = on;
            if (on) S.gallery[kind].set(cb.dataset.url, { name: cb.dataset.name });
            else S.gallery[kind].delete(cb.dataset.url);
          });
          BP.refreshCounts();
        });

        bar.querySelector('.bp-clear').addEventListener('click', () => {
          S.gallery[kind].clear();
          box.querySelectorAll('input.bp-cb').forEach(cb => (cb.checked = false));
          const all = bar.querySelector('.bp-all'); if (all) all.checked = false;
          BP.refreshCounts();
        });

        bar.querySelector('.bp-all-pages').addEventListener('click', async () => {
          const btn = bar.querySelector('.bp-all-pages');
          btn.disabled = true; btn.textContent = '遍历中…';
          try {
            const list = await GALLERY.fetchAll(kind === 'recycle');
            S.gallery[kind].clear();
            list.forEach(it => {
              if (it && it.picture) S.gallery[kind].set(it.picture, { name: it.name || '' });
            });
            box.querySelectorAll('input.bp-cb').forEach(cb => {
              cb.checked = S.gallery[kind].has(cb.dataset.url);
            });
            BP.refreshCounts();
            Prog.done('已全选 ' + S.gallery[kind].size + ' 张（跨页）');
          } catch (e) {
            alert('遍历分页失败：' + ((e && e.message) || e));
          } finally {
            btn.disabled = false; btn.textContent = '全选全部';
          }
        });

        bar.querySelector('.bp-download').addEventListener('click', () => {
          GALLERY.download(kind);
        });
      }
      return bar;
    },

    async download(kind) {
      if (S.busy) { alert('已有批量任务在进行'); return; }
      const store = S.gallery[kind];
      if (!store.size) { alert('请先勾选图片'); return; }

      const items = Array.from(store.entries()).map(([url, v]) => ({ url: url, name: v.name }));
      S.busy = true;
      const zip = new (zipLib())();
      const used = new Map();
      const failed = [];

      try {
        Prog.show(kind === 'recycle' ? '批量下载 · 回收站' : '批量下载 · 图库');
        await pool(items, 3, async (it) => {
          if (!it.url) throw new Error('没从页面里解析到图片地址');
          // 直链优先，失败自动退到本地代理；并要求响应确实是图片
          const blob = await fetchBlob(it.url, { image: true });
          let base = safeName(it.name || ('image_' + (it.url || '').split('/').pop()));
          let ext = extOf(it.url) || extOf(base) || '.jpg';
          if (ext && ext.toLowerCase() === '.bin') ext = '.jpg';
          if (ext && base.toLowerCase().endsWith(ext)) base = base.slice(0, -ext.length);
          let name = base + ext;
          const c = (used.get(name) || 0) + 1;
          used.set(name, c);
          if (c > 1) name = base + '_' + c + ext;
          zip.file(name, blob);
        }, (done, total, r) => {
          Prog.set(Math.round(done / total * 100), `${done}/${total}`,
            r && r.ok === false ? '失败：' + safeName(String(r.item && r.item.name || '')) : '');
          if (r && r.ok === false) failed.push(r);
        });

        if (!failed.length && !Object.keys(zip.files).length) {
          alert('没有可下载的内容');
          return;
        }
        const label = kind === 'recycle' ? '图库回收站' : '图库';
        await saveZip(zip, `${label}_${items.length - failed.length}项_${stamp()}.zip`);
        Prog.done(failed.length
          ? `完成，${failed.length} 项失败`
          : `已打包 ${items.length} 项`);
      } catch (e) {
        alert('批量下载失败：' + ((e && e.message) || e));
      } finally {
        S.busy = false;
      }
    }
  };

  // ============================ ② 云笔记 ============================
  const NOTES = {
    /** 复用 noteDownload2 的接口形态：/special/GetByFileId?<密文> */
    /**
     * 取单篇笔记的资源列表。
     * 端点有多套（与 index.js 的 getCloudNoteApiPathR / noteDownload2 保持一致，
     * 省锡中走 /special/ 代理，其它学校走 CloudNotes 原生路径），逐个尝试；
     * 失败时把 HTTP 状态和响应片段带进错误信息，便于定位。
     */
    async resources(fileId) {
      const fns = aesFns();
      const enc = fns.enc('fileId=' + fileId);
      const base = window.API_BASE_URL;

      const candidates = [];
      try {
        if (typeof window.getCloudNoteApiPathR === 'function') {
          candidates.push(window.getCloudNoteApiPathR('GetByFileId', enc));
        }
      } catch (e) { /* ignore */ }
      candidates.push(`${base}/special/GetByFileId?${enc}`);
      candidates.push(`${base}/CloudNotes/api/Resources/GetByFileId?${enc}`);
      candidates.push(`${base}/CloudNotes/api/Notes/GetByFileId?${enc}`);
      // aesEncrypt 返回的是 base64，可能含 '+'（在 query 里会被解析成空格）。
      // 原实现是裸拼，这里补一组编码后的候选，服务端若因此解密失败可自动兜住。
      if (encodeURIComponent(enc) !== enc) {
        const e2 = encodeURIComponent(enc);
        candidates.push(`${base}/special/GetByFileId?${e2}`);
        candidates.push(`${base}/CloudNotes/api/Resources/GetByFileId?${e2}`);
      }

      const tried = {};
      let lastErr = null;

      for (const url of candidates) {
        if (!url || tried[url]) continue;
        tried[url] = 1;

        let resp;
        try {
          resp = await fetch(url, {
            method: 'GET',
            headers: { 'Authorization': 'Bearer ' + token() }
          });
        } catch (e) {
          lastErr = new Error('网络错误: ' + ((e && e.message) || e));
          continue;
        }

        if (resp.status === 401) throw new Error('登录已失效（401）');

        const text = await resp.text();
        if (!text) { lastErr = new Error('HTTP ' + resp.status + '：响应为空'); continue; }

        let obj = null;
        try { obj = JSON.parse(text); }
        catch (e) { lastErr = new Error('HTTP ' + resp.status + '：非 JSON（' + text.slice(0, 100) + '）'); continue; }

        if (obj && obj.code != null && obj.code !== 0) {
          lastErr = new Error('接口返回 code=' + obj.code + ' ' + (obj.msg || ''));
          continue;
        }
        if (!obj || obj.data == null) {
          lastErr = new Error('HTTP ' + resp.status + '：无 data 字段（' + text.slice(0, 100) + '）');
          continue;
        }

        let plain;
        try { plain = fns.dec(obj.data); }
        catch (e) { lastErr = new Error('解密失败'); continue; }

        let parsed = null;
        try { parsed = JSON.parse(plain); }
        catch (e) { lastErr = new Error('解密后非 JSON（' + String(plain).slice(0, 100) + '）'); continue; }

        if (parsed && parsed.resourceList) return parsed.resourceList;
        lastErr = new Error('响应缺少 resourceList');
      }

      throw lastErr || new Error('无法获取笔记资源');
    },

    /** 从列表项 onclick 里抠出 fileId 与名称 */
    parseNoteRow(a) {
      const onclick = a.getAttribute('onclick') || '';
      const m = onclick.match(/noteDownload\('([^']+)'\s*,\s*'([^']*)'\)/);
      if (m) return { fileId: m[1], name: m[2] };
      return null;
    },

    decorateNoteList2() {
      const box = $id('noteList2');
      if (!box) return;
      NOTES.ensureToolbar('bp-bar-notes2', box, 'noteList2');

      Array.from(box.querySelectorAll('a.a-note')).forEach(a => {
        if (a.querySelector('input.bp-cb')) return;
        const info = NOTES.parseNoteRow(a);
        if (!info) return;
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'bp-cb';
        cb.style.cssText = 'cursor:pointer;margin-right:8px;flex:0 0 auto;';
        cb.dataset.fileId = info.fileId;
        cb.dataset.name = info.name;
        cb.checked = S.notes.has(info.fileId);
        cb.addEventListener('click', (e) => e.stopPropagation());
        cb.addEventListener('change', () => {
          if (cb.checked) S.notes.set(info.fileId, { name: info.name });
          else S.notes.delete(info.fileId);
          BP.refreshCounts();
        });
        const row = a.querySelector('.d-flex');
        if (row) row.insertBefore(cb, row.firstChild);
        else a.insertBefore(cb, a.firstChild);
      });
      BP.refreshCounts();
    },

    decorateNoteList() {
      const box = $id('noteList');
      if (!box) return;
      const stash = window.__bpLastNotes || [];
      NOTES.ensureToolbar('bp-bar-notes1', box, 'noteList');

      const rows = Array.from(box.querySelectorAll('a.list-group-item'));
      let idx = -1;
      rows.forEach(row => {
        idx++;
        const note = stash[idx];
        if (!note || note.type === 0) return;           // 文件夹不参与
        if (row.querySelector('input.bp-cb')) return;
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'bp-cb';
        cb.style.cssText = 'cursor:pointer;margin-right:8px;';
        cb.dataset.fileId = note.fileId;
        cb.dataset.name = note.fileName || '';
        cb.checked = S.notes.has(note.fileId);
        cb.addEventListener('click', (e) => e.stopPropagation());
        cb.addEventListener('change', () => {
          if (cb.checked) S.notes.set(note.fileId, { name: note.fileName || '' });
          else S.notes.delete(note.fileId);
          BP.refreshCounts();
        });
        row.insertBefore(cb, row.firstChild);
      });
      BP.refreshCounts();
    },

    ensureToolbar(id, box, scope) {
      let bar = $id(id);
      if (bar) return bar;
      bar = makeToolbar(id);
      box.parentNode.insertBefore(bar, box);

      bar.querySelector('.bp-all').addEventListener('change', function () {
        const on = this.checked;
        box.querySelectorAll('input.bp-cb').forEach(cb => {
          cb.checked = on;
          if (on) S.notes.set(cb.dataset.fileId, { name: cb.dataset.name });
          else S.notes.delete(cb.dataset.fileId);
        });
        BP.refreshCounts();
      });

      bar.querySelector('.bp-clear').addEventListener('click', () => {
        S.notes.clear();
        document.querySelectorAll('#noteList2 input.bp-cb, #noteList input.bp-cb')
          .forEach(cb => (cb.checked = false));
        document.querySelectorAll('.bp-bar .bp-all, #bp-bar-notes1 .bp-all, #bp-bar-notes2 .bp-all')
          .forEach(x => (x.checked = false));
        BP.refreshCounts();
      });

      // 全选全部：把所有已加载笔记（type 1/12）一次性纳入
      bar.querySelector('.bp-all-pages').addEventListener('click', () => {
        const all = (window.allNotes && window.allNotes.length)
          ? window.allNotes
          : (window.__bpLastNotes || []);
        let n = 0;
        all.forEach(x => {
          if (!x || x.type === 0) return;
          S.notes.set(x.fileId, { name: x.fileName || '' });
          n++;
        });
        if (!n) { alert('列表尚未加载完成'); return; }
        document.querySelectorAll('#noteList2 input.bp-cb, #noteList input.bp-cb').forEach(cb => {
          cb.checked = S.notes.has(cb.dataset.fileId);
        });
        BP.refreshCounts();
        Prog.done('已全选 ' + S.notes.size + ' 篇笔记');
      });

      bar.querySelector('.bp-download').addEventListener('click', () => NOTES.download());
      return bar;
    },

    async download() {
      if (S.busy) { alert('已有批量任务在进行'); return; }
      if (!S.notes.size) { alert('请先勾选笔记'); return; }

      const list = Array.from(S.notes.entries()).map(([fileId, v]) => ({ fileId: fileId, name: v.name }));
      S.busy = true;
      const zip = new (zipLib())();
      const failed = [];
      let totalFiles = 0;

      try {
        Prog.show(`批量下载 · 云笔记（${list.length} 篇）`);
        let doneNotes = 0;

        for (const note of list) {
          doneNotes++;
          Prog.set(Math.round((doneNotes - 1) / list.length * 100), `${doneNotes}/${list.length}`,
            '正在解析：' + safeName(note.name));
          let res;
          try {
            res = await NOTES.resources(note.fileId);
          } catch (e) {
            failed.push({ name: note.name, error: (e && e.message) || String(e) });
            continue;
          }
          const folder = zip.folder(safeName(note.name) || ('note_' + note.fileId));
          let seq = 0;
          const perPage = new Map();

          for (const r of res) {
            seq++;
            const page = (r.pageIndex == null ? 0 : r.pageIndex) + 1;
            const isThumb = r.resourceType === 2;
            // 直链优先；fetchBlob 内部在失败时才退到代理
            const src = absUrl(r.ossImageUrl);
            const ext = extOf(r.ossImageUrl) || '.bin';
            const n = (perPage.get(page) || 0) + 1;
            perPage.set(page, n);
            const fname = `${String(page).padStart(3, '0')}-${n}${isThumb ? '-thumb' : ''}${ext}`;
            try {
              const blob = await fetchBlob(src, { image: true });
              folder.file(fname, blob);
              totalFiles++;
            } catch (e) {
              failed.push({ name: note.name + '/' + fname, error: (e && e.message) || String(e) });
            }
            Prog.set(Math.round(((doneNotes - 1) + seq / Math.max(1, res.length)) / list.length * 100),
              `${doneNotes}/${list.length}`, '正在下载：' + safeName(note.name));
          }
        }

        await saveZip(zip, `云笔记_${list.length}篇_${totalFiles}个文件_${stamp()}.zip`);
        Prog.done(failed.length
          ? `完成，${failed.length} 项失败：<br>` + failed.slice(0, 8).map(f => safeName(f.name) + ' — ' + f.error).join('<br>')
          : `已打包 ${list.length} 篇 / ${totalFiles} 个文件`);
      } catch (e) {
        alert('批量下载失败：' + ((e && e.message) || e));
      } finally {
        S.busy = false;
      }
    }
  };

  // ============================ ③ 在线专栏 / 课程章节附件 ============================
  const CHAPTERS = {
    /** 从 CourseDetail 的响应里抓章节树，用于把 DOM 上的叶子节点映射回 catalogId */
    captureCourseDetail() {
      if (window.__bpCatalogHooked) return;
      window.__bpCatalogHooked = true;
      const stash = (json) => {
        try {
          const catalogs = json && json.data && json.data.catalogs;
          if (Array.isArray(catalogs)) {
            window.__bpCatalogs = catalogs;
            setTimeout(() => { try { CHAPTERS.decorate(); } catch (e) {} }, 0);
          }
        } catch (e) {}
      };
      if (window.jQuery) {
        jQuery(document).on('ajaxSuccess', function (ev, xhr, settings) {
          const u = (settings && (settings.url || '')) || '';
          if (u.indexOf('CourseDetail') < 0) return;
          let json = xhr && (xhr.responseJSON || null);
          if (!json && xhr && xhr.responseText) { try { json = JSON.parse(xhr.responseText); } catch (e) {} }
          stash(json);
        });
      }
    },

    /** 按渲染顺序把叶子章节拍平：[{id,title}] */
    flattenLeaves: function (nodes, out) {
      out = out || [];
      if (!Array.isArray(nodes)) return out;
      for (const c of nodes) {
        if (!c) continue;
        if (c.isLeaf) out.push({ id: c.id, title: c.title || '' });
        else if (c.children && c.children.length) CHAPTERS.flattenLeaves(c.children, out);
      }
      return out;
    },

    /** 给 DOM 上的叶子节点回填 catalogId（顺序对齐，标题兜底） */
    assignIds() {
      const box = $id('cid_card');
      if (!box) return 0;
      const leaves = CHAPTERS.flattenLeaves(window.__bpCatalogs || []);
      const els = Array.from(box.querySelectorAll('.chapter-item')).filter(el => !el.classList.contains('folder'));
      if (!els.length) return 0;
      let assigned = 0;
      els.forEach((el, i) => {
        const t = el.querySelector('.title');
        const title = t ? (t.textContent || '').replace(/^[●\s]+/, '').trim() : '';
        let id = el.__bpCatalogId;
        if (!id && leaves[i] && (!title || !leaves[i].title || leaves[i].title === title)) {
          id = leaves[i].id;
        }
        if (!id && title) {
          const hit = leaves.find(x => (x.title || '') === title);
          if (hit) id = hit.id;
        }
        if (!id) return;
        el.__bpCatalogId = id;
        el.__bpTitle = title;
        const cb = el.querySelector('input.bp-cb');
        if (cb) {
          cb.dataset.catalogId = id;
          cb.dataset.title = title;
          if (cb.checked) S.chapters.set(id, { title: title });
          assigned++;
        }
      });
      return assigned;
    },

    courseId() {
      const el = $id('id_input');
      if (el && el.value) return el.value;
      try { return (window._currentShare && window._currentShare.id) || ''; } catch (e) { return ''; }
    },

    async readContent(courseId, catalogId) {
      const url = `${window.API_BASE_URL}/SelfStudy/api/learn/readContent?catalogId=${encodeURIComponent(catalogId)}&courseId=${encodeURIComponent(courseId)}`;
      const resp = await fetch(url, {
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + token()
        }
      });
      if (resp.status === 401) throw new Error('登录已失效');
      const j = await resp.json();
      return (j && j.data && j.data.content) || '';
    },

    /** 从章节 HTML 里抽附件（与 change_all/change_object/change_video/change_div 的口径一致） */
    extractFiles(html) {
      const out = [];
      if (!html) return out;
      const div = document.createElement('div');
      div.innerHTML = html;

      div.querySelectorAll('object').forEach(o => {
        const u = o.getAttribute('data') || o.getAttribute('src');
        if (u) out.push({ url: absUrl(u), name: o.getAttribute('name') || '' });
      });

      div.querySelectorAll('video').forEach(v => {
        const u = v.getAttribute('src');
        if (u) out.push({ url: absUrl(u), name: v.getAttribute('name') || '' });
      });

      div.querySelectorAll('div[data-type]').forEach(d => {
        const t = (d.getAttribute('data-type') || '').toLowerCase();
        if (t === 'ppt' || t === 'pdf') {
          const u = d.getAttribute('data-url');
          if (u) out.push({ url: absUrl(u), name: d.getAttribute('data-name') || '' });
        } else if (t === 'image-block') {
          const img = d.querySelector('img');
          if (img && img.getAttribute('src')) {
            out.push({ url: absUrl(img.getAttribute('src')), name: img.getAttribute('alt') || '' });
          }
        }
      });

      // 去重
      const seen = new Set();
      return out.filter(f => {
        if (!f.url || seen.has(f.url)) return false;
        seen.add(f.url);
        return true;
      });
    },

    collectLeaves() {
      const box = $id('cid_card');
      if (!box) return [];
      const out = [];
      box.querySelectorAll('.chapter-item').forEach((el, i) => {
        if (el.classList.contains('folder')) return;
        const titleEl = el.querySelector('.title');
        const title = titleEl ? (titleEl.textContent || '').replace(/^[●\s]+/, '').trim() : ('章节' + (i + 1));
        el.dataset.bpIdx = String(i);
        out.push({ el: el, title: title });
      });
      return out;
    },

    decorate() {
      const box = $id('cid_card');
      if (!box) return;
      let bar = $id('bp-bar-chapters');
      if (!bar) {
        bar = makeToolbar('bp-bar-chapters');
        box.parentNode.insertBefore(bar, box);

        bar.querySelector('.bp-all').addEventListener('change', function () {
          const on = this.checked;
          box.querySelectorAll('.chapter-item:not(.folder)').forEach(el => {
            const cb = el.querySelector('input.bp-cb');
            if (!cb) return;
            if (!cb.dataset.catalogId) { cb.checked = false; return; }
            cb.checked = on;
            if (cb.checked) S.chapters.set(cb.dataset.catalogId, { title: cb.dataset.title });
            else S.chapters.delete(cb.dataset.catalogId);
          });
          BP.refreshCounts();
        });

        bar.querySelector('.bp-clear').addEventListener('click', () => {
          S.chapters.clear();
          box.querySelectorAll('input.bp-cb').forEach(cb => (cb.checked = false));
          const all = bar.querySelector('.bp-all'); if (all) all.checked = false;
          BP.refreshCounts();
        });

        bar.querySelector('.bp-all-pages').addEventListener('click', () => {
          let n = 0;
          box.querySelectorAll('.chapter-item:not(.folder)').forEach(el => {
            const cb = el.querySelector('input.bp-cb');
            if (!cb || !cb.dataset.catalogId) return;
            cb.checked = true;
            S.chapters.set(cb.dataset.catalogId, { title: cb.dataset.title });
            n++;
          });
          if (!n) { alert('章节尚未加载完成，或未能取得章节 ID（先点开一次该课程）'); return; }
          BP.refreshCounts();
          Prog.done('已全选 ' + n + ' 个章节');
        });

        bar.querySelector('.bp-download').addEventListener('click', () => CHAPTERS.download());
      }

      // 叶子章节注入 checkbox（目录节点是从 t.id 取到的，需要事件里拦截）
      box.querySelectorAll('.chapter-item').forEach((el, i) => {
        if (el.classList.contains('folder')) return;
        if (el.querySelector('input.bp-cb')) return;

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'bp-cb';
        cb.style.cssText = 'cursor:pointer;margin-right:6px;';
        cb.dataset.bpIdx = String(i);
        cb.addEventListener('click', (e) => e.stopPropagation());
        cb.addEventListener('change', () => {
          const id = el.__bpCatalogId;
          const title = el.__bpTitle || '';
          if (!id) { cb.checked = false; alert('未能取得该章节 ID，请先点击一次该章节后再试'); return; }
          if (cb.checked) S.chapters.set(id, { title: title });
          else S.chapters.delete(id);
          BP.refreshCounts();
        });
        el.insertBefore(cb, el.firstChild);
      });

      CHAPTERS.assignIds();
      BP.refreshCounts();
    },

    async download() {
      if (S.busy) { alert('已有批量任务在进行'); return; }
      if (!S.chapters.size) { alert('请先勾选章节'); return; }
      const courseId = CHAPTERS.courseId();
      if (!courseId) { alert('未取得 courseId，请先在课程列表里选择一门课'); return; }

      const list = Array.from(S.chapters.entries()).map(([id, v]) => ({ id: id, title: v.title }));
      S.busy = true;
      const zip = new (zipLib())();
      const failed = [];
      let total = 0;

      try {
        Prog.show(`批量下载 · 章节附件（${list.length} 章）`);
        let i = 0;
        for (const ch of list) {
          i++;
          Prog.set(Math.round((i - 1) / list.length * 100), `${i}/${list.length}`, '读取章节：' + safeName(ch.title));
          let html = '';
          try {
            html = await CHAPTERS.readContent(courseId, ch.id);
          } catch (e) {
            failed.push({ name: ch.title, error: (e && e.message) || String(e) });
            continue;
          }
          const files = CHAPTERS.extractFiles(html);
          if (!files.length) continue;

          const folder = zip.folder(safeName(ch.title) || ('chapter_' + ch.id));
          for (let k = 0; k < files.length; k++) {
            const f = files[k];
            const ext = extOf(f.url) || '.bin';
            const base = safeName(f.name || ('file_' + (k + 1)));
            const hasExt = extOf(base) === ext;
            const fname = hasExt ? base : (base + ext);
            try {
              const blob = await fetchBlob(viaProxy(f.url));
              folder.file(fname, blob);
              total++;
            } catch (e) {
              failed.push({ name: ch.title + '/' + fname, error: (e && e.message) || String(e) });
            }
          }
        }

        if (!total) {
          alert('所选章节没有可下载的附件' + (failed.length ? `（${failed.length} 项失败）` : ''));
          return;
        }
        await saveZip(zip, `章节附件_${list.length}章_${total}个文件_${stamp()}.zip`);
        Prog.done(failed.length
          ? `完成，${failed.length} 项失败`
          : `已打包 ${total} 个附件`);
      } catch (e) {
        alert('批量下载失败：' + ((e && e.message) || e));
      } finally {
        S.busy = false;
      }
    }
  };

  // ============================ 章节目录 id 采集 ============================
  // index.js 在 renderChapter 里用闭包的 c.id；DOM 上不落 id。
  // 这里通过拦截 show_page 的入参反推：点击叶子章节时 #cid_input 会被写入 catalogId。
  function watchChapterSelection() {
    const cid = $id('cid_input');
    if (!cid) return;
    const obs = new MutationObserver(() => {
      const v = cid.value;
      if (!v) return;
      const box = $id('cid_card');
      if (!box) return;
      const selected = box.querySelector('.chapter-item.selected');
      if (!selected) return;
      selected.__bpCatalogId = v;
      const t = selected.querySelector('.title');
      selected.__bpTitle = t ? (t.textContent || '').replace(/^[●\s]+/, '').trim() : '';
      const cb = selected.querySelector('input.bp-cb');
      if (cb && cb.checked) S.chapters.set(v, { title: selected.__bpTitle });
    });
    obs.observe(cid, { attributes: true, attributeFilter: ['value'], childList: true, subtree: true });
    // value 变化不触发 attribute 变化，补一个轮询兜底（1s，极轻）
    let last = cid.value;
    setInterval(() => {
      if (cid.value === last) return;
      last = cid.value;
      const box = $id('cid_card');
      if (!box) return;
      const selected = box.querySelector('.chapter-item.selected');
      if (!selected || !last) return;
      selected.__bpCatalogId = last;
      const t = selected.querySelector('.title');
      selected.__bpTitle = t ? (t.textContent || '').replace(/^[●\s]+/, '').trim() : '';
      const cb = selected.querySelector('input.bp-cb');
      if (cb && cb.checked) S.chapters.set(last, { title: selected.__bpTitle });
    }, 1000);
  }

  // ============================ 计数刷新 ============================
  BP.refreshCounts = function () {
    const n1 = S.gallery.normal.size, n2 = S.gallery.recycle.size, n3 = S.notes.size, n4 = S.chapters.size;
    const set = (id, n) => { const b = $id(id); if (b) updateCount(b, n); };
    set('bp-bar-normal', n1);
    set('bp-bar-recycle', n2);
    set('bp-bar-notes1', n3);
    set('bp-bar-notes2', n3);
    set('bp-bar-chapters', n4);
  };

  // ============================ ④ 图片 ORB / CORS 自愈 ============================
  // 背景：浏览器对跨域 <img>（no-cors）做 ORB 嗅探。若响应 Content-Type 不是图片
  // （典型：OSS 私有对象返回 403 XML、或中转站回的错误页/octet-stream），
  // 控制台会报 net::ERR_BLOCKED_BY_ORB，且即使服务端给了 ACAO 也照样被拦
  // —— 因为 no-cors 请求根本不看 ACAO。
  // 做法：图片加载失败时改用 fetch(CORS 模式) → blob → objectURL 重试。
  // CORS 模式的响应不受 ORB 约束，只要有带 ACAO 的通道（本地 :5005 代理）即可。
  const IMGFIX = {
    stats: { healed: 0, failed: 0 },

    /** 从 "中转/代理前缀 + 原始URL" 里还原出原始 URL，避免二次套壳 */
    innerUrl(u) {
      if (!u) return '';
      let m = u.match(/\/download\/(https?:\/\/[\s\S]+)$/i);
      if (m) return m[1];
      m = u.match(/\/proxy\/(https?:\/\/[\s\S]+)$/i);
      if (m) return m[1];
      return u;
    },

    async heal(img) {
      const src = img && (img.getAttribute('src') || '');
      if (!src || /^(data:|blob:)/i.test(src)) return false;
      if (img.dataset.bpHealing === '1') return false;
      img.dataset.bpHealing = '1';

      const inner = IMGFIX.innerUrl(src);
      const candidates = [];
      const wrapped = viaProxy(inner);
      if (wrapped && wrapped !== src) candidates.push(wrapped);
      if (inner !== src) candidates.push(inner);

      for (const u of candidates) {
        try {
          const blob = await fetchBlob(u);
          if (!/^image\//i.test(blob.type || '')) throw new Error('非图片类型: ' + (blob.type || '?'));
          const obj = URL.createObjectURL(blob);
          img.src = obj;
          img.dataset.bpHealed = '1';
          delete img.dataset.bpHealing;
          IMGFIX.stats.healed++;
          return true;
        } catch (e) { /* 换下一个候选 */ }
      }
      IMGFIX.stats.failed++;
      return false;
    }
  };

  function initImageHeal() {
    if (window.__bpImgHeal) return;
    window.__bpImgHeal = true;
    document.addEventListener('error', (ev) => {
      const t = ev.target;
      if (!t || t.tagName !== 'IMG') return;
      if (t.dataset.bpHealed === '1') return;
      IMGFIX.heal(t);
    }, true);
  }

  // ============================ 观察与初始化 ============================
  function observe(id, fn) {
    const el = $id(id);
    if (!el) {
      // 容器还没渲染出来（例如在未激活的 tab / 弹窗里）→ 记下来，稍后补挂
      if (!PENDING.some(p => p.id === id)) PENDING.push({ id: id, fn: fn });
      return;
    }
    // 任何一段装饰失败都不能拖垮其它模块
    try { fn(); } catch (e) { console.warn('[BP] decorate ' + id + ' 失败:', e); }
    if (el.__bpObserved) return;
    el.__bpObserved = true;

    // 去抖：自己写入 DOM 也会触发观察者，必须合并成一次并延后执行，
    // 否则"装饰 → 改 DOM → 再装饰"会无限递归把主线程占满（页面卡死）。
    let scheduled = false;
    const mo = new MutationObserver(() => {
      if (scheduled) return;
      scheduled = true;
      setTimeout(() => {
        scheduled = false;
        try { fn(); } catch (e) { console.warn('[BP]', id, e); }
      }, 0);
    });
    mo.observe(el, { childList: true, subtree: true });
  }

  function attachPending() {
    for (let i = PENDING.length - 1; i >= 0; i--) {
      const p = PENDING[i];
      if (!$id(p.id)) continue;
      PENDING.splice(i, 1);
      try { observe(p.id, p.fn); } catch (e) { }
    }
  }

  function hookRenderNotes() {
    if (typeof window.renderNotes !== 'function' || window.renderNotes.__bpHooked) return;
    const orig = window.renderNotes;
    const wrapped = function (notes) {
      try { window.__bpLastNotes = notes || []; } catch (e) {}
      const r = orig.apply(this, arguments);
      try { setTimeout(() => { try { NOTES.decorateNoteList(); } catch (e) {} }, 0); } catch (e) {}
      return r;
    };
    wrapped.__bpHooked = true;
    window.renderNotes = wrapped;
  }

  function init() {
    if (!zipLib()) console.warn('[BP] JSZip 未加载，打包下载会失败');

    observe('pictureTable', () => GALLERY.decorate('normal'));
    observe('recycleTable', () => GALLERY.decorate('recycle'));
    observe('noteList2', () => NOTES.decorateNoteList2());
    observe('noteList', () => NOTES.decorateNoteList());
    observe('cid_card', () => CHAPTERS.decorate());

    hookRenderNotes();
    watchChapterSelection();
    CHAPTERS.captureCourseDetail();
    initImageHeal();

    // 章节树是按需渲染的，切换课程后可能整体重建 → 定期兜底装饰（低频）
    setInterval(() => {
      try { attachPending(); } catch (e) {}
      try { CHAPTERS.decorate(); } catch (e) {}
      try { hookRenderNotes(); } catch (e) {}
    }, 3000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.BatchPick = Object.assign(window.BatchPick || {}, {
    state: S,
    gallery: GALLERY,
    notes: NOTES,
    chapters: CHAPTERS
  });
})();
