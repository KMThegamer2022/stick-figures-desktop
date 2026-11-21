"""Alan-Becker-style stick figures that roam your Windows desktop.

Run:   python sticks.py [how_many]        (default 3)
Quit:  Ctrl+Shift+Q
Test:  python sticks.py --selftest        (any OS, no windows touched)

They walk, dance, wave, chase your mouse, jump onto windows, climb window sides and
the screen edges, shove windows sideways, and stomp a window's minimise button.
Maximised / minimised windows are never touched. The overlay is click-through.
"""
import ctypes, json, math, os, random, shutil, sys, threading, time
import urllib.error, urllib.request
from ctypes import wintypes as wt

REPO = "KMThegamer2022/stick-figures-desktop"
VERSION = "0.0.0"                          # stamped from the git tag by the release workflow
AUTO_UPDATE = True                         # check GitHub for a newer release at start (installed .exe only)

GRAV, JUMP_V, JUMP_MAX = 0.9, 16.5, 135   # JUMP_MAX: tallest ledge (px) a jump can reach
SPEED, PUSH, CLIMB = 2.2, 1.6, 2.0
GAP = 70                                   # pushed windows stop this far from the screen edge, leaving room to hop down behind them
PRESS_MINIMIZE = True                      # True: a stomp really minimises that window
EXPLORE = True                             # True: figures open your Desktop folders (and subfolders) in File Explorer
COLORS = ["#202020", "#e03030", "#2fa84f", "#2f6fe0", "#f0b020", "#f07820"]
SKIP = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
KEY = "#010101"                            # transparent colour of the overlay
OWN = set()
Z = []                                     # every visible window, topmost first; refreshed by the main loop

