#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把一条消息发进正在运行的 WorkBuddy 对话窗口（模拟鼠标键盘）。

用途：检测到特定素材时，由外部脚本调用它，直接在当前对话里发一句唤醒消息。
只在 Windows 上可用；依赖纯标准库 + Pillow（截屏取样用）。

用法：
    python scripts/wake_workbuddy.py "消息内容"
    python scripts/wake_workbuddy.py "消息内容" --dry      # 只粘贴不发送（测位用）
    python scripts/wake_workbuddy.py --probe               # 只定位输入框并截一张图

设计要点：
- 系统若开了缩放（本机 150%），必须声明 DPI 感知，否则截图坐标与鼠标坐标差 1.5 倍。
- 定位不写死坐标：按窗口矩形比例 + 像素扫描找输入框，窗口大小/位置变了也能对上。
- 激活用 AttachThreadInput 强制前置，SetForegroundWindow 单独调用经常被系统拒绝。
- 中文不能靠 SendKeys，走剪贴板 + Ctrl+V。
- 默认发送（Enter）；--dry 只粘贴不按回车，避免误发。
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from pathlib import Path

WB_EXE = r"E:\Program Files\WorkBuddy\WorkBuddy.exe"
ROOT = Path(__file__).resolve().parent.parent


def log(msg):
    """同时写控制台和 logs/wake-YYYY-MM-DD.log。

    以前整个唤醒过程只打到控制台，检测窗口一滚就没了，事后只剩一个
    「成功 / 失败」，拉起花了多久、定位到哪、失败原因是什么全丢了。
    """
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        d = ROOT / 'logs'
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"wake-{time.strftime('%Y-%m-%d')}.log", 'a',
                  encoding='utf-8', newline='\n') as f:
            f.write(line + '\n')
    except Exception:
        pass


u = ctypes.windll.user32
k32 = ctypes.windll.kernel32
g32 = ctypes.windll.gdi32

# 64 位下把 HWND 当默认 int 会截断，句柄参数一律显式声明
u.GetForegroundWindow.restype = wt.HWND
u.IsIconic.argtypes = [wt.HWND]
u.IsZoomed.argtypes = [wt.HWND]
u.IsWindowVisible.argtypes = [wt.HWND]
u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u.ShowWindowAsync.argtypes = [wt.HWND, ctypes.c_int]
u.SetForegroundWindow.argtypes = [wt.HWND]
u.BringWindowToTop.argtypes = [wt.HWND]
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, ctypes.c_uint]
u.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                    ctypes.c_uint]
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.mouse_event.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                          ctypes.c_uint, ctypes.c_size_t]
u.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_size_t]
u.GetWindowTextLengthW.argtypes = [wt.HWND]
u.GetWindowTextW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.c_void_p]
u.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
u.RedrawWindow.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]


# ---------- 抓窗口画面 ----------
class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                ('biPlanes', wt.WORD), ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', wt.LONG),
                ('biYPelsPerMeter', wt.LONG), ('biClrUsed', wt.DWORD),
                ('biClrImportant', wt.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', _BITMAPINFOHEADER), ('bmiColors', wt.DWORD * 3)]


_GDI_READY = False


def _init_gdi():
    """64 位下 GDI 句柄会被当成 int32 截断，所有句柄参数必须显式声明。"""
    global _GDI_READY
    if _GDI_READY:
        return
    u.GetWindowDC.restype = ctypes.c_void_p
    u.GetWindowDC.argtypes = [wt.HWND]
    u.ReleaseDC.argtypes = [wt.HWND, ctypes.c_void_p]
    g32.CreateCompatibleDC.restype = ctypes.c_void_p
    g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    g32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    g32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    g32.SelectObject.restype = ctypes.c_void_p
    g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g32.DeleteObject.argtypes = [ctypes.c_void_p]
    g32.DeleteDC.argtypes = [ctypes.c_void_p]
    g32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                              ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), ctypes.c_uint]
    g32.GetDIBits.restype = ctypes.c_int
    u.PrintWindow.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_uint]
    u.PrintWindow.restype = ctypes.c_bool
    _GDI_READY = True


