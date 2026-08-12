import ctypes
import ctypes.wintypes
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

import psutil
import pystray
import serial
import serial.tools.list_ports
from PIL import Image, ImageDraw

APP_NAME = "LCD525Panel"
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


def _config_dir():
    if IS_WINDOWS:
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_NAME)


APPDIR = _config_dir()
CFG_PATH = os.path.join(APPDIR, "config.json")
LOG_PATH = os.path.join(APPDIR, "app.log")

GPU_BIN = shutil.which("nvidia-smi")


def _subprocess_kw(**kw):
    """Common subprocess kwargs; creationflags exists only on Windows."""
    if IS_WINDOWS:
        kw.setdefault("creationflags", 0x08000000)
    return kw

ERROR_KW = ("ошибк", "не удалось", "не удаетс", "сбой", "проблема")
ERROR_EN = ("error", "exception", "fail", "fatal", "crash", "fault",
            "invalid", "stop", "bug")
WARNING_KW = ("предупрежден", "внимание", "предупр", "вним")
WARNING_EN = ("warn", "caution", "alert")

DEFAULT_BUZZER = {
    "enabled": True,
    "led": True,
    "watch": True,
    "pin": 2,
    "volume": 80,
    "frequency": 2500,
    "speed": 100,
    "type": "passive",
    "error": "rapid",
    "warning": "long",
    "info": "short",
}

DEFAULT_DISPLAY = {
    "page_delay": 5.0,
    "pages": [],
}

DEFAULT_USB = {
    "sound": True,
    "connect": "chime_up",
    "disconnect": "chime_down",
}

DEFAULT_DISKS = {
    "sound": True,
    "connect": "chime_up",
    "disconnect": "chime_down",
}

DEFAULT_DELAYS = {
    "gpu": 5.0,
    "ctemp": 10.0,
    "windows": 2.0,
    "usb": 3.0,
    "disks": 5.0,
    "retry": 3.0,
    "alert_min": 2.0,
}

NO_SOUND = ("off", "None")

SOUND_PATTERNS = ["None", "off", "short", "long", "double", "triple", "rapid",
                  "chime_up", "chime_down", "siren", "wake",
                  "buzz", "notification", "success", "sad", "alarm",
                  "rising", "falling", "doorbell", "sos", "fanfare",
                  "game_over"]
AUDIBLE_PATTERNS = [p for p in SOUND_PATTERNS if p not in NO_SOUND]

_state = {
    "lock": threading.Lock(),
    "wlock": threading.Lock(),
    "running": True,
    "cpu": 0.0,
    "ram_gb": 0.0,
    "cpu_freq": None,
    "gpu": None,
    "gtemp": None,
    "gpu_freq": None,
    "ctemp": None,
    "ip": None,
    "procs": 0,
    "net_tx": 0.0,
    "net_rx": 0.0,
    "ser": None,
    "port": None,
    "connected": False,
    "interval": 1.0,
    "com_port": "",
    "last_line": "",
    "last_try": 0.0,
    "buzzer": dict(DEFAULT_BUZZER),
    "display": dict(DEFAULT_DISPLAY),
    "usb": dict(DEFAULT_USB),
    "disks": dict(DEFAULT_DISKS),
    "delays": dict(DEFAULT_DELAYS),
    "ignored": [],
    "self_pid": os.getpid(),
    "last_alert": 0.0,
}


def log(msg):
    try:
        os.makedirs(APPDIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def load_config():
    cfg = {"com_port": "", "interval": 1.0,
           "buzzer": dict(DEFAULT_BUZZER), "display": dict(DEFAULT_DISPLAY),
           "usb": dict(DEFAULT_USB), "disks": dict(DEFAULT_DISKS),
           "delays": dict(DEFAULT_DELAYS), "ignored": []}
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            cfg.update(stored)
            merged_buz = dict(DEFAULT_BUZZER)
            if isinstance(cfg.get("buzzer"), dict):
                merged_buz.update(cfg["buzzer"])
            cfg["buzzer"] = merged_buz
            merged_disp = dict(DEFAULT_DISPLAY)
            if isinstance(cfg.get("display"), dict):
                merged_disp.update(cfg["display"])
            cfg["display"] = merged_disp
            merged_usb = dict(DEFAULT_USB)
            if isinstance(cfg.get("usb"), dict):
                merged_usb.update(cfg["usb"])
            cfg["usb"] = merged_usb
            merged_disks = dict(DEFAULT_DISKS)
            if isinstance(cfg.get("disks"), dict):
                merged_disks.update(cfg["disks"])
            cfg["disks"] = merged_disks
            merged_delays = dict(DEFAULT_DELAYS)
            if isinstance(cfg.get("delays"), dict):
                merged_delays.update({k: max(float(v), 0.1)
                                      for k, v in cfg["delays"].items()
                                      if isinstance(v, (int, float))})
            cfg["delays"] = merged_delays
            if not isinstance(cfg.get("ignored"), list):
                cfg["ignored"] = []
            cfg["ignored"] = [str(s).strip() for s in cfg["ignored"] if str(s).strip()]
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(APPDIR, exist_ok=True)
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log("save_config: %s" % e)


def find_arduino_port(cfg_port):
    ports = [p.device for p in serial.tools.list_ports.comports()]
    if cfg_port and cfg_port in ports:
        return cfg_port
    for p in serial.tools.list_ports.comports():
        if p.vid in (0x2341, 0x2A03, 0x1A86):
            return p.device
    return None


def open_serial(port):
    try:
        s = serial.Serial(port, 9600, timeout=0.2, write_timeout=0.5)
        try:
            s.dtr = False
            s.rts = False
        except Exception:
            pass
        s.reset_input_buffer()
        return s
    except Exception as e:
        log("open %s: %s" % (port, e))
        return None


def try_connect():
    if _state["ser"] is not None and _state["ser"].is_open:
        return
    now = time.time()
    if now - _state["last_try"] < _state["delays"]["retry"]:
        return
    _state["last_try"] = now
    port = find_arduino_port(_state["com_port"])
    if not port:
        if _state["port"] is not None:
            _state["port"] = None
        return
    s = open_serial(port)
    with _state["lock"]:
        _state["ser"] = s
        _state["port"] = port
        _state["connected"] = s is not None
    if s is not None:
        log("connected: %s" % port)
        send_buzzer_config()


def send_line(line):
    s = _state["ser"]
    if s is None or not s.is_open:
        with _state["lock"]:
            _state["connected"] = False
        try_connect()
        return
    try:
        with _state["wlock"]:
            s.write((line + "\n").encode("ascii", "replace"))
            s.flush()
        _state["connected"] = True
    except Exception as e:
        log("write error: %s" % e)
        try:
            s.close()
        except Exception:
            pass
        with _state["lock"]:
            _state["ser"] = None
            _state["connected"] = False


def send_buzzer_config():
    b = _state["buzzer"]
    send_line("P:%d" % int(b.get("pin", 2)))
    send_line("V:%d" % int(b.get("volume", 80)))
    send_line("F:%d" % int(b.get("frequency", 2500)))
    send_line("S:%d" % int(b.get("speed", 100)))
    send_line("T:%s" % ("passive" if b.get("type", "active") == "passive" else "active"))
    send_line("L:%s" % ("on" if b.get("led", True) else "off"))


def read_gpu():
    if not GPU_BIN:
        return None, None, None
    try:
        p = subprocess.run(
            [GPU_BIN, "--query-gpu=utilization.gpu,temperature.gpu,clocks.sm",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, **_subprocess_kw())
        lines = (p.stdout or "").strip().splitlines()
        if not lines:
            return None, None, None
        parts = [x.strip() for x in lines[0].split(",")]
        if len(parts) < 3 or "N/A" in (parts[0], parts[1]):
            return None, None, None
        try:
            freq = int(parts[2])
        except Exception:
            freq = None
        return int(parts[0]), int(parts[1]), freq
    except Exception:
        return None, None, None


def read_cpu_temp():
    if IS_WINDOWS:
        return _read_cpu_temp_win()
    return _read_cpu_temp_linux()


_last_hwmon_probe = 0.0
_hwmon_avail = None


def _hwmon_available():
    global _last_hwmon_probe, _hwmon_avail
    now = time.time()
    if _hwmon_avail is not None and now - _last_hwmon_probe < 30.0:
        return _hwmon_avail
    _last_hwmon_probe = now
    avail = False
    try:
        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "").lower()
            if "librehardwaremonitor" in n or "openhardwaremonitor" in n:
                avail = True
                break
    except Exception:
        pass
    _hwmon_avail = avail
    return avail


def _read_cpu_temp_win():
    if not _hwmon_available():
        return None
    for ns in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
        try:
            script = (
                "$s = Get-WmiObject -Namespace '%s' -Class Sensor -ErrorAction SilentlyContinue; "
                "$s | Where-Object { $_.SensorType -eq 'Temperature' -and "
                "$_.Name -match 'CPU|Core|Tctl|Package|Die' } | "
                "ForEach-Object { $_.Value } | Measure-Object -Maximum | "
                "Select-Object -ExpandProperty Maximum" % ns
            )
            p = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=6, **_subprocess_kw())
            text = (p.stdout or "").strip()
            if text:
                v = float(text)
                if v > 0:
                    return v
        except Exception:
            continue
    return None