# shortcut: floor = bottom of the PRIMARY monitor's work area; upgrade if you want per-monitor floors
VX, VY, VW, VH, FLOOR = 0, 0, 1920, 1080, 1040
WIN = sys.platform == "win32"
if WIN:
    u32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)   # real pixels, not scaled ones
    except Exception: u32.SetProcessDPIAware()
    H, I = wt.HWND, ctypes.c_int
    for name, args in dict(IsWindow=[H], IsWindowVisible=[H], IsIconic=[H], IsZoomed=[H],
                           GetWindowTextLengthW=[H], GetWindowLongW=[H, I], SetWindowLongW=[H, I, ctypes.c_long],
                           GetClassNameW=[H, wt.LPWSTR, I], GetWindowRect=[H, ctypes.c_void_p], ShowWindow=[H, I],
                           SetWindowPos=[H, H, I, I, I, I, wt.UINT], GetAncestor=[H, wt.UINT],
                           PostMessageW=[H, wt.UINT, wt.WPARAM, wt.LPARAM],
                           FindWindowW=[wt.LPCWSTR, wt.LPCWSTR], FindWindowExW=[H, H, wt.LPCWSTR, wt.LPCWSTR],
                           GetWindowThreadProcessId=[H, ctypes.POINTER(wt.DWORD)], ClientToScreen=[H, ctypes.POINTER(wt.POINT)],
                           SendMessageTimeoutW=[H, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)]).items():
        getattr(u32, name).argtypes = args
    u32.GetAncestor.restype = u32.FindWindowW.restype = u32.FindWindowExW.restype = H
    k32, P, SZ = ctypes.windll.kernel32, ctypes.c_void_p, ctypes.c_size_t
    for name, args in dict(OpenProcess=[wt.DWORD, wt.BOOL, wt.DWORD], CloseHandle=[P],
                           VirtualAllocEx=[P, P, SZ, wt.DWORD, wt.DWORD], VirtualFreeEx=[P, P, SZ, wt.DWORD],
                           ReadProcessMemory=[P, P, P, SZ, P], WriteProcessMemory=[P, P, P, SZ, P]).items():
        getattr(k32, name).argtypes = args
    k32.OpenProcess.restype = k32.VirtualAllocEx.restype = P
    dwm.DwmGetWindowAttribute.argtypes = [H, wt.DWORD, ctypes.c_void_p, wt.DWORD]
    VX, VY, VW, VH = (u32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    _wa = wt.RECT(); u32.SystemParametersInfoW(48, 0, ctypes.byref(_wa), 0); FLOOR = _wa.bottom
    ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


# ---- Windows glue (the selftest swaps these for fakes) ---------------------------------
def rect(h):
    """Visible frame (l,t,r,b) of a window that is on screen (maximised ones included), else None."""
    if not (u32.IsWindow(h) and u32.IsWindowVisible(h)) or u32.IsIconic(h): return None
    r = wt.RECT()
    if dwm.DwmGetWindowAttribute(h, 9, ctypes.byref(r), ctypes.sizeof(r)): u32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom

def live(h):
    """Frame of a window we may use, or None if gone / minimised / maximised."""
    return None if u32.IsZoomed(h) else rect(h)

def move(h, dx):  # NOSIZE | NOZORDER | NOACTIVATE
    r = wt.RECT(); u32.GetWindowRect(h, ctypes.byref(r)); u32.SetWindowPos(h, 0, r.left + dx, r.top, 0, 0, 0x15)

def minimize(h): u32.ShowWindow(h, 11)   # SW_FORCEMINIMIZE: works even if the app is hung

def cursor():
    p = wt.POINT(); u32.GetCursorPos(ctypes.byref(p)); return p.x, p.y

def down(): return u32.GetAsyncKeyState(1) & 0x8000   # left mouse button: never fight the user's drag

def list_windows():
    """(movable, everything), each topmost first as (hwnd, l, t, r, b). movable = windows we may stand on, shove or
    minimise (not maximised, not tool windows); everything = every visible window, to tell which are hidden behind others."""
    mov, allw = [], []
    @ENUM
    def cb(h, _):
        ex = u32.GetWindowLongW(h, -20)
        if h not in OWN and u32.GetWindowTextLengthW(h) and not ex & 0x08000020:   # skip no-activate / click-through overlays
            c = wt.DWORD(); dwm.DwmGetWindowAttribute(h, 14, ctypes.byref(c), 4)   # cloaked = other desktop
            b = ctypes.create_unicode_buffer(64); u32.GetClassNameW(h, b, 64)
            r = None if c.value or b.value in SKIP else rect(h)
            if r:
                allw.append((h, *r))
                if (not ex & 0x80 and not u32.IsZoomed(h) and r[2]-r[0] >= 150 and r[3]-r[1] >= 80
                        and not (r[2]-r[0] >= VW and r[3]-r[1] >= VH)):
                    mov.append((h, *r))
        return True
    u32.EnumWindows(cb, 0)
    return mov, allw


# ---- updater ----------------------------------------------------------------------------
def ver(tag):
    """'v1.10' -> (1, 10). shortcut: numeric dotted tags only; anything else parses as () = never newer."""
    return tuple(int(x) for x in tag.lstrip("v").split(".") if x.isdigit())

def latest_tag():
    """Newest release tag. Tries the GitHub API; on rate limit (403/429), outage or bad reply it falls back
    to the github.com/<repo>/releases/latest redirect, which isn't subject to the API's anonymous limit."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"User-Agent": "stick-figures-updater", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as r: return json.load(r)["tag_name"]
    except (urllib.error.URLError, OSError, KeyError, ValueError):   # HTTPError (rate limit, 5xx) is a URLError
        with urllib.request.urlopen(f"https://github.com/{REPO}/releases/latest", timeout=10) as r:
            return r.geturl().rsplit("/", 1)[-1]                      # redirect ends in .../tag/<tag>

def newer_tag():
    try: t = latest_tag(); return t if ver(t) > ver(VERSION) else None
    except Exception: return None    # offline / blocked: just don't update; the app must never fail over this

def swap_exe(tag, exe):
    """Download the release's sticks.exe and swap it in. A running exe can be renamed but not overwritten."""
    new = exe + ".new"
    with urllib.request.urlopen(f"https://github.com/{REPO}/releases/download/{tag}/sticks.exe", timeout=30) as r, \
         open(new, "wb") as f: shutil.copyfileobj(r, f)
    with open(new, "rb") as f: ok = f.read(2) == b"MZ"               # sanity: it's a Windows program, not an error page
    if not ok: os.remove(new); raise ValueError("download was not a program")
    os.replace(exe, exe + ".old")
    try: os.replace(new, exe)
    except OSError: os.replace(exe + ".old", exe); raise


def explorer_hwnds():
    out = set()
    @ENUM
    def cb(h, _):
        b = ctypes.create_unicode_buffer(64); u32.GetClassNameW(h, b, 64)
        if b.value == "CabinetWClass" and u32.IsWindowVisible(h): out.add(h)
        return True
    u32.EnumWindows(cb, 0)
    return out

def desktop():
    b = ctypes.create_unicode_buffer(260); ctypes.windll.shell32.SHGetFolderPathW(0, 0x10, 0, 0, b); return b.value

def folders(p):
    """Real (non-link, non-hidden) subfolders of p. Only folders: never files, shortcuts or programs."""
    try:
        return [e.path for e in os.scandir(p) if e.is_dir(follow_symlinks=False)
                and not e.name.startswith(('.', '$')) and not getattr(e.stat(), 'st_file_attributes', 0) & 6]
    except OSError:
        return []

class LVITEM(ctypes.Structure):                    # start of the Win32 LVITEMW; the rest stays zero in the remote buffer
    _fields_ = [("mask", wt.UINT), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int), ("state", wt.UINT),
                ("stateMask", wt.UINT), ("pszText", ctypes.c_void_p), ("cchTextMax", ctypes.c_int),
                ("iImage", ctypes.c_int), ("lParam", ctypes.c_void_p)]