def grab_window(hwnd):
    """让窗口自己绘制到位图（PrintWindow + PW_RENDERFULLCONTENT）。

    为什么不用 ImageGrab 截屏幕：屏幕熄灭时整屏像素不刷新，窗口被别的窗口压住
    时截到的是遮挡物，两种情况扫描都会失败。PrintWindow 由窗口自己合成，**
    不受遮挡、也不受屏幕开关影响**。最小化时无效（那时窗口本身不绘制），
    那种情况必须先还原窗口再抓。

    返回 (PIL.Image, rect)；抓不到返回 (None, rect)。
    """
    from PIL import Image

    _init_gdi()
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    wl, wt_, wr, wb = r.left, r.top, r.right, r.bottom
    w, h = wr - wl, wb - wt_
    if w < 200 or h < 200:          # 最小化时 rect 会缩成 200x30 之类
        return None, (wl, wt_, wr, wb)

    hdc = u.GetWindowDC(hwnd)
    if not hdc:
        return None, (wl, wt_, wr, wb)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)          # 2 = PW_RENDERFULLCONTENT
    bmi = _BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h               # 负数 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    lines = g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    if not ok or lines == 0:
        return None, (wl, wt_, wr, wb)

    img = Image.frombuffer('RGB', (w, h), buf, 'raw', 'BGRX', 0, 1)
    # PrintWindow 对某些 GPU 合成的窗口会返回黑图（窗口被判定为不可见时尤其如此）。
    # 判据要留够余量：窗口边框、阴影会留下零星非黑像素，采样太稀、阈值太低的话
    # 这些像素会让黑图混过去，后面就拿着黑图找面板色，永远 0px。
    px = img.load()
    step_x, step_y = max(1, w // 40), max(1, h // 40)
    sample = [px[x, y][0] for y in range(0, h, step_y) for x in range(0, w, step_x)]
    if sum(1 for v in sample if v > 12) < len(sample) * 0.10:
        img = None
    return img, (wl, wt_, wr, wb)


# ---------- DPI ----------
def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return "per-monitor"
    except Exception:
        try:
            u.SetProcessDPIAware()
            return "system"
        except Exception:
            return "none"


# ---------- 找窗口 ----------
def find_window(title_part="WorkBuddy"):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, buf, n + 1)
            if title_part in buf.value:
                r = wt.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                w, hh = r.right - r.left, r.bottom - r.top
                # 最小化窗口的 rect 会退化成 (-32000,-32000) 加 158x26 这种
                # 缩略图尺寸，用面积过滤会把它整个筛掉——那样最小化时就
                # 直接报「没找到窗口」，连还原的机会都没有。所以最小化的
                # 一律收下，交给 activate 去还原。
                iconic = bool(u.IsIconic(h))
                if iconic or (w > 600 and hh > 400):   # 非最小化的仍排除托盘、提示小窗
                    found.append((h, buf.value, r, iconic, w * hh))
        return True

    u.EnumWindows(cb, 0)
    if not found:
        return None
    # 优先非最小化的（最小化的要还原后才能用），其次按面积从大到小
    found.sort(key=lambda x: (not x[3], x[4]), reverse=True)
    return found[0][:3]


def _window_ready(hwnd):
    """窗口算不算「真的摆出来了」：不是最小化，且矩形是正常大小。

    只看 GetForegroundWindow 不够：**最小化的窗口也能成为前台窗口**，此时
    GetForegroundWindow 返回它、但窗口并没有还原，rect 是 (-32000,-32000)
    加 237x39 这种缩略图尺寸。之前就是被这一点骗了——判定「已前置」，
    实际窗口根本没切出来，截图永远是缩略图，一路等到超时。
    """
    if u.IsIconic(hwnd):
        return False
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.right - r.left) >= 600 and (r.bottom - r.top) >= 400


