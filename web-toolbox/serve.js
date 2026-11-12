// 本地静态服务 + 同源转发（网页工具箱用它托管静态文件）
//   1) 把本文件所在目录（web-toolbox/）以 http 提供；file:// 下 fetch 与 iframe 受限
//   2) /proxy/<绝对URL> 做服务端转发：OSS 上的图片没有 Access-Control-Allow-Origin，
  console.log("领创转发 http://127.0.0.1:" + PORT + "/linspirer/public-interface.php");
//      浏览器直连会被 CORS 拦；走同源路径就没有跨域问题，也不需要额外开进程。
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

const ROOT = path.resolve(__dirname);
const PORT = Number(process.argv[2] || 8088);
const PROXY_TIMEOUT_MS = 30000;
const PROXY_MAX_BYTES = 64 * 1024 * 1024;

// 领创（Linspirer）JSON-RPC 同源转发配置
// 网页版原先 POST 到 https://<relay-host>/public-interface.php，
// 但该域名已被复用成只接受 GET/OPTIONS 的「GForm 授权管理」站，POST 一律 405。
// 真实上游 https://cloud.linspirer.com:883 仍接受 POST，却不返回 CORS 头，浏览器无法直连。
// 因此由本服务把 /linspirer/<path> 转发到上游，页面只访问同源地址。
const LINSPIRER_UPSTREAM = 'https://cloud.linspirer.com:883';
const LINSPIRER_TIMEOUT_MS = 20000;
const LINSPIRER_MAX_BYTES = 16 * 1024 * 1024;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.csv': 'text/csv; charset=utf-8',
  '.wasm': 'application/wasm',
  '.pdf': 'application/pdf',
  '.map': 'application/json; charset=utf-8',
  '.bin': 'application/octet-stream'
};

function targetOf(rawPath) {
  let rest = rawPath.replace(/^\/proxy\/?/, '');
  if (!rest) return null;
  if (!/^https?:\/\//i.test(rest)) {
    try {
      const dec = decodeURIComponent(rest);
      if (/^https?:\/\//i.test(dec)) rest = dec;
    } catch (e) { /* 保持原样 */ }
  }
  return /^https?:\/\//i.test(rest) ? rest : null;
}

function handleProxy(req, res) {
  const rawPath = req.url || '/';

  if (rawPath === '/proxy/ping' || rawPath === '/proxy/ping/') {
    res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*' });
    return res.end('pong');
  }

  const target = targetOf(rawPath);
  if (!target) {
    res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
    return res.end('用法：/proxy/http://example.com/a.jpg');
  }

  let u;
  try {
    u = new URL(target);
  } catch (e) {
    res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
    return res.end('目标地址不合法');
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') {
    res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
    return res.end('只支持 http/https');
  }

  const mod = u.protocol === 'https:' ? https : http;
  const up = mod.request(u, {
    method: 'GET',
    timeout: PROXY_TIMEOUT_MS,
    headers: {
      'Referer': 'https://<web-host>/',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155.0 Safari/537.36',
      'Accept': '*/*'
    }
  }, (ur) => {
    if (ur.statusCode >= 300 && ur.statusCode < 400 && ur.headers.location) {
      ur.resume();
      const next = new URL(ur.headers.location, u).toString();
      req.url = '/proxy/' + next;   // 简单跟随一层
      return handleProxy(req, res);
    }
    const ct = ur.headers['content-type'] || 'application/octet-stream';
    const len = ur.headers['content-length'];
    const head = {
      'Content-Type': ct,
      'Cache-Control': 'no-store',
      'Access-Control-Allow-Origin': '*'
    };
    if (len) head['Content-Length'] = len;
    res.writeHead(ur.statusCode || 200, head);
    let got = 0;
    ur.on('data', (chunk) => {
      got += chunk.length;
      if (got > PROXY_MAX_BYTES) { ur.destroy(); res.destroy(); return; }
      res.write(chunk);
    });
    ur.on('end', () => res.end());
    ur.on('error', () => { try { res.destroy(); } catch (e) {} });
  });

  up.on('timeout', () => up.destroy(new Error('上游超时')));
  up.on('error', (e) => {
    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('转发失败：' + e.message);
    } else {
      try { res.destroy(); } catch (err) {}
    }
  });
  up.end();
}

function linsCors(extra) {
  return Object.assign({
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,HEAD,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Cache-Control': 'no-store'
  }, extra || {});
}