def desktop_icons():
    """[(folder_path, x, y)]: screen positions of the Desktop's folder icons, read from Explorer's own icon list
    (a list-view in another process, so we borrow a bit of its memory to ask it questions).
    Any failure (icons hidden, odd shell, access denied) returns [] and the figures just skip walking up to icons."""
    try:
        dv = u32.FindWindowExW(u32.FindWindowW("Progman", None), None, "SHELLDLL_DefView", None)
        w = None
        while not dv:                                  # with some wallpaper setups the icons live under a WorkerW
            w = u32.FindWindowExW(None, w, "WorkerW", None)
            if not w: return []
            dv = u32.FindWindowExW(w, None, "SHELLDLL_DefView", None)
        lv = u32.FindWindowExW(dv, None, "SysListView32", None)
        if not lv or not u32.IsWindowVisible(lv): return []
        names = {os.path.basename(p): p for p in folders(desktop())}
        pid = wt.DWORD(); u32.GetWindowThreadProcessId(lv, ctypes.byref(pid))
        proc = k32.OpenProcess(0x0438, False, pid.value)         # VM operation | read | write | query
        if not proc: return []
        def ask(msg, i, lp):                           # SendMessage that gives up instead of hanging if Explorer is stuck
            res = ctypes.c_size_t()
            return res.value if u32.SendMessageTimeoutW(lv, msg, i, lp, 2, 300, ctypes.byref(res)) else None
        out, mem = [], None
        try:
            mem = k32.VirtualAllocEx(proc, None, 4096, 0x3000, 4)   # zeroed scratch: POINT at +0, LVITEM at +64, text at +512
            for i in range(min(ask(0x1004, 0, 0) or 0, 300)):       # LVM_GETITEMCOUNT
                it, pt, buf = LVITEM(1, i, 0, 0, 0, mem + 512, 260, 0, 0), wt.POINT(), ctypes.create_string_buffer(520)
                k32.WriteProcessMemory(proc, mem + 64, ctypes.byref(it), ctypes.sizeof(it), None)
                if ask(0x1073, i, mem + 64) is None or ask(0x1010, i, mem) is None: continue   # GETITEMTEXTW, GETITEMPOSITION
                k32.ReadProcessMemory(proc, mem, ctypes.byref(pt), 8, None)
                k32.ReadProcessMemory(proc, mem + 512, buf, 520, None)
                name = buf.raw.decode("utf-16-le").split("\0")[0]
                if name in names and u32.ClientToScreen(lv, ctypes.byref(pt)):
                    out.append((names[name], pt.x + 40, pt.y + 35))     # +40,+35: roughly the middle of the icon
        finally:
            if mem: k32.VirtualFreeEx(proc, mem, 0, 0x8000)
            k32.CloseHandle(proc)
        return out
    except Exception:
        return []

class Explorer:
    """Opens Desktop folders in File Explorer, then subfolders of the last one. Rare (every 1.5-3 min), and its
    windows are closed again after ~3 min."""
    def __init__(s):
        s.path = s.pending = s.icons = s.opener = None; s.opened = {}      # opened: hwnd -> time opened
        s.last, s.gap, s.icons_t = time.time(), 45, 0
    def ready(s): return EXPLORE and time.time() - s.last > s.gap
    def desk_target(s):
        """(path, x, y) of a visible Desktop folder icon near enough a screen edge to climb up to, else None."""
        if time.time() - s.icons_t > 30: s.icons, s.icons_t = desktop_icons(), time.time()
        ok = [i for i in s.icons or [] if min(i[1]-VX, VX+VW-i[1]) <= 260 and uncovered(i[1], i[2])]   # edge-reachable, in view
        return random.choice(ok) if ok else None
    def open_next(s, path=None, fig=None):
        """fig = the figure doing it: once the new window shows up it's sent to play on it."""
        s.last, s.gap, s.opener = time.time(), random.uniform(90, 180), fig
        if not path:                                   # no icon given: go one level deeper from the last folder
            subs = folders(s.path) if s.path else []
            path = random.choice(subs) if subs and random.random() < .7 else random.choice(folders(desktop()) or [None])
        s.path = path
        if path: s.pending = explorer_hwnds(); os.startfile(path)
    def poll(s):
        if s.pending is None and not s.opened: return
        s.opened = {h: t for h, t in s.opened.items() if u32.IsWindow(h)}
        if s.pending is not None:                     # find the window Explorer just made so we can close it later
            new = explorer_hwnds() - s.pending
            if new:
                s.opened.update({h: time.time() for h in new}); s.pending = None
                if s.opener: s.opener.visit, s.opener = min(new), None
            elif time.time() - s.last > 5: s.pending = None
        for h, t in list(s.opened.items()):           # nobody got round to closing it (or >2 open): close it ourselves
            if time.time() - t > 320 or len(s.opened) > 2: s.close(h)
    def due(s): return [h for h, t in s.opened.items() if time.time() - t > 200]   # old enough for a figure to go and click its X
    def close(s, h): u32.PostMessageW(h, 0x10, 0, 0); s.opened.pop(h, None)        # WM_CLOSE; only ever called on windows we opened

EXPL = Explorer()


# ---- physics / behaviour ---------------------------------------------------------------
def jump_frames(dh):
    """Frames for a jump to come DOWN onto a ledge dh px higher, or None if too high."""
    a, b = GRAV/2, -JUMP_V + GRAV/2
    d = b*b - 4*a*dh
    return None if d < 0 else (-b + math.sqrt(d)) / (2*a)