def _fit_on_screen(hwnd):
    """窗口跑到屏幕外就拉回来。最大化时 rect 会外扩几像素（阴影），
    坐标会落到屏幕边缘之外，点击就不可达了。"""
    wa = wt.RECT()
    u.SystemParametersInfoW(48, 0, ctypes.byref(wa), 0)     # SPI_GETWORKAREA
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    pad = 16                                                # 阴影会让 rect 外扩十几像素
    if (r.left >= wa.left - pad and r.top >= wa.top - pad
            and r.right <= wa.right + pad and r.bottom <= wa.bottom + pad):
        return
    aw, ah = wa.right - wa.left, wa.bottom - wa.top
    if w >= aw * 0.9 and h >= ah * 0.9:                     # 本来就接近全屏
        u.ShowWindow(hwnd, 3)                               # SW_MAXIMIZE
        time.sleep(0.3)
        return
    nw, nh = min(w, aw), min(h, ah)
    u.SetWindowPos(hwnd, 0, wa.left, wa.top, nw, nh, 0x0004)  # SWP_NOZORDER
    time.sleep(0.3)


def launch_workbuddy(exe=None, wait=25, title_part="WorkBuddy"):
    """拉起 WorkBuddy，让它出现在前台。返回 (ok, 说明)。

    **不管进程在不在都执行一次可执行文件**：WorkBuddy 是单实例应用，
    再次运行时新进程会把已有窗口带到前台（单实例转发），这比用
    SetForegroundWindow 去抢前台可靠得多——后者最容易被系统的前台锁定
    拒绝，表现就是「窗口怎么都不出来」。

    「已在前台」不作为跳过理由：存在前台判定为真、画面却抓不到的状态
    （DWM 隐藏 / 别的虚拟桌面），跳过就等于什么都不做。
    """
    exe = exe or WB_EXE
    if not os.path.exists(exe):
        return False, f"找不到程序：{exe}"

    # 不做「已在前台就跳过」的判断：实测存在「前台判定为真、画面却抓不到」
    # 的状态（窗口被 DWM 隐藏/在另一个虚拟桌面），这时跳过拉起等于什么都不做。
    # 按需求一律执行一次，交给单实例转发去把它带出来。
    try:
        subprocess.Popen([exe], close_fds=True)
    except Exception as e:
        return False, f"启动失败：{type(e).__name__}: {e}"

    # 先给它 4 秒自己到前台：单实例转发对「最小化」有效，但对「窗口可见
    # 只是不在前台」无效（新进程是后台进程，抢不到前台）。所以要限时，
    # 别傻等满 wait——之后立刻用 activate 兜底。
    t0 = time.time()
    soft = min(4.0, wait)
    while time.time() - t0 < soft:
        time.sleep(0.5)
        f = find_window(title_part)
        if f and not u.IsIconic(f[0]) and u.GetForegroundWindow() == f[0]:
            return True, f"已拉起，窗口在前台 {time.time() - t0:.0f}s"
    # 抢前台最多 3 次。之前是「每 1 秒抢一次、连抢 25 秒」，用户想切到别的
    # 窗口就被拽回来，体感就是这窗口一直赖在最前面。抢不到就认，不要纠缠。
    tries = 0
    while time.time() - t0 < wait:
        f = find_window(title_part)
        if f and not u.IsIconic(f[0]) and u.GetForegroundWindow() == f[0]:
            return True, f"已拉起，窗口在前台 {time.time() - t0:.0f}s"
        if f and tries < 3:
            tries += 1
            if activate(f[0]):
                # 刚点完最小化时动画还没放完，还原会被动画压回去，看起来像成功
                # 其实又缩回去了。等半秒复核，不稳就再来一轮。
                time.sleep(0.5)
                if not u.IsIconic(f[0]) and u.GetForegroundWindow() == f[0]:
                    return True, f"已拉起，activate 补前置 {time.time() - t0:.0f}s（第 {tries} 次）"
        time.sleep(1.2)
    return False, f"拉起后 {wait}s 内窗口仍未到前台（抢了 {tries} 次）"