def _read_cpu_temp_linux():
    cpu_drivers = ("coretemp", "k10temp", "zenpower", "k8temp", "cpu_thermal",
                   "cpu-thermal", "soc_thermal", "cpu")
    vals = []
    try:
        for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            hwname = ""
            try:
                with open(os.path.join(hw, "name")) as f:
                    hwname = f.read().strip().lower()
            except Exception:
                pass
            is_cpu = hwname in cpu_drivers
            for tfile in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
                try:
                    with open(tfile) as f:
                        v = float(f.read().strip()) / 1000.0
                except Exception:
                    continue
                if v <= 0 or v > 110:
                    continue
                label = ""
                try:
                    with open(tfile[:-6] + "_label") as f:
                        label = f.read().strip().lower()
                except Exception:
                    pass
                if is_cpu or any(s in label for s in ("core", "tctl", "package",
                                                      "tdie", "cpu")):
                    vals.append(v)
    except Exception:
        pass
    if vals:
        return max(vals)
    if shutil.which("sensors"):
        try:
            p = subprocess.run(["sensors", "-j"], capture_output=True,
                               text=True, timeout=4, **_subprocess_kw())
            text = (p.stdout or "")
            if re.search(r'"(k10temp|coretemp|zenpower|cpu_thermal|k8temp)"', text):
                m = re.findall(r"temp\d_input\s*:\s*([\d.]+)", text)
                if m:
                    return max(float(x) for x in m)
        except Exception:
            pass
    return None


LEGACY_DISPLAY_KEYS = [
    ("cpu", "C"), ("ram", "R"), ("cpu_freq", "F"),
    ("gpu", "G"), ("gpu_temp", "T"), ("gpu_freq", "M"), ("cpu_temp", "c"),
]


def default_page():
    cells = []
    for i, (key, label) in enumerate(LEGACY_DISPLAY_KEYS):
        if key == "cpu_temp":
            continue
        cells.append({"key": key, "x": (0, 5, 11)[i % 3], "y": i // 3,
                      "label": label})
    return {"name": "Page 1", "delay": None, "cells": cells}


def _to_int(v, dflt):
    try:
        return int(v)
    except (TypeError, ValueError):
        return dflt


def _to_float(v, dflt):
    try:
        return float(v)
    except (TypeError, ValueError):
        return dflt


def build_display_pages(disp):
    if not isinstance(disp, dict):
        disp = {}
    pages = disp.get("pages")
    if isinstance(pages, list) and pages:
        out = []
        for p in pages:
            if not isinstance(p, dict):
                continue
            cells = []
            for c in p.get("cells", []) if isinstance(p.get("cells"), list) else []:
                if not isinstance(c, dict):
                    continue
                key = str(c.get("key") or "").strip()
                if key not in RENDER_TARGETS:
                    continue
                cells.append({
                    "key": key,
                    "x": max(0, min(15, _to_int(c.get("x"), 0))),
                    "y": 1 if _to_int(c.get("y"), 0) > 0 else 0,
                    "label": str(c.get("label") or ""),
                })
            if not cells:
                continue
            name = str(p.get("name") or "").strip() or "Page %d" % (len(out) + 1)
            delay = _to_float(p.get("delay"), None)
            out.append({"name": name, "delay": delay, "cells": cells})
        if out:
            return {"page_delay": _to_float(disp.get("page_delay"),
                                            DEFAULT_DISPLAY["page_delay"]),
                    "pages": out}
    cells = []
    i = 0
    for key, label in LEGACY_DISPLAY_KEYS:
        if disp.get(key, False):
            y = i // 3
            if y > 1:
                log("display: %s does not fit the 16x2 grid, skipped" % key)
                i += 1
                continue
            cells.append({"key": key, "x": (0, 5, 11)[i % 3], "y": y,
                          "label": label})
            i += 1
    if not cells:
        cells = default_page()["cells"]
    return {"page_delay": DEFAULT_DISPLAY["page_delay"],
            "pages": [{"name": "Page 1", "delay": None, "cells": cells}]}


def _fmt_cpu():
    with _state["lock"]:
        return "%d%%" % int(round(_state["cpu"]))


def _fmt_ram():
    with _state["lock"]:
        return "%.1fG" % _state["ram_gb"]


def _fmt_cpu_freq():
    with _state["lock"]:
        cf = _state["cpu_freq"]
    return ("%.1fG" % (cf / 1000.0)) if cf else "NA"


def _fmt_gpu():
    with _state["lock"]:
        g = _state["gpu"]
    return ("%d%%" % int(round(g))) if g is not None else "NA"


def _fmt_gpu_temp():
    with _state["lock"]:
        t = _state["gtemp"]
    return ("%dC" % int(round(t))) if t is not None else "NA"


def _fmt_gpu_freq():
    with _state["lock"]:
        f = _state["gpu_freq"]
    return ("%dM" % f) if f else "NA"


def _fmt_cpu_temp():
    with _state["lock"]:
        t = _state["ctemp"]
    return ("%dC" % int(round(t))) if t is not None else "NA"


def _fmt_ip():
    with _state["lock"]:
        ip = _state.get("ip")
    return ip or "NA"


def _fmt_procs():
    with _state["lock"]:
        return "%d" % _state.get("procs", 0)


def _fmt_net_tx():
    with _state["lock"]:
        return "%.1fM" % _state.get("net_tx", 0.0)


def _fmt_net_rx():
    with _state["lock"]:
        return "%.1fM" % _state.get("net_rx", 0.0)


RENDER_TARGETS = {
    "cpu": _fmt_cpu,
    "ram": _fmt_ram,
    "cpu_freq": _fmt_cpu_freq,
    "gpu": _fmt_gpu,
    "gpu_temp": _fmt_gpu_temp,
    "gpu_freq": _fmt_gpu_freq,
    "cpu_temp": _fmt_cpu_temp,
    "ip": _fmt_ip,
    "procs": _fmt_procs,
    "net_tx": _fmt_net_tx,
    "net_rx": _fmt_net_rx,
}


def render_page(page):
    rows = [[" "] * 16, [" "] * 16]
    if not isinstance(page, dict):
        return ["", ""]
    for cell in page.get("cells", []) if isinstance(page.get("cells"), list) else []:
        if not isinstance(cell, dict):
            continue
        fmt = RENDER_TARGETS.get(cell.get("key"))
        if fmt is None:
            continue
        text = str(cell.get("label") or "") + fmt()
        if not text:
            continue
        x = max(0, min(15, _to_int(cell.get("x"), 0)))
        y = 1 if _to_int(cell.get("y"), 0) > 0 else 0
        for ch in text:
            if x > 15:
                break
            rows[y][x] = ch
            x += 1
    return ["".join(r).rstrip() for r in rows]


def build_line():
    pages = _state["display"].get("pages")
    page = (pages or [default_page()])[0]
    rows = render_page(page)
    return "D:" + rows[0] + "|" + rows[1]


def read_ip():
    try:
        for _name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET:
                    ip = a.address
                    if (ip.startswith("127.") or ip.startswith("169.254.")
                            or ip.startswith("0.")):
                        continue
                    return ip
    except Exception:
        pass
    return None


def _page_keys():
    keys = set()
    for p in _state["display"].get("pages", []):
        if not isinstance(p, dict):
            continue
        for c in p.get("cells", []) if isinstance(p.get("cells"), list) else []:
            if isinstance(c, dict) and c.get("key"):
                keys.add(c.get("key"))
    return keys