def covered(h, x, y, W):
    """Is point (x,y) of window h hidden behind a window above it? Uses every visible window (Z), maximised included."""
    for w in (Z or W):
        if w[0] == h: return False
        if w[1] < x < w[3] and w[2] <= y < w[4]: return True
    return False

def uncovered(x, y):
    """Is screen point (x,y) in plain view, i.e. no window (maximised and fullscreen ones included) over it?"""
    return not any(w[1] < x < w[3] and w[2] <= y < w[4] for w in Z)

def tops(W, x):
    """(y, hwnd) of window top edges at column x that you can actually see, plus the floor."""
    for h, l, t, r, b in W:
        if l+6 < x < r-6 and t <= FLOOR and not covered(h, x, t+2, W): yield t, h
    yield FLOOR, None

class Fig:
    def __init__(s, color):
        s.c, s.x, s.y, s.vx, s.vy, s.t, s.acc = color, random.uniform(VX+80, VX+VW-80), VY+40, 0, 0, 0, 0
        s.dir, s.mode, s.act, s.sup, s.supl, s.goal, s.timer = random.choice((-1, 1)), 'air', 'walk', None, 0, None, 0
        s.tgt = s.pw = s.cw = s.top = s.cross = s.ic = s.visit = s.hail = s.after = None; s.wall, s.pushback, s.kill = 1, 0, False
        # cross=(window, gap side); pushback=dir to shove next; ic=(folder, x, y) desktop icon we're climbing to;
        # visit=folder window just opened, to go play on; hail=window we're heading for, to dance on once there;
        # after=(window, goal kind) to start once we're standing on that window; kill=the stomp closes (not minimises)

    def step(s, W):
        s.t += 1
        (s.fly if s.mode == 'air' else s.climb if s.mode == 'climb' else s.ground)(W)

    def drop(s, vx=0, vy=0): s.mode, s.sup, s.vx, s.vy = 'air', None, vx, vy

    def land(s, y, h):
        g = s.goal
        s.y, s.vx, s.vy, s.mode, s.sup, s.act, s.timer = y, 0, 0, 'ground', h, 'walk', 20
        r = h and live(h); s.supl = r[0] if r else 0
        if g and g[0] == 'jump': s.goal = (g[2], h, None) if g[1] == h and g[2] in ('press', 'open') else None
        if s.cross:                                    # got on top of the window we pushed: cross to the gap side
            if h == s.cross[0]: s.goal = ('walkoff', h, s.cross[1])
            else: s.cross = None
        if s.after:                                    # reached the window we were heading for: start the job
            if h == s.after[0]: s.goal = (s.after[1], h, None)
            s.after = None
        if s.goal: s.timer = 400
        if s.pushback:                                 # dropped into the gap: now shove it back the other way
            if h is None: s.dir, s.timer = s.pushback, 300
            s.pushback = 0
        if h and h == s.hail: s.act, s.timer, s.hail = 'dance', 90, None   # made it onto the folder window: celebrate

    def fly(s, W):
        py = s.y; s.vy += GRAV; s.y += s.vy
        s.x = min(max(s.x + s.vx, VX+10), VX+VW-10)
        if s.vy > 0:
            hit = [(y, h) for y, h in tops(W, s.x) if py-1 <= y <= s.y]
            if hit: s.land(*min(hit, key=lambda p: p[0]))
            elif s.y > FLOOR: s.land(FLOOR, None)
        if s.y < VY+70: s.y, s.vy = VY+70, max(s.vy, 0)

    def climb(s, W):
        R = live(s.cw) if s.cw else None
        if s.cw and not R: return s.drop()
        if s.timer:                                    # hanging at the top: let go when done
            s.timer -= 1
            if s.ic and s.timer == 30:                         # reached the icon: open that folder, if it's still in view
                if uncovered(s.ic[1], s.ic[2]): EXPL.open_next(s.ic[0], s)
                s.ic = None
            if not s.timer: s.drop(-s.wall*4, -9)
            return
        s.y -= CLIMB
        if s.cw:
            s.x = R[0]-6 if s.wall > 0 else R[2]+6
            if s.y <= R[1]+4:                          # over the top
                s.land(R[1], s.cw); s.x = R[0]+20 if s.wall > 0 else R[2]-20
        else:
            s.x = VX+8 if s.wall < 0 else VX+VW-8
            if s.y <= s.top: s.timer = 50

    def ground(s, W):
        if s.sup:
            R = live(s.sup)
            if not R or covered(s.sup, s.x, R[1]+2, W): return s.drop()   # window gone, or someone put a window over it
            s.x += R[0]-s.supl; s.supl = R[0]; s.y = R[1]      # ride the window
            lo, hi = R[0]+10, R[2]-10
        else: s.y, lo, hi = FLOOR, VX+10, VX+VW-10
        if s.visit and s.act == 'walk' and not s.goal: return s.think(W)   # the folder we opened is up: go to it now
        if s.act == 'walk':
            if s.goal and s.steer(): return
            s.x += s.dir*SPEED
            if not lo <= s.x <= hi:
                s.x = min(max(s.x, lo), hi)
                if s.goal and s.goal[0] == 'walkoff':   # hop down into the gap
                    s.pushback, s.cross, s.goal = -s.dir, None, None
                    s.x += s.dir*20                     # step fully past the edge so we don't re-land on the top
                    return s.drop(s.dir*1.5)
                if s.sup and random.random() < .4: return s.drop(s.dir*2.5, -4)
                if not s.sup and random.random() < .6:
                    s.mode, s.wall, s.cw, s.timer, s.top = 'climb', s.dir, None, 0, VY+90 + random.random()*(FLOOR-VY-240)
                    return
                s.dir = -s.dir
            elif not s.sup and not s.goal: s.shove(W)
        elif s.act == 'push': s.push()
        elif s.act == 'stomp' and s.timer == 22 and s.tgt: (EXPL.close if s.kill else minimize)(s.tgt)
        elif s.act == 'knock' and s.timer == 22: EXPL.open_next(None, s)
        s.timer -= 1
        if s.timer <= 0: s.think(W)

    def shove(s, W):
        for h, l, t, r, b in W:
            gap = l - s.x if s.dir > 0 else s.x - r
            if (0 < gap < 10 and t < s.y-15 and b > s.y-45 and r-l < VW*.9 and not down()
                    and not covered(h, l if s.dir > 0 else r, s.y-30, W)):   # only shove windows we can see
                s.act, s.pw, s.acc, s.timer = 'push', h, 0, random.randint(60, 150); return

    def push(s):
        R = live(s.pw)
        lim = R and (R[2]+PUSH > VX+VW-GAP if s.dir > 0 else R[0]-PUSH < VX+GAP)   # pushed as far as it goes
        if not R or down() or lim:
            gap = R and (VX+VW-R[2] if s.dir > 0 else R[0]-VX)
            if lim and gap >= 40 and random.random() < .6: s.cross = (s.pw, s.dir)   # maybe go round the back
            s.timer = 0; return
        s.acc += s.dir*PUSH; d = int(s.acc); s.acc -= d
        if d: move(s.pw, d)
        R = live(s.pw) or R
        s.x = R[0]-8 if s.dir > 0 else R[2]+8

    def steer(s):
        """Walk toward the goal, act on arrival. True = this frame is used up."""
        k, h, w = s.goal
        R = live(h) if h else None
        if h and not R: s.goal = None; return False
        if k == 'cursor':
            tx = cursor()[0]
            if abs(tx - s.x) < 40: s.act, s.timer, s.goal = 'wave', 70, None; return True
        elif k == 'jump':
            n = jump_frames(s.y - R[1]); tx = min(max(s.x, R[0]+30), R[2]-30)
            if n is None: s.goal = None; return False
            if abs(tx - s.x) < 100:
                s.dir = 1 if tx >= s.x else -1
                s.vx, s.vy, s.mode, s.sup = (tx - s.x)/n, -JUMP_V, 'air', None; return True
        elif k == 'climb':
            tx = (R[0]-14 if w > 0 else R[2]+14) if h else (VX+8 if w < 0 else VX+VW-8)
            if abs(tx - s.x) < SPEED+.5:
                s.mode, s.wall, s.cw, s.goal, s.timer = 'climb', w, h, None, 0
                s.top = VY+90 + random.random()*(FLOOR-VY-240); return True
        elif k == 'desk':                                      # walk to the screen edge, climb to the icon's height
            if not s.ic or not uncovered(s.ic[1], s.ic[2]): s.goal = s.ic = None; return False   # something covered it
            tx = VX+8 if w < 0 else VX+VW-8
            if abs(tx - s.x) < SPEED+.5:
                s.mode, s.wall, s.cw, s.goal, s.timer = 'climb', w, None, None, 0
                s.top = min(max(s.ic[2]+40, VY+90), FLOOR-60); return True   # feet ~40px below the icon's middle
        elif k == 'close':                                     # on top of an old folder window: walk to its X and stomp it
            if s.sup != h: s.goal = None; return False
            tx = min(max(R[2]-24, R[0]+20), R[2]-20)           # the close button is the far-right ~46px of the title bar
            if abs(tx - s.x) < SPEED+.5: s.act, s.timer, s.tgt, s.kill, s.goal = 'stomp', 45, h, True, None; return True
        elif k == 'open':                                      # on top of a folder window we opened: knock to open a subfolder
            if s.sup != h: s.goal = None; return False
            tx = (R[0]+R[2])/2
            if abs(tx - s.x) < SPEED+.5: s.act, s.timer, s.goal = 'knock', 45, None; return True
        elif k == 'walkoff':
            if s.sup != h: s.goal = None; return False
            s.dir = w; return False
        elif k == 'press':
            if s.sup != h: s.goal = None; return False
            tx = min(max(R[2]-115, R[0]+20), R[2]-20)          # about where the minimise button is
            if abs(tx - s.x) < SPEED+.5: s.act, s.timer, s.tgt, s.kill, s.goal = 'stomp', 45, h, False, None; return True
        s.dir = 1 if tx > s.x else -1
        return False

    def think(s, W):
        y = s.y; s.goal = s.ic = None; s.act = 'walk'; s.timer = random.randint(60, 200); s.dir = random.choice((-1, 1))
        if s.cross:                                    # just pushed a window to its limit: get on top of it
            w = next((w for w in W if w[0] == s.cross[0]), None)
            if w and 20 < y-w[2] <= JUMP_MAX: s.goal, s.timer = ('jump', w[0], None), 400; return
            if w and y-w[2] > JUMP_MAX and w[4] > y-20:
                s.goal, s.timer = ('climb', w[0], 1 if s.x < (w[1]+w[3])/2 else -1), 400; return
            s.cross = None
        exposed = lambda w: any(h == w[0] for _, h in tops(W, min(max(s.x, w[1]+30), w[3]-30)))
        old = EXPL.due()
        for w in W:                                    # a folder window we opened is overdue: go and click its X
            if w[0] in old and exposed(w):
                if s.sup == w[0]: s.goal, s.timer = ('close', w[0], None), 400; return
                if y-w[2] > 20:
                    s.after, s.timer = (w[0], 'close'), 400
                    s.goal = ('jump', w[0], None) if y-w[2] <= JUMP_MAX else ('climb', w[0], 1 if s.x < (w[1]+w[3])/2 else -1)
                    return
        if s.visit:                                    # we just opened a folder window: jump onto it, or climb its side
            w = next((w for w in W if w[0] == s.visit), None); s.visit = None
            if w and exposed(w) and y-w[2] > 20:
                s.hail, s.timer = w[0], 400
                s.goal = ('jump', w[0], None) if y-w[2] <= JUMP_MAX else ('climb', w[0], 1 if s.x < (w[1]+w[3])/2 else -1)
                return
        ledges = [w for w in W if 20 < y-w[2] <= JUMP_MAX and w[0] != s.sup and exposed(w)]
        walls = [w for w in W if y-w[2] > JUMP_MAX and w[2] > VY+90 and w[4] > y-20 and exposed(w)]
        blockers = [w for w in W if w[2] < y-15 and w[4] > y-45 and w[3]-w[1] < VW*.9
                    and not covered(w[0], w[1] if s.x < (w[1]+w[3])/2 else w[3], y-30, W)]
        opts = ['walk']*3 + ['dance', 'wave', 'cursor']
        if not s.sup: opts += ['edge'] + ['push']*2*bool(blockers)
        ours = [w for w in ledges if w[0] in EXPL.opened]          # a folder window we opened, close enough to jump onto
        ic = EXPL.desk_target() if EXPL.ready() and not s.sup else None   # a visible Desktop folder icon to climb up to
        if EXPL.ready() and (ours or ic): opts += ['explore']
        if ledges: opts += ['jump']*3
        if walls: opts += ['climb']*2
        k = random.choice(opts)
        if k == 'dance': s.act, s.timer = 'dance', random.randint(60, 120)
        elif k == 'wave': s.act, s.timer = 'wave', 70
        elif k == 'explore':
            if ours and (not ic or random.random() < .5): s.goal, s.timer = ('jump', random.choice(ours)[0], 'open'), 400
            else: s.ic, s.goal, s.timer = ic, ('desk', None, -1 if ic[1]-VX < VX+VW-ic[1] else 1), 400
        elif k == 'cursor': s.goal, s.timer = ('cursor', None, None), 400
        elif k == 'edge': s.goal, s.timer = ('climb', None, -1 if s.x < VX+VW/2 else 1), 400
        elif k == 'jump':
            then = 'press' if PRESS_MINIMIZE and random.random() < .4 else None
            s.goal, s.timer = ('jump', random.choice(ledges)[0], then), 400
        elif k == 'climb':
            w = random.choice(walls); s.goal, s.timer = ('climb', w[0], 1 if s.x < (w[1]+w[3])/2 else -1), 400
        elif k == 'push':
            w = min(blockers, key=lambda w: abs((w[1]+w[3])/2 - s.x))
            s.dir, s.timer = (1 if (w[1]+w[3])/2 > s.x else -1), 300

    def draw(s, cv):
        a = s.mode if s.mode != 'ground' else s.act
        d = s.wall if a == 'climb' else s.dir
        x, y = s.x - VX, s.y - VY
        sw, cs = math.sin(s.t*.35), math.cos(s.t*.35)
        lean = hop = 0
        G, A = [(8, 4), (-8, -4)], [(10, 15), (-10, -5)]          # leg / arm (upper, lower) angles; 0=down, 180=up
        if a in ('walk', 'push'):
            k = 1 if a == 'walk' else .6
            G = [(30*k*sw, 30*k*sw - 40*max(0, cs)), (-30*k*sw, -30*k*sw - 40*max(0, -cs))]
            A = [(-35*sw, -35*sw+25), (35*sw, 35*sw+25)]
            if a == 'push': A, lean = [(85, 88), (80, 84)], 10
        elif a == 'dance':
            w = math.sin(s.t*.45); hop, lean = 6*abs(w), 4*w
            A, G = [(130+35*w, 150), (130-35*w, 150)], [(15*w, 15*w - 20*abs(w)), (-15*w, -15*w)]
        elif a == 'wave':
            w = math.sin(s.t*.5); A = [(150+20*w, 160+30*w), (10, 15)]
        elif a == 'air':
            w = math.sin(s.t*.8); A, G = [(145+20*w, 165), (-145-20*w, -165)], [(30, -15), (-25, -45)]
        elif a == 'climb':
            c = 0 if s.timer else math.sin(s.t*.3); lean = 6
            A, G = [(160+15*c, 175), (160-15*c, 175)], [(50+25*c, 5), (50-25*c, 5)]
            if s.timer and s.ic: A[1] = (95, 100)                # hanging by one hand, reaching out to the icon
        elif a in ('stomp', 'knock'):
            hop = 14*abs(math.sin(s.timer*.25)); A, G = [(130, 140), (125, 135)], [(10, 0), (-10, 0)]
        hx, hy = x, y - 30 - hop
        nx, ny = x + d*lean, hy - 28
        def limb(p, ang, ln):
            m = (p[0] + d*math.sin(math.radians(ang[0]))*ln, p[1] + math.cos(math.radians(ang[0]))*ln)
            return [*p, *m, m[0] + d*math.sin(math.radians(ang[1]))*ln, m[1] + math.cos(math.radians(ang[1]))*ln]
        for pts in ([hx, hy, nx, ny], *(limb((hx, hy), g, 15) for g in G), *(limb((nx, ny+3), q, 11) for q in A)):
            cv.create_line(*pts, width=5, fill=s.c, capstyle='round', joinstyle='round')
        cv.create_oval(nx-9, ny-20, nx+9, ny-2, outline=s.c, width=4, fill='white')