def _clear_topmost(hwnd):
    """把「总在最前」摘掉。

    激活之后如果窗口带着 WS_EX_TOPMOST，它会一直压在别的窗口上面，
    用户切到别处也甩不掉。这里主动清一次（我们从不设 topmost，
    所以只会清掉我们自己或应用加上的那一个）。
    """
    try:
        GWL_EXSTYLE = -20
        WS_EX_TOPMOST = 0x00000008
        HWND_NOTOPMOST = -2
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
        u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex & WS_EX_TOPMOST:
            u.SetWindowPos(hwnd, wt.HWND(HWND_NOTOPMOST), 0, 0, 0, 0,
                           0x0001 | 0x0002 | 0x0010)      # NOSIZE|NOMOVE|NOACTIVATE
    except Exception:
        pass


def _force_redraw(hwnd):
    """逼窗口重新画一遍。

    Chromium 在窗口长时间不可见时会停止合成（occlusion tracking），被还原
    回来可能还是旧帧或全黑，于是 PrintWindow 抓到的画面里没有输入框。发一次
    整窗重绘，再把鼠标在窗口里挪一下（hover 会触发可见性刷新），比干等有效。
    """
    try:
        u.RedrawWindow(hwnd, None, None, 0x0001 | 0x0080 | 0x0100)  # INVALIDATE|ALLCHILDREN|UPDATENOW
    except Exception:
        pass
    try:
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        u.SetCursorPos(cx, cy)
        time.sleep(0.06)
        u.SetCursorPos(cx + 2, cy)
        time.sleep(0.06)
    except Exception:
        pass


def _nudge_repaint(hwnd):
    """把窗口宽度抖 1px 再还原，逼 Chromium 重排重绘。

    Chromium 不理会 RedrawWindow（合成层自己管），但窗口尺寸变化一定会触发
    重新布局与绘制。最大化窗口不能这么干（会取消最大化），所以只在窗口化时用。
    """
    try:
        if u.IsZoomed(hwnd) or u.IsIconic(hwnd):
            return
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 300 or h < 300:
            return
        u.SetWindowPos(hwnd, 0, r.left, r.top, w - 1, h, 0x0004 | 0x0010)   # NOZORDER|NOACTIVATE
        time.sleep(0.12)
        u.SetWindowPos(hwnd, 0, r.left, r.top, w, h, 0x0004 | 0x0010)
        time.sleep(0.12)
    except Exception:
        pass