def _needs_gpu():
    return GPU_BIN is not None and bool(_page_keys() & {"gpu", "gpu_temp", "gpu_freq"})


def _needs_ctemp():
    return "cpu_temp" in _page_keys()


def _needs_net():
    return bool(_page_keys() & {"net_tx", "net_rx"})


def _needs_ip():
    return "ip" in _page_keys()


def sampler_loop():
    psutil.cpu_percent(None)
    last_gpu = 0.0
    last_ctemp = 0.0
    last_ip = 0.0
    net_prev = None
    net_prev_t = 0.0
    while _state["running"]:
        try:
            cpu = psutil.cpu_percent(interval=0.8)
            vm = psutil.virtual_memory()
            cf = psutil.cpu_freq()
            cf_mhz = cf.current if cf is not None else None
            with _state["lock"]:
                _state["cpu"] = cpu
                _state["ram_gb"] = vm.used / 1073741824.0
                _state["cpu_freq"] = cf_mhz
                _state["procs"] = len(psutil.pids())
            now = time.time()
            if _needs_net():
                cur = psutil.net_io_counters()
                if net_prev is not None:
                    dt = max(now - net_prev_t, 0.001)
                    with _state["lock"]:
                        _state["net_tx"] = ((cur.bytes_sent - net_prev.bytes_sent)
                                            / dt / 1048576.0)
                        _state["net_rx"] = ((cur.bytes_recv - net_prev.bytes_recv)
                                            / dt / 1048576.0)
                net_prev = cur
                net_prev_t = now
            else:
                net_prev = None
            if _needs_ip() and now - last_ip >= 30.0:
                last_ip = now
                with _state["lock"]:
                    _state["ip"] = read_ip()
            if not _state["connected"]:
                continue
            if now - last_gpu >= _state["delays"]["gpu"] and _needs_gpu():
                last_gpu = now
                gpu, gtemp, gfreq = read_gpu()
                with _state["lock"]:
                    _state["gpu"] = gpu
                    _state["gtemp"] = gtemp
                    _state["gpu_freq"] = gfreq
            if now - last_ctemp >= _state["delays"]["ctemp"] and _needs_ctemp():
                last_ctemp = now
                ctemp = read_cpu_temp()
                with _state["lock"]:
                    _state["ctemp"] = ctemp
        except Exception as e:
            log("sampler: %s" % e)
            time.sleep(0.5)


def worker_loop():
    tick = 0
    page_idx = 0
    last_switch = time.time()
    while _state["running"]:
        try:
            disp = _state["display"]
            pages = disp.get("pages")
            if not pages:
                pages = [default_page()]
            page = pages[page_idx % len(pages)]
            pd = page.get("delay") or disp.get("page_delay", 5.0)
            if time.time() - last_switch >= pd:
                page_idx += 1
                last_switch = time.time()
                page = pages[page_idx % len(pages)]
            rows = render_page(page)
            line = "D:" + rows[0] + "|" + rows[1]
            _state["last_line"] = line
            send_line(line)
            with _state["lock"]:
                cpu = _state["cpu"]
            send_line("X:%d" % int(round(cpu)))
            tick += 1
            if tick % 5 == 0:
                send_buzzer_config()
        except Exception as e:
            log("worker: %s" % e)
        time.sleep(_state["interval"])


_EnumWindowsProc = None
if IS_WINDOWS:
    _EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)


def _init_user32():
    u = ctypes.windll.user32
    u.EnumWindows.argtypes = [_EnumWindowsProc, ctypes.wintypes.LPARAM]
    u.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
    u.IsWindowVisible.restype = ctypes.c_bool
    u.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetClassNameW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.GetWindowThreadProcessId.argtypes = [
        ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
    u.GetWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
    u.GetWindow.restype = ctypes.wintypes.HWND
    u.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.wintypes.LONG
    return u


_WIN_USER32 = _init_user32() if IS_WINDOWS else None

BENIGN_CLASSES = (
    "tooltips_class32", "#32768", "Shell_TrayWnd", "Progman", "Button",
    "ComboBox", "Edit", "Static", "ScrollBar", "IME", "MSCTFIME UI",
    "DummyDWMListenerWindow", "EdgeUiInputTopWndClass",
    "Windows.UI.Core.CoreWindow",
)


def enum_windows():
    result = []

    def _cb(hwnd, lparam):
        if _WIN_USER32.IsWindowVisible(hwnd):
            result.append(hwnd)
        return True

    _WIN_USER32.EnumWindows(_EnumWindowsProc(_cb), 0)
    return result


def win_title(hwnd):
    n = _WIN_USER32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _WIN_USER32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    _WIN_USER32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def win_pid(hwnd):
    pid = ctypes.wintypes.DWORD()
    _WIN_USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def match_kw(low, keywords):
    return any(k in low for k in keywords)


def match_en(low, words):
    return any(re.search(r"\b" + re.escape(w), low) for w in words)


def is_dialog_like(hwnd, cls, title):
    if cls == "#32770" or cls.startswith("#"):
        return True
    if cls in BENIGN_CLASSES or not title.strip():
        return False
    owner = _WIN_USER32.GetWindow(hwnd, 4)  # GW_OWNER
    if not owner:
        return False
    style = _WIN_USER32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
    return bool(style & 0x00C00000)  # WS_CAPTION (border or dialog frame)


def classify_window(title, cls, is_dialog, wtype=""):
    if not title or not title.strip():
        return None
    if wtype:
        up = wtype.upper()
        if any(t in up for t in ("DESKTOP", "DOCK", "SPLASH")):
            return None
    low = title.lower()
    if match_kw(low, ERROR_KW) or match_en(low, ERROR_EN):
        return "error"
    if match_kw(low, WARNING_KW) or match_en(low, WARNING_EN):
        return "warning"
    if is_dialog:
        return "info"
    return None


def send_alert(pattern, use_led):
    if pattern in NO_SOUND:
        return
    if use_led:
        send_line("A:" + pattern)
    else:
        send_line("B:" + pattern)


_LINUX_WIN_BACKEND = None


def _detect_linux_window_backend():
    global _LINUX_WIN_BACKEND
    if _LINUX_WIN_BACKEND is not None:
        return _LINUX_WIN_BACKEND
    try:
        from Xlib import display  # noqa: F401
        _LINUX_WIN_BACKEND = "xlib"
    except Exception:
        _LINUX_WIN_BACKEND = "xdotool" if shutil.which("xdotool") else None
    return _LINUX_WIN_BACKEND


_XLIB_DISP = None


def _xlib_list_windows():
    global _XLIB_DISP
    try:
        from Xlib import X, display
    except Exception:
        return []
    if _XLIB_DISP is None:
        try:
            _XLIB_DISP = display.Display()
        except Exception:
            return []
    d = _XLIB_DISP
    res = []
    try:
        root = d.screen().root
        atom_wname = d.intern_atom("_NET_WM_NAME")
        atom_wmclass = d.intern_atom("WM_CLASS")
        atom_wtype = d.intern_atom("_NET_WM_WINDOW_TYPE")
        atom_pid = d.intern_atom("_NET_WM_PID")

        def _prop_text(prop):
            if not prop:
                return ""
            v = prop.value
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace")
            return ""

        def walk(w):
            try:
                children = w.query_tree().children
            except Exception:
                children = []
            for c in children:
                try:
                    attrs = c.get_attributes()
                except Exception:
                    attrs = None
                if attrs is not None and attrs.map_state == X.IsViewable:
                    title = ""
                    try:
                        title = _prop_text(c.get_property(atom_wname, "UTF8_STRING", 0, 512))
                    except Exception:
                        pass
                    if not title:
                        try:
                            title = c.get_wm_name() or ""
                        except Exception:
                            title = ""
                    cls = ""
                    try:
                        cls = _prop_text(c.get_property(atom_wmclass, X.STRING, 0, 256))
                    except Exception:
                        pass
                    wtype = ""
                    try:
                        prop = c.get_property(atom_wtype, X.ATOM, 0, 16)
                        if prop and prop.value:
                            wtype = " ".join(
                                str(d.get_atom_name(a)) for a in prop.value
                                if isinstance(a, int))
                    except Exception:
                        pass
                    pid = None
                    try:
                        prop = c.get_property(atom_pid, X.CARDINAL, 0, 8)
                        if prop and prop.value:
                            pid = int(prop.value[0])
                    except Exception:
                        pass
                    res.append({
                        "id": int(c.id), "pid": pid, "title": title,
                        "cls": cls,
                        "dialog": any(t in wtype.upper() for t in ("DIALOG", "NOTIFICATION")),
                        "wtype": wtype,
                    })
                walk(c)
        walk(root)
    except Exception:
        _XLIB_DISP = None
        return []
    return res


def _xdotool_list_windows():
    res = []
    try:
        out = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "."],
                             capture_output=True, text=True, timeout=5, **_subprocess_kw())
        for wid in (out.stdout or "").split():
            title = ""
            cls = ""
            try:
                title = subprocess.run(["xdotool", "getwindowname", wid],
                                       capture_output=True, text=True, timeout=3,
                                       **_subprocess_kw()).stdout.strip()
            except Exception:
                pass
            try:
                cls = subprocess.run(["xdotool", "getwindowclassname", wid],
                                     capture_output=True, text=True, timeout=3,
                                     **_subprocess_kw()).stdout.strip()
            except Exception:
                pass
            try:
                wid_int = int(wid, 16) if wid.lower().startswith("0x") else int(wid)
            except Exception:
                wid_int = len(res)
            res.append({"id": wid_int, "pid": None, "title": title,
                        "cls": cls, "dialog": False, "wtype": ""})
    except Exception:
        pass
    return res