# ---- app --------------------------------------------------------------------------------
def start_tray(stop):
    """System-tray icon with a Quit item. Optional: skipped (Ctrl+Shift+Q still works) if pystray/Pillow are missing."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    img = Image.new("RGBA", (64, 64)); d = ImageDraw.Draw(img); c = "#e03030"
    for seg in ((32, 24, 32, 44), (32, 30, 17, 40), (32, 30, 47, 40), (32, 44, 20, 60), (32, 44, 44, 60)):
        d.line(seg, fill=c, width=5)
    d.ellipse((22, 3, 42, 23), outline=c, width=4, fill="white")
    icon = pystray.Icon("sticks", img, f"Stick Figures {VERSION}",
                        pystray.Menu(pystray.MenuItem("Quit", lambda icon, item: stop.append(1))))
    icon.run_detached()
    return icon

def main():
    import tkinter as tk
    n = int(next((a for a in sys.argv[1:] if a.isdigit()), 3))
    root = tk.Tk(); root.overrideredirect(True)
    root.geometry(f"{VW}x{VH}{VX:+d}{VY:+d}")
    root.attributes('-topmost', True); root.attributes('-transparentcolor', KEY)
    cv = tk.Canvas(root, width=VW, height=VH, bg=KEY, highlightthickness=0); cv.pack()
    root.update()
    hw = u32.GetAncestor(root.winfo_id(), 2)
    # layered | transparent (click-through) | toolwindow (no taskbar) | noactivate (no focus theft)
    u32.SetWindowLongW(hw, -20, u32.GetWindowLongW(hw, -20) | 0x80000 | 0x20 | 0x80 | 0x08000000)
    OWN.update({hw, root.winfo_id()})
    figs, W, frame, found = [Fig(COLORS[i % len(COLORS)]) for i in range(n)], [], [0], []
    if AUTO_UPDATE and getattr(sys, "frozen", False):      # only the built .exe self-updates, never a source checkout
        try: os.remove(sys.executable + ".old")
        except OSError: pass
        threading.Thread(target=lambda: found.append(newer_tag()), daemon=True).start()

    def offer(tag):
        from tkinter import messagebox
        if not messagebox.askyesno("Stick Figures", f"Version {tag} is available (you have {VERSION}).\nDownload it and restart now?"):
            return False
        try: swap_exe(tag, sys.executable)
        except Exception as e:
            messagebox.showerror("Stick Figures", f"Update failed: {e}\nDownload it from https://github.com/{REPO}/releases")
            return False
        os.startfile(sys.executable); return True

    stop = []                                              # the tray's Quit item appends here (it runs on another thread)
    tray = start_tray(stop)

    def quit():
        if tray: tray.stop()                               # else the icon lingers in the tray until hovered
        root.destroy()

    def tick():
        frame[0] += 1
        if found and (t := found.pop()) and offer(t): return quit()
        if frame[0] % 6 == 1: m, a = list_windows(); W[:], Z[:] = m, a; EXPL.poll()
        if frame[0] % 90 == 0: root.attributes('-topmost', True)
        if stop or all(u32.GetAsyncKeyState(k) & 0x8000 for k in (0x11, 0x10, 0x51)): return quit()
        cv.delete('all')
        for f in figs: f.step(W); f.draw(cv)
        root.after(33, tick)
    tick(); root.mainloop()


def selftest():
    global live, move, minimize, cursor, down, VX, VY, VW, VH, FLOOR, EXPL
    class FakeExplorer:                           # no real folders opened in the test
        n = desk = closed = 0
        opened = {4: 0}                           # pretend window 4 is a folder window we opened
        due = lambda s: [4] if s.closed < 3 else []      # ...that is overdue for closing until figures have closed it 3 times
        def close(s, h): s.closed += 1
        ready = lambda s: True
        desk_target = lambda s: ("C:/folder", 60, 500)   # a desktop icon near the left edge
        def open_next(s, path=None, fig=None):
            s.n += 1; s.desk += bool(path)
            if fig: fig.visit = 4                 # as if window 4 had just appeared for them to visit
    EXPL = FakeExplorer()
    import io, tempfile

    class Resp(io.BytesIO):                        # stands in for a urlopen() response
        def __init__(s, body=b"", url=""): super().__init__(body); s.u = url
        def geturl(s): return s.u
    mode = ["ok"]
    def fake(req, timeout=0):
        u = req if isinstance(req, str) else req.full_url
        if "api.github.com" in u:
            if mode[0] == "ok": return Resp(b'{"tag_name": "v9.1"}')
            raise urllib.error.HTTPError(u, 403, "rate limit exceeded", {}, None)
        if "/releases/download/" in u: return Resp(b"MZ-new-exe")
        return Resp(url=f"https://github.com/{REPO}/releases/tag/v9.2")
    urllib.request.urlopen = fake
    assert latest_tag() == "v9.1"                  # API works
    mode[0] = "limited"
    assert latest_tag() == "v9.2"                  # rate limited -> redirect fallback
    assert ver("v1.10") > ver("1.9") and ver("releases") == () and newer_tag() == "v9.2"
    with tempfile.TemporaryDirectory() as d:       # swap keeps the old exe aside and puts the new one in place
        exe = os.path.join(d, "sticks.exe"); open(exe, "wb").write(b"MZ-old")
        swap_exe("v9.2", exe)
        assert open(exe, "rb").read() == b"MZ-new-exe" and open(exe + ".old", "rb").read() == b"MZ-old"
    VX, VY, VW, VH, FLOOR = 0, 0, 1920, 1080, 1040   # the fake windows below assume this screen
    for dh in (20, 80, 130):                      # closed-form jump time must match frame-by-frame physics
        y, vy, n = 0, -JUMP_V, 0
        while True:
            n += 1; vy += GRAV; y += vy
            if vy > 0 and y >= -dh: break
        assert abs(n - jump_frames(dh)) <= 1.5, (dh, n, jump_frames(dh))
    assert jump_frames(JUMP_MAX+60) is None

    wins = {1: [100, 700, 500, 1040], 2: [800, 500, 1200, 900], 3: [300, 100, 600, 200], 4: [1300, 950, 1700, 1040]}
    live = lambda h: tuple(wins[h]) if h in wins else None
    def move(h, dx): wins[h][0] += dx; wins[h][2] += dx
    minimize = lambda h: wins.pop(h, None) if h != 4 else None   # window 4 stands in for a folder window that stays put
    cursor, down = (lambda: (900, 500)), (lambda: 0)
    random.seed(1)
    figs, seen, hops = [Fig(COLORS[i]) for i in range(4)], set(), [0]
    _land = Fig.land
    hails = [0]
    def land(s, y, h):                            # count hop-into-gap landings and arrivals on a folder window
        hops[0] += bool(s.pushback and h is None); hails[0] += bool(h and h == s.hail); _land(s, y, h)
    Fig.land = land
    class CV:                                     # draw() must run too
        def create_line(self, *a, **k): assert len(a) == 6 or len(a) == 4
        def create_oval(self, *a, **k): pass
    for i in range(30000):
        if i % 6 == 0: W = [(h, *w) for h, w in wins.items()]
        for f in figs:
            f.step(W); f.draw(CV()); seen.add(f.mode if f.mode != 'ground' else f.act)
            assert VX <= f.x <= VX+VW and VY <= f.y <= FLOOR+1, (f.x, f.y, f.mode, f.act)
    for w in wins.values(): assert VX <= w[0] and w[2] <= VX+VW
    assert {'air', 'climb', 'push', 'stomp'} <= seen, seen
    assert hops[0], "no figure ever hopped into the gap and pushed back"
    Z[:] = [(99, 0, 0, 1920, 1040)] + [(h, *w) for h, w in wins.items()]   # a maximised window over everything
    assert list(tops([(h, *w) for h, w in wins.items()], 1000)) == [(FLOOR, None)], "walked on a hidden window"
    assert covered(2, 1000, 502, [(h, *w) for h, w in wins.items()])
    Z[:] = []
    assert EXPL.desk, "no figure ever climbed up to a desktop icon"
    assert EXPL.n > EXPL.desk, "no figure ever opened a subfolder from a folder window"
    assert hails[0], "no figure ever went to play on a folder it opened"
    assert EXPL.closed, "no figure ever climbed up and clicked a folder window's X"
    Z[:] = [(98, 0, 0, 1920, 1040)]               # a maximised window covers the desktop: icons must count as out of reach
    assert not uncovered(60, 500)
    Z[:] = []
    print("ok; behaviours seen:", sorted(seen), "gap hops:", hops[0], "windows left:", len(wins))


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