def activate(hwnd, tries=4):
    """强制把窗口还原 + 提到前台，并返回是否真的可用。

    单次 SetForegroundWindow 经常被系统拒绝（前台锁定），最小化的窗口更是
    可以「当前台但保持最小化」。所以每一轮都先还原、校验，再置前：
      1) 最小化 → ShowWindowAsync(SW_RESTORE)，跨进程别用同步版，会被卡住；
      2) AttachThreadInput 到当前前台线程，抢它的输入队列再置前；
      3) 仍不行就模拟一次 Alt 键——按过 Alt 之后系统会短暂放开前台锁定；
      4) 每轮结束用 _window_ready 校验（不只是看 GetForegroundWindow）。
    """
    for i in range(tries):
        if u.IsIconic(hwnd):
            u.ShowWindowAsync(hwnd, 9)          # SW_RESTORE
            time.sleep(0.3)
            if u.IsIconic(hwnd):
                u.ShowWindow(hwnd, 9)
                time.sleep(0.25)

        if u.GetForegroundWindow() == hwnd and _window_ready(hwnd):
            _fit_on_screen(hwnd)
            return True

        fg = u.GetForegroundWindow()
        t_fg = u.GetWindowThreadProcessId(fg, None)
        t_me = k32.GetCurrentThreadId()
        attached = False
        if t_fg and t_fg != t_me:
            attached = bool(u.AttachThreadInput(t_me, t_fg, True))
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        if attached:
            u.AttachThreadInput(t_me, t_fg, False)
        time.sleep(0.25)

        if u.GetForegroundWindow() == hwnd and _window_ready(hwnd):
            _fit_on_screen(hwnd)
            return True

        # 第二招：模拟 Alt 键松开前台锁定，再置前一次
        key(0x12)                       # VK_MENU down
        time.sleep(0.05)
        key(0x12, False)
        time.sleep(0.05)
        u.SetForegroundWindow(hwnd)
        u.BringWindowToTop(hwnd)
        time.sleep(0.35)
        if u.GetForegroundWindow() == hwnd and _window_ready(hwnd):
            _fit_on_screen(hwnd)
            return True

        if u.IsIconic(hwnd):            # 这一轮还原失败，下一轮再来
            time.sleep(0.4)
    return False


# ---------- 定位输入框 ----------
_LAST_REASON = ""


def _is_cloaked(hwnd):
    """DWM 是否把这个窗口藏起来了（切到别的虚拟桌面、应用被挂起）。

    被 cloak 的窗口：GetForegroundWindow 可能仍返回它、IsIconic 也是 False，
    但屏幕上看不到、截图也抓不到——表现就是「窗口怎么都切不出来」。
    """
    try:
        dwm = ctypes.windll.dwmapi
        val = ctypes.c_int(0)
        hr = dwm.DwmGetWindowAttribute(wt.HWND(hwnd), 14,      # DWMWA_CLOAKED
                                       ctypes.byref(val), ctypes.sizeof(val))
        return (val.value != 0) if hr == 0 else None
    except Exception:
        return None


def _describe_failure(hwnd):
    """定位失败时给出可读的原因（写进 _LAST_REASON，由调用方打印）。

    失败原因必须能自证：是窗口没好（还是最小化 / 抓不到画面），还是画面
    拿到了但里面没有输入框（对话在生成 / 界面不是对话页 / 阈值没命中）。
    不写清楚就只能靠猜。
    """
    global _LAST_REASON
    try:
        iconic = bool(u.IsIconic(hwnd))
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        rect = (r.left, r.top, r.right, r.bottom)
        cloak = _is_cloaked(hwnd)
        ck = {True: '是', False: '否', None: '未知'}[cloak]
        img, _ = grab_window(hwnd)
        if img is None:
            if iconic:
                _LAST_REASON = f"窗口仍是最小化 rect={rect}，还原没生效，cloaked={ck}"
            else:
                _LAST_REASON = (f"抓不到窗口画面 rect={rect}，cloaked={ck}"
                                f"（cloaked=是 说明窗口在别的虚拟桌面或被 DWM 隐藏）")
            return
        px = img.load()
        W, H = img.size
        best = 0
        hist = {'black': 0, 'dark': 0, 'panel': 0, 'mid': 0, 'light': 0}
        tot = 0
        for y in range(int(H * 0.75), H - max(2, int(H * 0.02)), 3):
            cur = 0
            for x in range(W):
                v = px[x, y][0]
                tot += 1
                if v < 12:
                    hist['black'] += 1
                elif v < 24:
                    hist['dark'] += 1
                elif v <= 52:
                    hist['panel'] += 1
                    cur += 1
                    if cur > best:
                        best = cur
                elif v < 180:
                    hist['mid'] += 1
                else:
                    hist['light'] += 1
                if v < 24 or v > 52:
                    cur = 0
        pct = ' '.join(f'{k}={v*100//max(1, tot)}%' for k, v in hist.items())
        _LAST_REASON = (f"画面里没有输入框：图 {W}x{H}，rect={rect}，"
                        f"最长面板段 {best}px / 阈值 {int(W * 0.35)}px，最小化={iconic}，cloaked={ck}｜{pct}")
    except Exception as e:                      # 诊断本身不能再抛
        _LAST_REASON = f"诊断异常 {type(e).__name__}: {e}"