def _linux_list_windows():
    backend = _detect_linux_window_backend()
    if backend == "xlib":
        return _xlib_list_windows()
    if backend == "xdotool":
        return _xdotool_list_windows()
    return []


NO_TITLE_CLASSES = ("tooltips_class32", "#32768", "IME", "MSCTFIME UI",
                    "Progman", "Shell_TrayWnd", "DummyDWMListenerWindow",
                    "EdgeUiInputTopWndClass", "Windows.UI.Core.CoreWindow")


def iter_windows():
    if IS_WINDOWS:
        for hwnd in enum_windows():
            cls = win_class(hwnd)
            if cls in NO_TITLE_CLASSES:
                continue
            pid = win_pid(hwnd)
            title = win_title(hwnd)
            yield {"id": hwnd, "pid": pid, "title": title, "cls": cls,
                   "dialog": is_dialog_like(hwnd, cls, title), "wtype": ""}
    else:
        for w in _linux_list_windows():
            yield w


def alert_loop():
    prev = set()
    warned = False
    was_connected = False
    while _state["running"]:
        try:
            delays = _state["delays"]
            if not _state["connected"]:
                prev = set()
                was_connected = False
                time.sleep(max(3.0, delays["windows"] + 1.0))
                continue
            buz = _state["buzzer"]
            if not buz.get("watch", True):
                prev = set()
                was_connected = False
                time.sleep(delays["windows"])
                continue
            if not IS_WINDOWS and not warned:
                if _detect_linux_window_backend() is None:
                    log("window monitor: X11/xdotool not available, window alerts off")
                warned = True
            own = _state["self_pid"]
            ignored = [s.lower() for s in _state.get("ignored", [])]
            cur = {}
            for w in iter_windows():
                if w.get("pid") == own:
                    continue
                title = w.get("title", "") or ""
                low = title.lower()
                if any(s in low for s in ignored):
                    continue
                kind = classify_window(title, w.get("cls", ""),
                                       w.get("dialog", False), w.get("wtype", ""))
                if kind is not None:
                    cur[w["id"]] = (kind, title)
            if not was_connected:
                prev = set(cur)
                was_connected = True
                time.sleep(delays["windows"])
                continue
            new = {k: v for k, v in cur.items() if k not in prev}
            if new:
                now = time.time()
                if now - _state["last_alert"] >= delays["alert_min"]:
                    _state["last_alert"] = now
                    wid = min(new)
                    kind, title = new[wid]
                    if kind == "error":
                        pattern = buz.get("error", "rapid")
                    elif kind == "warning":
                        pattern = buz.get("warning", "long")
                    else:
                        pattern = buz.get("info", "short")
                    log("alert %s: %s" % (kind, title))
                    if buz.get("enabled", True):
                        send_alert(pattern, buz.get("led", True))
            prev = set(cur)
        except Exception as e:
            log("alert: %s" % e)
        time.sleep(_state["delays"]["windows"])


if IS_WINDOWS:
    class _SPDevInfoData(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.wintypes.DWORD),
                    ("ClassGuid", ctypes.wintypes.BYTE * 16),
                    ("DevInst", ctypes.wintypes.DWORD),
                    ("Reserved", ctypes.c_void_p)]
else:
    _SPDevInfoData = None


def _init_setupapi():
    sa = ctypes.windll.setupapi
    cm = ctypes.windll.cfgmgr32
    sa.SetupDiGetClassDevsW.argtypes = [
        ctypes.c_void_p, ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.HWND, ctypes.wintypes.DWORD]
    sa.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    sa.SetupDiEnumDeviceInfo.argtypes = [
        ctypes.c_void_p, ctypes.wintypes.DWORD,
        ctypes.POINTER(_SPDevInfoData)]
    sa.SetupDiEnumDeviceInfo.restype = ctypes.wintypes.BOOL
    sa.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    sa.SetupDiDestroyDeviceInfoList.restype = ctypes.wintypes.BOOL
    cm.CM_Get_Device_IDW.argtypes = [
        ctypes.wintypes.DWORD, ctypes.wintypes.LPWSTR,
        ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
    cm.CM_Get_Device_IDW.restype = ctypes.wintypes.DWORD
    return sa, cm


_SA, _CM = _init_setupapi() if IS_WINDOWS else (None, None)


def _win_list_usb_device_ids():
    if _SA is None:
        return None
    h = _SA.SetupDiGetClassDevsW(
        None, None, None, 0x00000002 | 0x00000004)
    if not h or h == ctypes.c_void_p(-1).value:
        return None
    ids = set()
    try:
        idx = 0
        while True:
            info = _SPDevInfoData()
            info.cbSize = ctypes.sizeof(_SPDevInfoData)
            if not _SA.SetupDiEnumDeviceInfo(h, idx, ctypes.byref(info)):
                break
            buf = ctypes.create_unicode_buffer(200)
            if _CM.CM_Get_Device_IDW(info.DevInst, buf, 200, 0) == 0:
                did = buf.value.upper()
                if "VID_" in did and "ROOT_HUB" not in did:
                    ids.add(did)
            idx += 1
    finally:
        _SA.SetupDiDestroyDeviceInfoList(h)
    return ids


def _linux_list_usb_devices():
    ids = set()
    base = "/sys/bus/usb/devices"
    try:
        for name in os.listdir(base):
            d = os.path.join(base, name)
            try:
                with open(os.path.join(d, "idVendor")) as f:
                    vid = f.read().strip().lower()
                with open(os.path.join(d, "idProduct")) as f:
                    pid = f.read().strip().lower()
            except Exception:
                continue
            if vid and pid and vid != "1d6b":  # 1d6b = Linux root hub
                ids.add("%s:%s@%s" % (vid, pid, name))
    except Exception:
        return None
    return ids


def list_usb_device_ids():
    if IS_WINDOWS:
        return _win_list_usb_device_ids()
    return _linux_list_usb_devices()


def usb_monitor_loop():
    prev = None
    while _state["running"]:
        try:
            dly = _state["delays"]["usb"]
            if not _state["connected"]:
                prev = None
                time.sleep(dly)
                continue
            cur = list_usb_device_ids()
            if cur is None:
                time.sleep(dly)
                continue
            if prev is not None:
                usb = _state.get("usb", DEFAULT_USB)
                added = cur - prev
                removed = prev - cur
                if added:
                    log("usb added: %s" % sorted(added)[0])
                    pat = usb.get("connect", "chime_up")
                    if usb.get("sound", True) and pat not in NO_SOUND:
                        send_alert(pat, False)
                if removed:
                    log("usb removed: %s" % sorted(removed)[0])
                    pat = usb.get("disconnect", "chime_down")
                    if usb.get("sound", True) and pat not in NO_SOUND:
                        send_alert(pat, False)
            prev = cur
        except Exception as e:
            log("usb: %s" % e)
        time.sleep(_state["delays"]["usb"])


def _win_list_sata_disks():
    try:
        script = (
            "Get-CimInstance Win32_DiskDrive | "
            "Where-Object { $_.InterfaceType -eq 'SATA' } | "
            "ForEach-Object { $_.Model + '|' + $_.SerialNumber }"
        )
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=6, **_subprocess_kw())
        ids = set()
        for line in (p.stdout or "").splitlines():
            line = line.strip()
            if line:
                ids.add(line)
        return ids
    except Exception:
        return None


