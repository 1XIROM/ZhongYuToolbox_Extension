"""把含 $...$ / $$...$$ 的 HTML 片段预渲染成 KaTeX 静态节点。

产物是「单文件、零脚本、零 CDN」：KaTeX 只在本地 Chrome 里跑一次，
把公式变成静态 DOM（含 MathML），字体以 base64 内嵌进 CSS。因此转 PDF /
打印时不存在「脚本没执行」「CDN 连不上」「字体缺失」导致公式变源码的风险。

资源查找顺序：包内 assets/katex（随包分发，离线可用）→ 指定的 katex_dir
→ 状态目录缓存 → 从 CDN 下载并缓存。
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from .lock import FileLock, LockTimeout

CDN = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/"
PKG_ASSETS = Path(__file__).resolve().parent / "assets" / "katex"

_WIN = [
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
]
_MAC = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
_LINUX = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
]
_WHICH = ["chrome", "google-chrome", "google-chrome-stable", "chromium",
          "chromium-browser", "msedge", "microsoft-edge"]

ENV_CHROME = ("ANSWER_CHROME", "CHROME_PATH", "GOOGLE_CHROME_SHIM")


class ChromeNotFound(RuntimeError):
    pass


@contextmanager
def _chrome_slot(work: Path, slots: int = 4, timeout: float = 300.0):
    """抢一个空闲的 Chrome profile 槽位，用完释放。

    Chrome 对同一个 user-data-dir 是独占的，两个进程同时用一个会直接以
    profile in use 退出。这里把 profile 做成固定几个槽：谁先抢到谁用，
    不同槽之间完全并行，槽满了就等——既不会撞车，也不会把并发压成串行。

    用非阻塞 try-lock 而不是排队锁：排队会让所有进程挤在同一个槽上，
    等于没有并发。
    """
    deadline = time.monotonic() + timeout
    while True:
        for idx in range(slots):
            suffix = "" if idx == 0 else f"-{idx}"
            lock = FileLock(work / f".chrome{suffix}.lock", timeout=0)
            try:
                lock.acquire()
            except LockTimeout:
                continue
            try:
                yield suffix
            finally:
                lock.release()
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"等待可用的渲染槽超时（{timeout}s，共 {slots} 个槽）")
        time.sleep(0.15)


def find_chrome(explicit: Optional[str] = None) -> Optional[str]:
    """按 显式配置 → 环境变量 → 平台常见路径 → PATH 查找的顺序定位浏览器。"""
    for cand in (explicit, *(os.environ.get(k) for k in ENV_CHROME)):
        if cand and Path(cand).exists():
            return str(cand)
    candidates = _WIN if sys.platform == "win32" else (_MAC if sys.platform == "darwin" else _LINUX)
    for p in candidates:
        if os.path.exists(p):
            return p
    for name in _WHICH:
        found = shutil.which(name)
        if found:
            return found
    return None


class KatexAssets:
    def __init__(self, cache_dir: Optional[Path] = None, katex_dir: Optional[Path] = None,
                 allow_download: bool = True):
        self.cache_dir = Path(cache_dir) if cache_dir else PKG_ASSETS
        self.katex_dir = Path(katex_dir) if katex_dir else None
        self.allow_download = allow_download
        self._css_memo: Optional[str] = None

    def locate(self, name: str) -> Path:
        for base in (self.katex_dir, PKG_ASSETS, self.cache_dir):
            if base is None:
                continue
            f = Path(base) / name
            if f.exists() and f.stat().st_size > 0:
                return f
        if not self.allow_download:
            raise FileNotFoundError(
                f"缺少 KaTeX 资源 {name}，且已禁用下载；"
                f"请把 Resources 放到 {self.cache_dir}（离线部署时用）"
            )
        dest = self.cache_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(CDN + name, dest)
        except Exception as e:  # pragma: no cover - 网络相关
            raise RuntimeError(f"下载 KaTeX 资源失败（{name}）：{e}") from e
        if dest.stat().st_size == 0:
            raise RuntimeError(f"下载到的 KaTeX 资源为空：{name}")
        return dest

    def css(self) -> str:
        """返回把 woff2 字体内联为 base64 的 KaTeX CSS（带磁盘缓存）。"""
        if self._css_memo is not None:
            return self._css_memo
        src = self.locate("katex.min.css")
        cache = self.cache_dir / "katex_inline.css"
        if cache.exists() and cache.stat().st_mtime >= src.stat().st_mtime:
            self._css_memo = cache.read_text(encoding="utf-8")
            return self._css_memo
        css = src.read_text(encoding="utf-8")
        # 只保留 woff2，去掉 woff/ttf 回退，体积从 ~1.4MB 降到 ~0.3MB
        css = re.sub(
            r',url\(fonts/[^)]+\.woff\) format\("woff"\),url\(fonts/[^)]+\.ttf\) format\("truetype"\)',
            "", css)
        for rel in sorted(set(re.findall(r"url\((fonts/[^)]+\.woff2)\)", css))):
            b64 = base64.b64encode(self.locate(rel).read_bytes()).decode("ascii")
            css = css.replace("url(%s)" % rel, "url(data:font/woff2;base64,%s)" % b64)
        try:
            cache.write_text(css, encoding="utf-8")
        except OSError:
            pass
        self._css_memo = css
        return css


_MEASURE_JS = r"""
(function () {
  var secs = document.querySelectorAll('section.page');
  if (!secs.length) return;
  var hs = [];
  for (var i = 0; i < secs.length; i++) {
    var s = secs[i];
    // `.page + .page` 带 18px 上间距 + 12px 内边距 + 1px 边框，
    // 这些是页与页的间隔，不属于本题内容，测之前临时清零，测完还原。
    var old = s.getAttribute('style');
    s.style.marginTop = '0px';
    s.style.paddingTop = '0px';
    s.style.borderTopWidth = '0px';
    hs.push(Math.round(s.getBoundingClientRect().height));
    // 只要碰过 CSSOM，Chrome 序列化时就会留下 style=""，成品里会多一个空属性，
    // 还会让「<section class="page">」的精确匹配失效。这里一律清掉原值。
    if (old === null) s.removeAttribute('style'); else s.setAttribute('style', old);
    if (s.getAttribute('style') === '') s.removeAttribute('style');
  }
  document.body.setAttribute('data-block-heights', hs.join(','));
})();
"""


_MATH_JS = r"""
(function () {
  var RE = /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g;
  var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
  var nodes = [], n;
  while ((n = walker.nextNode())) nodes.push(n);
  var count = 0, failed = 0;
  nodes.forEach(function (node) {
    var t = node.nodeValue;
    if (!t || t.indexOf('$') < 0) return;
    if (node.parentNode && node.parentNode.closest && node.parentNode.closest('.katex')) return;
    RE.lastIndex = 0;
    if (!RE.test(t)) return;
    RE.lastIndex = 0;
    var frag = document.createDocumentFragment(), last = 0, m;
    while ((m = RE.exec(t)) !== null) {
      if (m.index > last) frag.appendChild(document.createTextNode(t.slice(last, m.index)));
      var tex = m[1] !== undefined ? m[1] : m[2];
      var disp = m[1] !== undefined;
      var span = document.createElement('span');
      try {
        span.innerHTML = katex.renderToString(tex, {throwOnError: false, displayMode: disp});
        count++;
      } catch (e) {
        span.textContent = m[0];
        failed++;
      }
      frag.appendChild(span);
      last = m.index + m[0].length;
    }
    if (last < t.length) frag.appendChild(document.createTextNode(t.slice(last)));
    node.parentNode.replaceChild(frag, node);
  });
  document.body.setAttribute('data-math-count', String(count));
  document.body.setAttribute('data-math-failed', String(failed));
})();
"""


def prerender(body_html: str, page_css: str = "", *, chrome: Optional[str] = None,
              assets: Optional[KatexAssets] = None, work_dir: Optional[Path] = None,
              timeout: int = 180, with_math: bool = True,
              measure: bool = True) -> str:
    """在本地浏览器里把 $...$ 渲染成静态节点，返回 (body, stats)。

    with_math=False 时跳过 KaTeX，只做布局测量（纯文字题用，省掉 270KB 脚本解析）；
    measure=True 时顺带量出每个 <section class="page"> 的实际高度，
    结果在返回的 stats["heights"] 里，用来判断有没有超出一页。

    Chrome profile 走槽位池（见 _chrome_slot），所以线程池批量渲、多个进程
    同时渲都不会抢同一个 user-data-dir，也不用手工指定槽号。

    找不到浏览器时抛 ChromeNotFound，由调用方决定提示还是降级，
    不再静默退化成纯文本公式（那会让成品“能过校验但排版是坏的”）。
    """
    exe = find_chrome(chrome)
    if exe is None:
        raise ChromeNotFound(
            "未找到 Chrome/Edge/Chromium。请安装其一，或设置环境变量 ANSWER_CHROME 指向可执行文件。"
        )
    assets = assets or KatexAssets()
    work = Path(work_dir or assets.cache_dir)
    work.mkdir(parents=True, exist_ok=True)
    scripts = ""
    if with_math:
        katex_js = assets.locate("katex.min.js").read_text(encoding="utf-8")
        scripts += "<script>" + katex_js + "</script><script>" + _MATH_JS + "</script>"
    if measure:
        scripts += "<script>" + _MEASURE_JS + "</script>"

    with _chrome_slot(work) as suffix:
        profile = work / ("chrome-profile" + suffix)
        tmp = work / f"_prerender{suffix}.html"
        tmp.write_text(
            "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
            + page_css + "\n" + assets.css()
            + "</style></head><body><div class='doc'>" + body_html
            + "</div>" + scripts
            + "</body></html>", encoding="utf-8")
        r = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--user-data-dir=" + str(profile),
             "--virtual-time-budget=8000", "--dump-dom", tmp.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
    dom = r.stdout or ""
    m = re.search(r"<body[^>]*>([\s\S]*)</body>", dom, re.I)
    if not m:
        raise RuntimeError("预渲染未取到 DOM：" + (r.stderr or "")[-400:])
    body = re.sub(r"<script[\s\S]*?</script>", "", m.group(1))
    heights: list = []
    # 属性挂在 <body> 上，而 body 片段是从 <body ...> 之后截的，
    # 所以必须从整篇 dom 里取，不能从 body 里取。
    hm = re.search(r'data-block-heights="([\d,]*)"', dom)
    if hm and hm.group(1):
        heights = [int(x) for x in hm.group(1).split(",") if x.isdigit()]
    body = re.sub(r'\sdata-(?:math-(?:count|failed)|block-heights)="[^"]*"', "", body)
    stats = {"math": body.count('class="katex"'),
             "failed": body.count("katex-error"),
             "heights": heights}
    # 并发渲多份时这个属性会被别的线程覆盖，调用方必须用返回值里的 stats。
    prerender.last_stats = stats
    return body, stats


prerender.last_stats = {"math": 0, "failed": 0, "heights": []}