def find_input_box(hwnd, tries=3):
    """定位输入框，并保证结果落在「此刻」的窗口矩形内。

    窗口还原后应用可能自己再调一次大小（实测从 2100x1350 变回 1400x900），
    算出来的坐标就失效了，点下去会落到别处。所以每算完一次都要拿当前
    rect 复核，落不进去就重算。
    """
    for i in range(tries):
        box = _find_input_box_once(hwnd)
        if not box:
            _describe_failure(hwnd)
            return None
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        if (r.right - r.left) >= 200 and r.left <= box[0] and r.top <= box[1] \
                and r.right >= box[2] and r.bottom >= box[3]:
            return box
        _LAST_REASON = (f"定位结果 ({box[0]},{box[1]})~({box[2]},{box[3]}) "
                        f"已不在当前窗口 rect=({r.left},{r.top})~({r.right},{r.bottom}) 内")
        time.sleep(0.35)            # 窗口刚被改过大小，重新来过
    return None


def _find_input_box_once(hwnd):
    """按窗口比例取候选区，用"最长同色连续段"定位输入框。返回 (left, top, right, bottom) 物理坐标，或 None。

    为什么不用"该行出现最多的颜色当背景"：深色主题下输入框与侧边栏同色，
    面积加起来比页面背景还大，会把输入框误判成背景。改用"最长连续同色段"更稳。
    """
    from PIL import ImageGrab

    img, rect = grab_window(hwnd)
    if img is None:
        # PrintWindow 不可用（全黑 / 拒绝绘制）时退回截屏幕。这时窗口必须
        # 真的在屏幕上看得到，否则同样拿不到东西。
        r2 = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r2))
        if r2.right - r2.left < 200 or r2.bottom - r2.top < 200:
            return None
        try:
            img = ImageGrab.grab(bbox=(r2.left, r2.top, r2.right, r2.bottom),
                                 all_screens=True)
        except TypeError:                   # 老版本 Pillow 不认 all_screens
            img = ImageGrab.grab(bbox=(r2.left, r2.top, r2.right, r2.bottom))
        rect = (r2.left, r2.top, r2.right, r2.bottom)

    wl, wt_, wr, wb = rect
    W, H = wr - wl, wb - wt_
    if W < 200 or H < 200:
        return None
    px = img.load()
    sw, sh = img.size
    x0, x1 = 0, min(sw, W)

    def longest_panel_run(y):
        """该行最长的"面板色"连续段（深色主题下输入框与侧边栏同色，比页面背景亮）。
        只认亮度在 24~52 之间的像素，页面纯黑背景会被排除。"""
        def is_panel(c):
            return 24 <= c[0] <= 52
        best = (x0, x0, 0)
        cur_s, cur_on = None, False
        for x in range(x0, x1):
            on = is_panel(px[x, y])
            if on and not cur_on:
                cur_s, cur_on = x, True
            elif not on and cur_on:
                if x - cur_s > best[2]:
                    best = (cur_s, x, x - cur_s)
                cur_on = False
        if cur_on and x1 - cur_s > best[2]:
            best = (cur_s, x1, x1 - cur_s)
        return best

    # 在窗口底部 75%~97% 的纵向范围里逐行找，取面板段最宽的那一行作为探针行
    y_lo = max(0, int(H * 0.75))
    y_hi = min(sh - 2, H - int(H * 0.02))
    best = None
    for y in range(y_lo, y_hi, 3):
        s, e, w = longest_panel_run(y)
        if w > W * 0.35 and (best is None or w > best[2]):
            best = (s, e, w, y)
    if not best:
        return None

    left, right, _, probe_y = best

    # 纵向找上下边界：按"该行面板色占比"判断，能跨过输入框里的文字行
    def row_is_panel(y):
        cnt = tot = 0
        for x in range(left + 15, right - 15, 8):
            tot += 1
            if 24 <= px[x, y][0] <= 52:
                cnt += 1
        return tot > 0 and cnt / tot > 0.55

    top = probe_y
    y = probe_y
    while y > 0 and row_is_panel(y):
        top = y
        y -= 1
    bottom = probe_y
    y = probe_y
    while y < y_hi and row_is_panel(y):
        bottom = y
        y += 1

    if bottom - top < 40:      # 太薄，肯定不是输入框
        return None
    # 局部坐标换回屏幕坐标（点击要用屏幕坐标）
    return (wl + left, wt_ + top, wl + right, wt_ + bottom)