def _linux_list_sata_disks():
    ids = set()
    try:
        for name in os.listdir("/sys/block"):
            if not (name.startswith("sd") or name.startswith("hd")):
                continue
            try:
                with open(os.path.join("/sys/block", name, "removable")) as f:
                    if f.read().strip() != "0":
                        continue
            except Exception:
                continue
            model = ""
            try:
                with open(os.path.join("/sys/block", name, "device", "model")) as f:
                    model = f.read().strip()
            except Exception:
                pass
            ids.add("%s|%s" % (name, model))
    except Exception:
        return None
    return ids


def list_sata_disks():
    if IS_WINDOWS:
        return _win_list_sata_disks()
    return _linux_list_sata_disks()


def disk_monitor_loop():
    prev = None
    while _state["running"]:
        try:
            dly = _state["delays"]["disks"]
            if not _state["connected"]:
                prev = None
                time.sleep(dly)
                continue
            cur = list_sata_disks()
            if cur is None:
                time.sleep(dly)
                continue
            if prev is not None:
                disks = _state.get("disks", DEFAULT_DISKS)
                added = cur - prev
                removed = prev - cur
                if added:
                    log("sata added: %s" % sorted(added)[0])
                    pat = disks.get("connect", "chime_up")
                    if disks.get("sound", True) and pat not in NO_SOUND:
                        send_alert(pat, False)
                if removed:
                    log("sata removed: %s" % sorted(removed)[0])
                    pat = disks.get("disconnect", "chime_down")
                    if disks.get("sound", True) and pat not in NO_SOUND:
                        send_alert(pat, False)
            prev = cur
        except Exception as e:
            log("disks: %s" % e)
        time.sleep(_state["delays"]["disks"])


def make_icon_image():
    img = Image.new("RGB", (64, 64), (20, 22, 26))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 6, 60, 58), radius=8, outline=(70, 200, 150), width=3)
    for x, y, h in ((8, 50, 12), (16, 50, 24), (24, 50, 18),
                    (32, 50, 30), (40, 50, 22), (48, 50, 38)):
        d.rounded_rectangle((x, y - h, x + 6, y), radius=2, fill=(110, 230, 130))
    return img


def _autostart_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "autostart")


def _app_command():
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return os.path.abspath(sys.argv[0])
    return '"%s" "%s"' % (sys.executable, os.path.abspath(sys.argv[0]))


def autostart_enabled():
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, APP_NAME)
            return True
        except Exception:
            return False
    return os.path.exists(os.path.join(_autostart_dir(), APP_NAME + ".desktop"))


def set_autostart(enabled):
    if IS_WINDOWS:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        try:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, '"%s"' % sys.executable)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
        return
    d = _autostart_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, APP_NAME + ".desktop")
    if enabled:
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=%s\n"
            "Comment=LCD525 Panel monitor\n"
            "Exec=%s\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            "Hidden=false\n" % (APP_NAME, _app_command())
        )
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            log("set_autostart: %s" % e)
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def toggle_autostart(icon, item):
    set_autostart(not autostart_enabled())
    state = "Autostart enabled" if autostart_enabled() else "Autostart disabled"
    icon.notify(state, APP_NAME)


def force_reconnect(icon=None, item=None):
    s = _state["ser"]
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
    with _state["lock"]:
        _state["ser"] = None
        _state["connected"] = False
    try_connect()


def show_status(icon, item):
    conn = "OK" if _state["connected"] else "NO LINK"
    port = _state["port"] or "not found"
    icon.notify("Port: %s | Link: %s\n%s" % (port, conn, _state["last_line"]), APP_NAME)