function linsForward(target, req, res, body, hops) {
  let u;
  try {
    u = new URL(target);
  } catch (e) {
    res.writeHead(400, linsCors({ 'Content-Type': 'text/plain; charset=utf-8' }));
    return res.end('转发目标不合法');
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') {
    res.writeHead(400, linsCors({ 'Content-Type': 'text/plain; charset=utf-8' }));
    return res.end('只支持 http/https');
  }

  const mod = u.protocol === 'https:' ? https : http;
  const method = req.method === 'HEAD' ? 'HEAD' : (req.method === 'POST' ? 'POST' : 'GET');
  // GET/HEAD 是文件下载：APK 动辄几十 MB，不能套用 API 的字节上限与超时，
  // 否则传到一半被 destroy，Chrome 就会报「请检查互联网连接状况」。
  const isDownload = method !== 'POST';
  const headers = {
    'Accept': req.headers['accept'] || '*/*',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155.0 Safari/537.36'
  };
  if (method === 'POST') headers['Content-Type'] = req.headers['content-type'] || 'application/json';
  // 断点续传：Chrome 点「继续」会带 Range，必须原样透传给上游
  ['range', 'if-range', 'if-modified-since', 'if-none-match'].forEach((h) => {
    if (req.headers[h]) headers[h] = req.headers[h];
  });

  const up = mod.request(u, {
    method: method,
    headers: headers,
    timeout: isDownload ? 0 : LINSPIRER_TIMEOUT_MS
  }, (ur) => {
    // 跟随 3xx（下载接口会 302 到实际文件），最多 3 跳
    if (ur.statusCode >= 300 && ur.statusCode < 400 && ur.headers.location && (hops || 0) < 3) {
      ur.resume();
      const next = new URL(ur.headers.location, u).toString();
      return linsForward(next, req, res, null, (hops || 0) + 1);
    }

    // 透传文件元信息：Content-Length / Content-Range / Accept-Ranges / Content-Disposition
    // 缺了这些，Chrome 既显示不了进度，也无法续传，断线只能报网络错误。
    const out = linsCors();
    ['content-type', 'content-length', 'content-range', 'accept-ranges',
     'content-disposition', 'etag', 'last-modified', 'location'].forEach((h) => {
      if (ur.headers[h]) out[h] = ur.headers[h];
    });
    if (!out['content-type']) out['content-type'] = 'application/octet-stream';
    res.writeHead(ur.statusCode || 200, out);

    if (method === 'HEAD') {
      ur.resume();
      return res.end();
    }

    if (isDownload) {
      // 纯流式转发：不计数、不主动超时；客户端取消时一并关闭上游连接
      ur.pipe(res);
      ur.on('error', () => { try { res.destroy(); } catch (e) {} });
      res.on('close', () => { if (!res.writableEnded) ur.destroy(); });
      return;
    }

    // JSON-RPC 响应仍然限流，避免异常响应把内存吃满
    let got = 0;
    ur.on('data', (chunk) => {
      got += chunk.length;
      if (got > LINSPIRER_MAX_BYTES) { ur.destroy(); res.destroy(); return; }
      res.write(chunk);
    });
    ur.on('end', () => res.end());
    ur.on('error', () => { try { res.destroy(); } catch (e) {} });
  });

  up.on('timeout', () => {
    if (!isDownload) up.destroy(new Error('上游超时'));
  });
  up.on('error', (e) => {
    if (!res.headersSent) {
      res.writeHead(502, linsCors({ 'Content-Type': 'text/plain; charset=utf-8' }));
      res.end('领创上游转发失败：' + e.message);
    } else try { res.destroy(); } catch (err) {}
  });

  if (method === 'POST' && body && body.length) up.end(body);
  else up.end();
}

function handleLinspirer(req, res) {
  const rawPath = req.url || '/';
  if (rawPath.startsWith('/linspirer/ping')) {
    res.writeHead(200, linsCors({ 'Content-Type': 'text/plain; charset=utf-8' }));
    return res.end('pong');
  }
  if (req.method === 'OPTIONS') {
    res.writeHead(204, linsCors());
    return res.end();
  }
  if (!['GET', 'HEAD', 'POST'].includes(req.method)) {
    res.writeHead(405, linsCors({
      'Allow': 'GET,HEAD,POST,OPTIONS',
      'Content-Type': 'application/json; charset=utf-8'
    }));
    return res.end('{"message":"Method Not Allowed"}');
  }

  // rel 一定以 / 开头，拼到固定上游后仍是同一个 host，避免被当成开放代理
  const rel = rawPath.slice('/linspirer'.length) || '/';
  const target = LINSPIRER_UPSTREAM + rel;
  const chunks = [];
  let size = 0;
  req.on('data', (c) => {
    size += c.length;
    if (size > LINSPIRER_MAX_BYTES) { req.destroy(); return; }
    chunks.push(c);
  });
  req.on('end', () => linsForward(target, req, res, Buffer.concat(chunks), 0));
  req.on('error', () => { try { res.destroy(); } catch (e) {} });
}

function sendFile(full, res) {
  fs.readFile(full, (err, buf) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      return res.end('404 ' + path.basename(full));
    }
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(full).toLowerCase()] || 'application/octet-stream',
      'Cache-Control': 'no-store',
      'Access-Control-Allow-Origin': '*'
    });
    res.end(buf);
  });
}

http.createServer((req, res) => {
  const rawPath = req.url || '/';
  if (rawPath === '/proxy' || rawPath.startsWith('/proxy/')) {
    return handleProxy(req, res);
  }

  // 领创 JSON-RPC：POST /linspirer/public-interface.php 走服务端转发（无 CORS 问题）
  if (rawPath === '/linspirer' || rawPath.startsWith('/linspirer/')) {
    return handleLinspirer(req, res);
  }


  let p = decodeURIComponent(rawPath.split('?')[0].split('#')[0]);
  if (p === '/' || p === '') p = '/index.html';
  const full = path.join(ROOT, p.replace(/^\/+/, ''));
  if (!full.startsWith(ROOT)) {
    res.writeHead(403);
    return res.end('forbidden');
  }
  sendFile(full, res);
}).listen(PORT, '127.0.0.1', () => {
  console.log('serving ' + ROOT + ' at http://127.0.0.1:' + PORT + '/');
  console.log('同源转发 http://127.0.0.1:' + PORT + '/proxy/<绝对URL>');
  console.log("领创转发 http://127.0.0.1:" + PORT + "/linspirer/public-interface.php");
});