def click(x, y):
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.35)


def key(vk, down=True):
    u.keybd_event(vk, 0, 0 if down else 2, 0)


def combo(vk_mod, vk_key):
    key(vk_mod); time.sleep(0.05)
    key(vk_key); time.sleep(0.05)
    key(vk_key, False); time.sleep(0.04)
    key(vk_mod, False); time.sleep(0.12)


# ---------- 剪贴板 ----------
def set_clipboard(text):
    u.OpenClipboard.argtypes = [ctypes.c_void_p]
    u.OpenClipboard.restype = ctypes.c_bool
    u.EmptyClipboard.restype = ctypes.c_bool
    u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u.SetClipboardData.restype = ctypes.c_void_p
    u.CloseClipboard.restype = ctypes.c_bool
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    data = text.encode("utf-16-le") + b"\x00\x00"
    h = k32.GlobalAlloc(0x0042, len(data))       # GMEM_MOVEABLE | GMEM_ZEROINIT
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)

    if not u.OpenClipboard(None):
        raise RuntimeError("打不开剪贴板（可能被其他程序占用）")
    u.EmptyClipboard()
    u.SetClipboardData(13, h)                    # CF_UNICODETEXT
    u.CloseClipboard()


# ---------- 主流程 ----------
def send_message(text, dry=False, title_part="WorkBuddy", wait=180, exe=None):
    # 不管进程在不在都先拉起一次，让窗口到前台（单实例应用会自己转发）
    ok, info = launch_workbuddy(exe, title_part=title_part)
    log(f"拉起 WorkBuddy：{info}")
    if not ok:
        return False, info
    found = find_window(title_part)
    if not found:
        return False, "拉起后仍没找到 WorkBuddy 窗口"
    hwnd, title, _ = found

    if not activate(hwnd):
        return False, "窗口无法前置（可能被系统拒绝或有全屏程序在前台）"
    _clear_topmost(hwnd)                # 别让它一直压在别的窗口上
    _force_redraw(hwnd)                 # 长时间最小化后可能还是旧帧/黑屏

    # 等输入框出现。**AI 正在生成回答时，界面底部没有输入框**（那块位置被
    # 停止按钮和进度占据），这时定位必然失败——早期把这种情况报成「界面
    # 结构变了」，白丢一次唤醒（实测三连 FAIL 全是这个原因）。
    # 所以不是重试三次就放弃，而是等：每 2 秒试一次，最多等 wait 秒。
    t_start = time.time()
    deadline = t_start + wait
    box = None
    n = 0
    while time.time() < deadline:
        n += 1
        time.sleep(0.6 if n == 1 else 1.6)
        box = find_input_box(hwnd)
        if box:
            break
        if u.IsIconic(hwnd):
            # 只有被最小化才还原。前台被别人占了就不管——等待期间反复抢
            # 焦点会让人没法用电脑，表现就像这个窗口一直置顶赖着不走。
            activate(hwnd)
            _clear_topmost(hwnd)
        if n % 2 == 0:                  # 每两轮抖一次，太频繁会一直在抖
            _nudge_repaint(hwnd)
            _force_redraw(hwnd)
        waited = int(time.time() - t_start)
        if n % 5 == 0:
            log(f"还没出现输入框，已等 {waited}s｜{_LAST_REASON}")
    if not box:
        return False, (f"等了 {wait}s 仍没有输入框：对话一直在生成，"
                       f"或窗口被完全遮挡 / 界面已变")
    left, top, right, bottom = box

    # 点之前再确认一次前台：设置里粘贴、回车都是发给当前前台窗口的，
    # 一旦中途被别的窗口抢走，Delete 和 Ctrl+V 就会落进别人的输入框。
    if u.GetForegroundWindow() != hwnd and not activate(hwnd):
        return False, "发送前窗口失去前台，已放弃（没有键入任何按键）"
    _clear_topmost(hwnd)

    # 点进文字区：横向取中间偏左，纵向取上部（避开底部按钮行）
    tx = int(left + (right - left) * 0.45)
    ty = int(top + (bottom - top) * 0.30)
    click(tx, ty)

    # 清空可能残留的草稿
    combo(0x11, 0x41)          # Ctrl+A
    key(0x2E); key(0x2E, False)  # Delete
    time.sleep(0.2)

    set_clipboard(text)
    time.sleep(0.15)
    combo(0x11, 0x56)          # Ctrl+V
    time.sleep(0.6)

    if dry:
        return True, f"已粘贴但未发送（输入框 {left},{top}~{right},{bottom}；点击点 {tx},{ty}）"

    key(0x0D); key(0x0D, False)   # Enter
    time.sleep(0.5)
    _clear_topmost(hwnd)          # 发完再摘一次，别让它压着别的窗口
    return True, f"已发送（输入框 {left},{top}~{right},{bottom}；点击点 {tx},{ty}）"