def open_folder(icon=None, item=None):
    try:
        os.makedirs(APPDIR, exist_ok=True)
        if IS_WINDOWS:
            os.startfile(APPDIR)
        else:
            subprocess.Popen(["xdg-open", APPDIR],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log("open_folder: %s" % e)


_LOCK_PATH = None


def shutdown(icon=None, item=None):
    _state["running"] = False
    s = _state["ser"]
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
    global _LOCK_PATH
    if _LOCK_PATH:
        try:
            os.remove(_LOCK_PATH)
        except OSError:
            pass
    if icon is not None:
        icon.stop()
    os._exit(0)


def open_settings():
    threading.Thread(target=_settings_thread, daemon=True).start()


def _settings_thread():
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception as e:
        log("settings: tkinter unavailable: %s" % e)
        return
    buz = dict(_state["buzzer"])
    disp_cfg = build_display_pages(dict(_state["display"]))
    pages = disp_cfg["pages"]
    root = tk.Tk()
    root.title(APP_NAME + " - Settings")
    root.resizable(False, False)
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=6, pady=6)

    preview_canvases = []
    preview_cur = {"page": pages[0] if pages else default_page()}

    def make_preview_canvas(parent):
        c = tk.Canvas(parent, width=284, height=64, bg="#0b0f0d",
                      highlightthickness=0)
        preview_canvases.append(c)
        return c

    def draw_preview(canv, rows):
        canv.delete("all")
        pad = 12
        cw, ch = 17, 22
        canv.create_rectangle(0, 0, pad * 2 + 16 * cw,
                              pad * 2 + 2 * (ch + 4),
                              fill="#111", outline="")
        for r in range(2):
            line = rows[r] if r < len(rows) else ""
            for x in range(16):
                chch = line[x] if x < len(line) else " "
                x0 = pad + x * cw
                y0 = pad + r * (ch + 4)
                on = chch != " "
                canv.create_rectangle(x0, y0, x0 + cw, y0 + ch,
                                      fill="#2c9f5a" if on else "#1a3327",
                                      outline="#0a2415")
                if on:
                    canv.create_text(x0 + cw // 2, y0 + ch // 2, text=chch,
                                     fill="#06230f", font=("Courier New", 10, "bold"))

    f1 = ttk.Frame(nb, padding=8)
    nb.add(f1, text="Connection")
    ports = [p.device for p in serial.tools.list_ports.comports()]
    var_port = tk.StringVar(value=_state["com_port"] or "auto")
    var_int = tk.StringVar(value=str(_state["interval"]))
    tk.Label(f1, text="Port:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
    ttk.Combobox(f1, textvariable=var_port, values=["auto"] + ports,
                 width=16, state="readonly").grid(row=0, column=1, padx=4, pady=4)
    tk.Label(f1, text="Update interval (s):").grid(row=1, column=0, sticky="w", padx=4, pady=4)
    tk.Entry(f1, textvariable=var_int, width=18).grid(row=1, column=1, padx=4, pady=4)
    ttk.Separator(f1, orient="horizontal").grid(
        row=3, column=0, columnspan=2, sticky="we", padx=4, pady=(10, 2))
    make_preview_canvas(f1).grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=4)

    f2 = ttk.Frame(nb, padding=8)
    nb.add(f2, text="Notifications")
    patterns = SOUND_PATTERNS
    var_watch = tk.BooleanVar(value=buz["watch"])
    var_buz = tk.BooleanVar(value=buz["enabled"])
    var_led = tk.BooleanVar(value=buz["led"])
    var_pin = tk.StringVar(value=str(buz["pin"]))
    var_vol = tk.IntVar(value=buz["volume"])
    var_freq = tk.IntVar(value=buz.get("frequency", 2500))
    var_speed = tk.IntVar(value=buz.get("speed", 100))
    var_type = tk.StringVar(value=buz["type"])
    var_err = tk.StringVar(value=buz["error"])
    var_warn = tk.StringVar(value=buz["warning"])
    var_info = tk.StringVar(value=buz["info"])
    r = 0
    ttk.Checkbutton(f2, text="Watch for error/warning windows", variable=var_watch).grid(
        row=r, column=0, columnspan=4, sticky="w", padx=4, pady=4)
    r += 1
    ttk.Checkbutton(f2, text="Buzzer", variable=var_buz).grid(
        row=r, column=0, columnspan=4, sticky="w", padx=4, pady=4)
    r += 1
    ttk.Checkbutton(f2, text="Built-in LED (pin 13)", variable=var_led).grid(
        row=r, column=0, columnspan=4, sticky="w", padx=4, pady=4)
    r += 1
    tk.Label(f2, text="Buzzer pin:").grid(
        row=r, column=0, sticky="w", padx=4, pady=4)
    ttk.Spinbox(f2, from_=2, to=13, textvariable=var_pin, width=5).grid(
        row=r, column=1, sticky="w", padx=4, pady=4)
    r += 1
    tk.Label(f2, text="Buzzer type:").grid(row=r, column=0, sticky="w", padx=4, pady=4)
    ttk.Combobox(f2, textvariable=var_type, values=["active", "passive"],
                 width=10, state="readonly").grid(row=r, column=1, sticky="w", padx=4, pady=4)
    r += 1
    tk.Label(f2, text="Volume (%):").grid(row=r, column=0, sticky="w", padx=4, pady=4)
    tk.Scale(f2, from_=0, to=100, orient="horizontal", variable=var_vol,
             length=140).grid(row=r, column=1, columnspan=2, sticky="w", padx=4, pady=4)
    r += 1
    tk.Label(f2, text="Frequency (Hz):").grid(row=r, column=0, sticky="w", padx=4, pady=4)
    tk.Scale(f2, from_=100, to=5000, resolution=50, orient="horizontal",
             variable=var_freq, length=140).grid(row=r, column=1, columnspan=2,
                                                 sticky="w", padx=4, pady=4)
    r += 1
    tk.Label(f2, text="Speed (%):").grid(row=r, column=0, sticky="w", padx=4, pady=4)
    tk.Scale(f2, from_=25, to=400, resolution=5, orient="horizontal",
             variable=var_speed, length=140).grid(row=r, column=1, columnspan=2,
                                                  sticky="w", padx=4, pady=4)
    r += 1
    tk.Label(f2, text="Error alert:").grid(row=r, column=0, sticky="w", padx=4, pady=4)
    ttk.Combobox(f2, textvariable=var_err, values=patterns, width=8,
                 state="readonly").grid(row=r, column=1, sticky="w", padx=4, pady=4)
    r += 1
    tk.Label(f2, text="Warning alert:").grid(row=r, column=0, sticky="w", padx=4, pady=4)
    ttk.Combobox(f2, textvariable=var_warn, values=patterns, width=8,
                 state="readonly").grid(row=r, column=1, sticky="w", padx=4, pady=4)
    r += 1
    tk.Label(f2, text="Other window alert:").grid(row=r, column=0, sticky="w", padx=4, pady=4)
    ttk.Combobox(f2, textvariable=var_info, values=patterns, width=8,
                 state="readonly").grid(row=r, column=1, sticky="w", padx=4, pady=4)

    def make_test(pattern):
        def do_test():
            b = {"enabled": var_buz.get(), "led": var_led.get(),
                 "pin": int(var_pin.get() or 2),
                 "volume": max(0, min(100, var_vol.get())),
                 "frequency": max(100, min(10000, var_freq.get())),
                 "speed": max(25, min(400, var_speed.get())),
                 "type": var_type.get()}
            old = _state["buzzer"]
            _state["buzzer"] = b
            send_buzzer_config()
            _state["buzzer"] = old
            if b["enabled"] and pattern not in NO_SOUND:
                if b["led"]:
                    send_line("A:" + pattern)
                else:
                    send_line("B:" + pattern)
        return do_test

    r += 1
    tk.Label(f2, text="Test:").grid(row=r, column=0, sticky="w", padx=4, pady=6)
    test_frame = ttk.Frame(f2)
    test_frame.grid(row=r, column=1, columnspan=3, sticky="w", padx=4, pady=6)
    for idx, pat in enumerate(AUDIBLE_PATTERNS):
        ttk.Button(test_frame, text=pat, width=9,
                   command=make_test(pat)).grid(
            row=idx // 3, column=idx % 3, padx=2, pady=2)

    r += 1
    tk.Label(f2, text="Ignore titles (no alert):").grid(
        row=r, column=0, columnspan=4, sticky="w", padx=4, pady=(10, 0))
    r += 1
    fign = ttk.Frame(f2)
    fign.grid(row=r, column=0, columnspan=4, sticky="w", padx=4, pady=4)
    var_ign_entry = tk.StringVar()
    list_ign = tk.Listbox(fign, width=32, height=5)
    list_ign.grid(row=1, column=0, rowspan=2, padx=(0, 4))
    entry_ign = tk.Entry(fign, textvariable=var_ign_entry, width=32)
    entry_ign.grid(row=0, column=0, padx=(0, 4), sticky="we")
    ttk.Button(fign, text="Add", width=8,
               command=lambda: _add_ignore()).grid(row=0, column=1, pady=(0, 2))
    ttk.Button(fign, text="Remove", width=8,
               command=lambda: _remove_ignore()).grid(row=1, column=1)

    def _add_ignore():
        t = var_ign_entry.get().strip()
        low = t.lower()
        if t and not any(low == x.lower() for x in list_ign.get(0, "end")):
            list_ign.insert("end", t)
        var_ign_entry.set("")

    def _remove_ignore():
        sel = list_ign.curselection()
        if sel:
            list_ign.delete(sel[0])

    for t in _state.get("ignored", []):
        list_ign.insert("end", t)

    r += 1
    ttk.Separator(f2, orient="horizontal").grid(
        row=r, column=0, columnspan=4, sticky="we", padx=4, pady=(10, 2))
    r += 1
    make_preview_canvas(f2).grid(row=r, column=0, columnspan=4, sticky="w", padx=4, pady=4)

    f3 = ttk.Frame(nb, padding=8)
    nb.add(f3, text="Display")
    keys = sorted(RENDER_TARGETS.keys())

    def refresh_previews():
        page = preview_cur["page"]
        rows = render_page(page)
        for canv in preview_canvases:
            draw_preview(canv, rows)

    var_page_delay = tk.StringVar(value=str(disp_cfg.get("page_delay", 5.0)))
    var_page_delay.trace_add("write", lambda *a: refresh_previews())

    def load_page():
        sel = list_pages.curselection()
        if not sel:
            return
        p = pages[sel[0]]
        preview_cur["page"] = p
        var_page_name.set(p.get("name") or "")
        var_page_delay_per.set(str(p.get("delay") or "") if p.get("delay") else "")
        tree_cells.delete(*tree_cells.get_children())
        for i, c in enumerate(p.get("cells", [])):
            tree_cells.insert("", "end", iid=str(i), values=(
                c.get("label") or "", c.get("key") or "",
                c.get("x", 0), c.get("y", 0)))
        tree_cells.selection_remove(*tree_cells.get_children())
        refresh_previews()

    def add_page():
        new_page = {"name": "Page %d" % (len(pages) + 1), "delay": None, "cells": []}
        pages.append(new_page)
        list_pages.insert("end", new_page["name"])
        list_pages.selection_clear(0, "end")
        list_pages.selection_set("end")
        list_pages.activate("end")
        load_page()

    def del_page():
        sel = list_pages.curselection()
        if not sel:
            return
        if len(pages) <= 1:
            pages[0]["cells"] = []
            pages[0]["name"] = "Page 1"
            var_page_name.set("Page 1")
            var_page_delay_per.set("")
            refresh_previews()
            load_page()
            return
        idx = sel[0]
        pages.pop(idx)
        list_pages.delete(idx)
        n = len(pages)
        nsel = min(idx, n - 1)
        list_pages.selection_clear(0, "end")
        list_pages.selection_set(nsel)
        list_pages.activate(nsel)
        load_page()

    def load_cell():
        sel = tree_cells.selection()
        if not sel:
            return
        i = int(sel[0])
        c = preview_cur["page"]["cells"][i]
        var_cell_key.set(c.get("key") or "")
        var_cell_label.set(c.get("label") or "")
        var_cell_x.set(str(c.get("x", 0)))
        var_cell_y.set(str(c.get("y", 0)))

    def cell_from_fields():
        key = var_cell_key.get()
        if key not in RENDER_TARGETS:
            key = keys[0]
        try:
            x = max(0, min(15, int(var_cell_x.get())))
        except ValueError:
            x = 0
        try:
            y = 1 if int(var_cell_y.get()) > 0 else 0
        except ValueError:
            y = 0
        return {"key": key, "x": x, "y": y, "label": var_cell_label.get()}

    def add_cell():
        p = preview_cur["page"]
        p["cells"].append(cell_from_fields())
        load_page()
        i = len(p["cells"]) - 1
        tree_cells.see(str(i))
        tree_cells.selection_set(str(i))

    def update_cell():
        sel = tree_cells.selection()
        cell = cell_from_fields()
        if sel:
            i = int(sel[0])
            p = preview_cur["page"]
            if i < len(p["cells"]):
                p["cells"][i] = cell
                load_page()
                tree_cells.selection_set(sel[0])
                return
        p = preview_cur["page"]
        p["cells"].append(cell)
        load_page()
        i = len(p["cells"]) - 1
        tree_cells.see(str(i))
        tree_cells.selection_set(str(i))

    def remove_cell():
        sel = tree_cells.selection()
        if not sel:
            return
        i = int(sel[0])
        p = preview_cur["page"]
        if i < len(p["cells"]):
            p["cells"].pop(i)
            load_page()

    r = 0
    tk.Label(f3, text="Default page delay (s):").grid(
        row=r, column=0, sticky="w", padx=4, pady=4)
    tk.Entry(f3, textvariable=var_page_delay, width=8).grid(
        row=r, column=1, sticky="w", padx=4, pady=4)
    r += 1
    tk.Label(f3, text="Pages:").grid(
        row=r, column=0, sticky="nw", padx=4, pady=(8, 0))
    list_pages = tk.Listbox(f3, width=22, height=4)
    list_pages.grid(row=r, column=1, sticky="w", padx=4, pady=(8, 0))
    for p in pages:
        list_pages.insert("end", p.get("name") or "Page")
    ttk.Button(f3, text="Add page", command=add_page).grid(
        row=r, column=2, sticky="w", padx=2, pady=2)
    ttk.Button(f3, text="Delete", command=del_page).grid(
        row=r, column=3, sticky="w", padx=2, pady=2)
    list_pages.bind("<<ListboxSelect>>", lambda e: load_page())
    r += 1
    var_page_name = tk.StringVar()
    var_page_delay_per = tk.StringVar()
    tk.Label(f3, text="Name:").grid(
        row=r, column=0, sticky="w", padx=4, pady=4)
    tk.Entry(f3, textvariable=var_page_name, width=16).grid(
        row=r, column=1, sticky="w", padx=4, pady=4)
    tk.Label(f3, text="Page delay (s):").grid(
        row=r, column=2, sticky="w", padx=4, pady=4)
    tk.Entry(f3, textvariable=var_page_delay_per, width=8).grid(
        row=r, column=3, sticky="w", padx=4, pady=4)
    var_page_name.trace_add("write", lambda *a: _apply_page_meta())
    var_page_delay_per.trace_add("write", lambda *a: _apply_page_meta())

    def _apply_page_meta():
        p = preview_cur["page"]
        p["name"] = var_page_name.get().strip()
        raw = var_page_delay_per.get().strip()
        try:
            p["delay"] = max(float(raw), 0.1) if raw else None
        except ValueError:
            p["delay"] = None
        try:
            idx = list_pages.curselection()[0]
            list_pages.delete(idx)
            list_pages.insert(idx, p["name"] or "Page")
            list_pages.selection_set(idx)
        except (IndexError, tk.TclError):
            pass

    r += 1
    ttk.Separator(f3, orient="horizontal").grid(
        row=r, column=0, columnspan=4, sticky="we", padx=4, pady=(10, 2))
    r += 1
    tk.Label(f3, text="Cells (label, key, col, row):").grid(
        row=r, column=0, columnspan=4, sticky="w", padx=4, pady=(6, 0))
    r += 1
    tree_cells = ttk.Treeview(f3, columns=("label", "key", "x", "y"),
                              show="headings", height=6)
    tree_cells.heading("label", text="Label")
    tree_cells.heading("key", text="Key")
    tree_cells.heading("x", text="Col")
    tree_cells.heading("y", text="Row")
    tree_cells.column("label", width=60)
    tree_cells.column("key", width=80)
    tree_cells.column("x", width=36, anchor="center")
    tree_cells.column("y", width=36, anchor="center")
    tree_cells.grid(row=r, column=0, columnspan=3, sticky="we", padx=4, pady=4)
    scroll_cells = ttk.Scrollbar(f3, orient="vertical", command=tree_cells.yview)
    scroll_cells.grid(row=r, column=3, sticky="ns", pady=4)
    tree_cells.configure(yscrollcommand=scroll_cells.set)
    tree_cells.bind("<<TreeviewSelect>>", lambda e: load_cell())
    r += 1
    tk.Label(f3, text="Key").grid(row=r, column=0, sticky="w", padx=4, pady=(6, 0))
    tk.Label(f3, text="Label").grid(row=r, column=1, sticky="w", padx=4, pady=(6, 0))
    tk.Label(f3, text="Col").grid(row=r, column=2, sticky="w", padx=4, pady=(6, 0))
    tk.Label(f3, text="Row").grid(row=r, column=3, sticky="w", padx=4, pady=(6, 0))
    r += 1
    var_cell_key = tk.StringVar(value=keys[0])
    var_cell_label = tk.StringVar()
    var_cell_x = tk.StringVar(value="0")
    var_cell_y = tk.StringVar(value="0")
    ttk.Combobox(f3, textvariable=var_cell_key, values=keys, width=14,
                 state="readonly").grid(row=r, column=0, sticky="w", padx=4, pady=4)
    tk.Entry(f3, textvariable=var_cell_label, width=10).grid(
        row=r, column=1, sticky="w", padx=4, pady=4)
    tk.Entry(f3, textvariable=var_cell_x, width=4).grid(
        row=r, column=2, sticky="w", padx=4, pady=4)
    tk.Entry(f3, textvariable=var_cell_y, width=4).grid(
        row=r, column=3, sticky="w", padx=4, pady=4)
    r += 1
    ttk.Button(f3, text="Add", width=8, command=add_cell).grid(
        row=r, column=0, sticky="w", padx=2, pady=2)
    ttk.Button(f3, text="Update", width=8, command=update_cell).grid(
        row=r, column=1, sticky="w", padx=2, pady=2)
    ttk.Button(f3, text="Remove", width=8, command=remove_cell).grid(
        row=r, column=2, sticky="w", padx=2, pady=2)
    r += 1
    ttk.Separator(f3, orient="horizontal").grid(
        row=r, column=0, columnspan=4, sticky="we", padx=4, pady=(10, 2))
    r += 1
    make_preview_canvas(f3).grid(row=r, column=0, columnspan=4, sticky="w", padx=4, pady=4)
    if pages:
        list_pages.selection_set(0)
    load_page()

    f4 = ttk.Frame(nb, padding=8)
    nb.add(f4, text="USB")
    usb_cfg = dict(_state.get("usb", DEFAULT_USB))
    var_usb_sound = tk.BooleanVar(value=usb_cfg.get("sound", True))
    var_usb_conn = tk.StringVar(value=usb_cfg.get("connect", "chime_up"))
    var_usb_disc = tk.StringVar(value=usb_cfg.get("disconnect", "chime_down"))
    ttk.Checkbutton(f4, text="Sound on USB device connect/disconnect",
                    variable=var_usb_sound).grid(
        row=0, column=0, columnspan=2, sticky="w", padx=4, pady=4)
    tk.Label(f4, text="Connect:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
    ttk.Combobox(f4, textvariable=var_usb_conn, values=patterns, width=10,
                 state="readonly").grid(row=1, column=1, sticky="w", padx=4, pady=4)
    tk.Label(f4, text="Disconnect:").grid(row=2, column=0, sticky="w", padx=4, pady=4)
    ttk.Combobox(f4, textvariable=var_usb_disc, values=patterns, width=10,
                 state="readonly").grid(row=2, column=1, sticky="w", padx=4, pady=4)
    ttk.Separator(f4, orient="horizontal").grid(
        row=3, column=0, columnspan=2, sticky="we", padx=4, pady=(10, 2))
    make_preview_canvas(f4).grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=4)

    f6 = ttk.Frame(nb, padding=8)
    nb.add(f6, text="Disks")
    disks_cfg = dict(_state.get("disks", DEFAULT_DISKS))
    var_disk_sound = tk.BooleanVar(value=disks_cfg.get("sound", True))
    var_disk_conn = tk.StringVar(value=disks_cfg.get("connect", "chime_up"))
    var_disk_disc = tk.StringVar(value=disks_cfg.get("disconnect", "chime_down"))
    ttk.Checkbutton(f6, text="Sound on SATA disk connect/disconnect",
                    variable=var_disk_sound).grid(
        row=0, column=0, columnspan=2, sticky="w", padx=4, pady=4)
    tk.Label(f6, text="Connect:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
    ttk.Combobox(f6, textvariable=var_disk_conn, values=patterns, width=10,
                 state="readonly").grid(row=1, column=1, sticky="w", padx=4, pady=4)
    tk.Label(f6, text="Disconnect:").grid(row=2, column=0, sticky="w", padx=4, pady=4)
    ttk.Combobox(f6, textvariable=var_disk_disc, values=patterns, width=10,
                 state="readonly").grid(row=2, column=1, sticky="w", padx=4, pady=4)
    ttk.Separator(f6, orient="horizontal").grid(
        row=3, column=0, columnspan=2, sticky="we", padx=4, pady=(10, 2))
    make_preview_canvas(f6).grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=4)

    f5 = ttk.Frame(nb, padding=8)
    nb.add(f5, text="Delays")
    delays_cfg = dict(_state.get("delays", DEFAULT_DELAYS))
    delay_vars = {}
    delay_fields = [
        ("gpu", "GPU update interval (s):"),
        ("ctemp", "CPU temp update interval (s):"),
        ("windows", "Window monitor interval (s):"),
        ("usb", "USB monitor interval (s):"),
        ("disks", "SATA disk monitor interval (s):"),
        ("retry", "Reconnect retry (s):"),
        ("alert_min", "Min time between alerts (s):"),
    ]
    for i, (key, label) in enumerate(delay_fields):
        tk.Label(f5, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=4)
        v = tk.StringVar(value=str(delays_cfg.get(key, DEFAULT_DELAYS[key])))
        delay_vars[key] = v
        tk.Entry(f5, textvariable=v, width=8).grid(
            row=i, column=1, sticky="w", padx=4, pady=4)
    ttk.Separator(f5, orient="horizontal").grid(
        row=len(delay_fields), column=0, columnspan=2, sticky="we",
        padx=4, pady=(10, 2))
    make_preview_canvas(f5).grid(
        row=len(delay_fields) + 1, column=0, columnspan=2, sticky="w", padx=4, pady=4)

    refresh_previews()

    def save():
        try:
            iv = float(var_int.get())
        except ValueError:
            messagebox.showerror("Error", "Interval must be a number", parent=root)
            return
        try:
            pin = int(var_pin.get())
        except ValueError:
            pin = 2
        iv = max(iv, 0.05)
        buz_new = {
            "enabled": var_buz.get(),
            "led": var_led.get(),
            "watch": var_watch.get(),
            "pin": max(2, min(13, pin)),
            "volume": max(0, min(100, var_vol.get())),
            "frequency": max(100, min(10000, var_freq.get())),
            "speed": max(25, min(400, var_speed.get())),
            "type": var_type.get(),
            "error": var_err.get(),
            "warning": var_warn.get(),
            "info": var_info.get(),
        }
        disp_pages = []
        for p in pages:
            cells = [c for c in p.get("cells", [])
                     if c.get("key") in RENDER_TARGETS]
            if not cells:
                continue
            disp_pages.append({
                "name": (p.get("name") or "").strip() or "Page %d" % (len(disp_pages) + 1),
                "delay": p.get("delay"),
                "cells": cells,
            })
        if not disp_pages:
            disp_pages = default_page()["cells"]
            disp_new = {"page_delay": 5.0,
                        "pages": [{"name": "Page 1", "delay": None,
                                   "cells": disp_pages}]}
        else:
            try:
                page_delay = max(float(var_page_delay.get()), 0.1)
            except ValueError:
                page_delay = 5.0
            disp_new = {"page_delay": page_delay, "pages": disp_pages}
        usb_new = {
            "sound": var_usb_sound.get(),
            "connect": var_usb_conn.get(),
            "disconnect": var_usb_disc.get(),
        }
        disks_new = {
            "sound": var_disk_sound.get(),
            "connect": var_disk_conn.get(),
            "disconnect": var_disk_disc.get(),
        }
        delays_new = {}
        for key, var in delay_vars.items():
            try:
                v = float(var.get())
            except ValueError:
                v = DEFAULT_DELAYS.get(key, 2.0)
            delays_new[key] = max(v, 0.1)
        ignored_new = [list_ign.get(i).strip()
                       for i in range(list_ign.size())
                       if list_ign.get(i).strip()]
        cfg = {"com_port": "" if var_port.get() == "auto" else var_port.get(),
               "interval": iv, "buzzer": buz_new, "display": disp_new,
               "usb": usb_new, "disks": disks_new, "delays": delays_new,
               "ignored": ignored_new}
        save_config(cfg)
        _state["com_port"] = cfg["com_port"]
        _state["interval"] = iv
        _state["buzzer"] = buz_new
        _state["display"] = disp_new
        _state["usb"] = usb_new
        _state["disks"] = disks_new
        _state["delays"] = delays_new
        _state["ignored"] = ignored_new
        send_buzzer_config()
        root.destroy()
        force_reconnect()

    tk.Button(root, text="Save", command=save, width=12).pack(pady=8)
    root.attributes("-topmost", True)
    root.mainloop()


def _single_instance():
    global _LOCK_PATH
    if IS_WINDOWS:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
            handle = kernel32.CreateMutexW(None, False, "Local\\" + APP_NAME)
            if kernel32.GetLastError() == 183:
                return None
            return handle
        except Exception:
            return True
    lock = os.path.join(APPDIR, APP_NAME + ".lock")
    try:
        os.makedirs(APPDIR, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        _LOCK_PATH = lock
        return lock
    except FileExistsError:
        try:
            with open(lock) as f:
                old = int(f.read().strip() or "0")
            if old > 0:
                try:
                    os.kill(old, 0)
                    return None
                except ProcessLookupError:
                    pass
        except (ValueError, OSError, IOError):
            pass
        try:
            os.remove(lock)
        except OSError:
            return None
        return _single_instance()
    except Exception:
        return True


def main():
    log("started")
    if "--check" in sys.argv:
        cpu = psutil.cpu_percent(interval=0.8)
        vm = psutil.virtual_memory()
        cf = psutil.cpu_freq()
        gpu, gtemp, gfreq = read_gpu()
        ctemp = read_cpu_temp()
        with _state["lock"]:
            _state["cpu"] = cpu
            _state["ram_gb"] = vm.used / 1073741824.0
            _state["cpu_freq"] = cf.current if cf is not None else None
            _state["gpu"] = gpu
            _state["gtemp"] = gtemp
            _state["gpu_freq"] = gfreq
            _state["ctemp"] = ctemp
        line = build_line()
        print(line)
        log("check: " + line)
        return

    if _single_instance() is None:
        return

    cfg = load_config()
    _state["com_port"] = cfg.get("com_port", "")
    _state["interval"] = float(cfg.get("interval", 1.0))
    _state["buzzer"] = dict(cfg.get("buzzer", DEFAULT_BUZZER))
    _state["display"] = build_display_pages(cfg.get("display"))
    _state["usb"] = dict(cfg.get("usb", DEFAULT_USB))
    _state["disks"] = dict(cfg.get("disks", DEFAULT_DISKS))
    _state["delays"] = dict(cfg.get("delays", DEFAULT_DELAYS))
    _state["ignored"] = list(cfg.get("ignored", []))

    threading.Thread(target=sampler_loop, daemon=True).start()
    threading.Thread(target=worker_loop, daemon=True).start()
    threading.Thread(target=alert_loop, daemon=True).start()
    threading.Thread(target=usb_monitor_loop, daemon=True).start()
    threading.Thread(target=disk_monitor_loop, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("Status", show_status),
        pystray.MenuItem("Settings...", lambda i, ic: open_settings()),
        pystray.MenuItem("Reconnect", lambda i, ic: force_reconnect(i, ic)),
        pystray.MenuItem("Autostart",
                         lambda i, ic: toggle_autostart(i, ic),
                         checked=lambda item: autostart_enabled()),
        pystray.MenuItem("Open folder", lambda i, ic: open_folder(i, ic)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", lambda i, ic: shutdown(i, ic)),
    )

    icon = pystray.Icon(APP_NAME, make_icon_image(), "LCD525 Panel", menu)
    try:
        icon.run()
    except Exception as e:
        log("tray backend unavailable, running headless: %s" % e)
        while _state["running"]:
            time.sleep(1.0)


if __name__ == "__main__":
    main()