def probe():
    found = find_window()
    if not found:
        print("没找到窗口"); return 1
    hwnd, title, r = found
    print(f"窗口: {title}  HWND={hwnd}  还原前 rect=({r.left},{r.top})~({r.right},{r.bottom})  "
          f"尺寸={r.right-r.left}x{r.bottom-r.top}  最小化={bool(u.IsIconic(hwnd))}")
    ok = activate(hwnd)
    r2 = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r2))
    print(f"前置: {ok}  还原后 rect=({r2.left},{r2.top})~({r2.right},{r2.bottom})  "
          f"尺寸={r2.right-r2.left}x{r2.bottom-r2.top}  最小化={bool(u.IsIconic(hwnd))}")
    fg = u.GetForegroundWindow()
    if fg != hwnd:
        n = u.GetWindowTextLengthW(fg)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(fg, buf, n + 1)
        print(f"当前前台不是目标窗口，而是: {buf.value!r}")
    box = find_input_box(hwnd)
    print("输入框:", box)
    return 0 if box else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="?", default="", help="要发送的消息")
    ap.add_argument("--dry", action="store_true", help="只粘贴不按回车")
    ap.add_argument("--probe", action="store_true", help="只定位输入框，不操作")
    ap.add_argument("--title", default="WorkBuddy", help="窗口标题关键字")
    ap.add_argument("--wait", type=int, default=180,
                    help="等输入框出现的最长秒数（对话生成中时底部没有输入框），默认 180")
    ap.add_argument("--exe", default=None,
                    help=f"WorkBuddy 可执行文件路径，默认 {WB_EXE}")
    args = ap.parse_args()

    dpi = set_dpi_aware()

    if args.probe:
        print("DPI 模式:", dpi)
        sys.exit(probe())

    if not args.message.strip():
        print("消息为空"); sys.exit(2)

    ok, info = send_message(args.message, dry=args.dry, title_part=args.title,
                            wait=args.wait, exe=args.exe)
    log(("OK  " if ok else "FAIL") + " " + info)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
