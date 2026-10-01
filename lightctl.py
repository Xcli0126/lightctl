#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lightctl — ROG Flow Z13 (GZ302EA) 灯光控制 GUI

两路独立控制，各自按 USB Product ID 精准路由，互不干扰：
  背光（后部灯条 / rear glow, USB 0b05:18c6, HIDID 0x18C6）
  键盘灯（keyboard,          USB 0b05:1a30, HIDID 0x1A30）

实现方式：直接向 /dev/hidrawN 写 64 字节 Aura 输出报告（Report ID 0x5d），
不依赖 z13ctl 二进制。协议逆向自 g-helper (MIT) 与 z13ctl PROTOCOL.md。

功能：
  - 两路独立开关 + 各 4 档亮度（关/低/中/高）
  - 状态栏（托盘）常驻，图标随灯光状态变色，菜单可控
  - 设置：界面语言（中/英）、托盘开关、开机自启、主题（深/浅）
  - --selftest 不开 GUI 验证链路
"""
import ctypes
import ctypes.util
import glob
import argparse
import io
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

APP_NAME = "lightctl"
APP_VERSION = "1.1.0"
CONFIG_DIR = os.path.expanduser("~/.config/lightctl")
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
AUTOSTART_FILE = os.path.expanduser(
    "~/.config/autostart/lightctl.desktop")

# Aura 协议常量
AURA_ID = 0x5D
HIDIOCGRDESCSIZE = 0x80044801
HIDIOCGRDESC = 0x90044802
REPORT_SIZE = 64
# power 字节对：keyb, bar, lid, rear（关=全 0，开=键盘全亮+灯条全状态）
POWER_OFF = (0x00, 0x00, 0x00, 0x00)
POWER_ON = (0xFF, 0x1F, 0xFF, 0xFF)
# Aura 命令字节
CMD_WAKE, CMD_CFG, CMD_DYN = 0xB9, 0x05, 0xC0
CMD_POWER, CMD_BRIGHT, CMD_MODE = 0xBD, 0xBA, 0xB3
CMD_SET, CMD_APPLY = 0xB5, 0xB4
BRIGHT_HDR = (0xC5, 0xC4)

PID_REAR = "18C6"
PID_KEYBOARD = "1A30"
DEVICES = ("rear", "keyboard")
# 每个设备只认自己 zone 的 SetMode 字节（后盖/灯条=1，键盘=0）
DEVICE_ZONE = {"rear": 1, "keyboard": 0}

LEVELS = ["off", "low", "medium", "high"]

# Aura 灯效模式（mode 字节值）与速度（speed 字节值）
MODES = {"static": 0, "breathe": 1, "cycle": 2, "rainbow": 3, "strobe": 10}
MODE_LIST = list(MODES)
SPEEDS = {"slow": 0xE1, "normal": 0xEB, "fast": 0xF5}
SPEED_LIST = list(SPEEDS)

# 主题配色
THEMES = {
    "dark": {
        "BG": "#1e1e2e", "CARD_BG": "#313244", "BTN_BG": "#45475a",
        "FG": "#cdd6f4", "FG_DIM": "#a6adc8", "FG_OK": "#a6e3a1",
        "FG_ERR": "#f38ba8", "FG_INFO": "#89b4fa",
        "ACCENT": "#89b4fa", "ENTRY_BG": "#45475a",
        "HL_BG": "#45475a",
    },
    "light": {
        "BG": "#eff1f5", "CARD_BG": "#e6e9ef", "BTN_BG": "#dce0e8",
        "FG": "#4c4f69", "FG_DIM": "#6c6f85", "FG_OK": "#40a02b",
        "FG_ERR": "#d20f39", "FG_INFO": "#1e66f5",
        "ACCENT": "#1e66f5", "ENTRY_BG": "#ffffff",
        "HL_BG": "#dce0e8",
    },
}

# ---------- i18n ----------
_LANG = "zh"
_THEME = "dark"
_STRINGS = {
    "zh": {
        "app_title": "lightctl — Z13 灯光控制",
        "backend": "后端：Aura HID 直写（按 PID 路由）",
        "no_backend": "未找到可用的 Aura 设备",
        "rear_name": "背光（后部灯条）",
        "kbd_name": "键盘灯",
        "switch": "开关",
        "brightness": "亮度",
        "mode": "模式", "color": "颜色",
        "mode_static": "常亮", "mode_breathe": "呼吸",
        "mode_cycle": "循环", "mode_rainbow": "彩虹", "mode_strobe": "频闪",
        "speed": "速度", "speed_slow": "慢", "speed_normal": "中", "speed_fast": "快",
        "auto_color": "自动",
        "on": "开", "off": "关",
        "low": "低", "medium": "中", "high": "高",
        "all_on": "全部打开", "all_off": "全部关闭",
        "refresh": "刷新状态", "settings": "设置",
        "status_fmt": "Aura 接口 {n}/2：{list}",
        "missing": "（缺: {m}）",
        "hidraw_detected": "hidraw: {p}",
        "hidraw_missing": "hidraw: 未检测到",
        "executing": "执行中…",
        "all_on_msg": "全部已打开", "all_off_msg": "全部已关闭",
        "fail": "失败：{m}",
        "settings_title": "设置",
        "language": "界面语言",
        "lang_zh": "简体中文", "lang_en": "English",
        "tray": "状态栏（托盘）常驻",
        "autostart": "开机自启动",
        "theme": "主题",
        "theme_dark": "深色", "theme_light": "浅色",
        "save": "保存", "cancel": "取消",
        "close": "关闭", "quit": "退出",
        "show_win": "显示主窗口", "hide_win": "隐藏主窗口",
        "tray_on": "灯光已开", "tray_off": "灯光已关",
        "tray_partial": "部分灯光已关",
        "err_no_dev": "未找到 Aura 设备，请检查 udev 规则。",
        "version": "版本",
        "rear_zone": "后盖灯", "kbd_zone": "键盘灯",
        "state_on": "开", "state_off": "关",
        "tray_tip": "lightctl 灯光控制",
        "err_generic": "操作失败",
        "autostart_on": "已开启开机自启", "autostart_off": "已关闭开机自启",
        "need_restart": "语言/主题改动即时生效",
        "about": "关于", "ok": "确定",
    },
    "en": {
        "app_title": "lightctl — Z13 Lighting Control",
        "backend": "Backend: Aura HID direct write (per-PID routing)",
        "no_backend": "No Aura device found",
        "rear_name": "Rear glow (light bar)",
        "kbd_name": "Keyboard backlight",
        "switch": "Switch",
        "brightness": "Brightness",
        "mode": "Mode", "color": "Color",
        "mode_static": "Static", "mode_breathe": "Breathe",
        "mode_cycle": "Cycle", "mode_rainbow": "Rainbow", "mode_strobe": "Strobe",
        "speed": "Speed", "speed_slow": "Slow", "speed_normal": "Normal", "speed_fast": "Fast",
        "auto_color": "Auto",
        "on": "On", "off": "Off",
        "low": "Low", "medium": "Medium", "high": "High",
        "all_on": "All on", "all_off": "All off",
        "refresh": "Refresh", "settings": "Settings",
        "status_fmt": "Aura interfaces {n}/2: {list}",
        "missing": " (missing: {m})",
        "hidraw_detected": "hidraw: {p}",
        "hidraw_missing": "hidraw: not detected",
        "executing": "Working…",
        "all_on_msg": "All lights on", "all_off_msg": "All lights off",
        "fail": "Failed: {m}",
        "settings_title": "Settings",
        "language": "Language",
        "lang_zh": "简体中文", "lang_en": "English",
        "tray": "Keep in system tray",
        "autostart": "Start on login",
        "theme": "Theme",
        "theme_dark": "Dark", "theme_light": "Light",
        "save": "Save", "cancel": "Cancel",
        "close": "Close", "quit": "Quit",
        "show_win": "Show main window", "hide_win": "Hide main window",
        "tray_on": "Lights on", "tray_off": "Lights off",
        "tray_partial": "Some lights off",
        "err_no_dev": "No Aura device found. Check udev rules.",
        "version": "Version",
        "rear_zone": "Rear", "kbd_zone": "Keyboard",
        "state_on": "On", "state_off": "Off",
        "tray_tip": "lightctl lighting control",
        "err_generic": "Operation failed",
        "autostart_on": "Autostart enabled", "autostart_off": "Autostart disabled",
        "need_restart": "Language/theme changes apply immediately",
        "about": "About", "ok": "OK",
    },
}


def t(key, **kw):
    s = _STRINGS.get(_LANG, _STRINGS["zh"]).get(key, key)
    if kw:
        try:
            return s.format(**kw)
        except Exception:
            return s
    return s


def _i18n_check():
    """S5: zh/en key sets match, and every t() literal exists."""
    import re as _re
    zh, en = set(_STRINGS["zh"]), set(_STRINGS["en"])
    if zh != en:
        raise AssertionError("i18n mismatch")
    src = open(__file__, encoding="utf-8").read()
    # 只扫直接闭合的静态键；动态拼接（前缀 + 变量）跳过
    for m in _re.findall(r'(?<![A-Za-z_.])t\(\s*"([a-z_0-9]+)"\s*\)', src):
        if m not in zh:
            raise AssertionError("missing i18n key: " + m)


def _dev_label(dev):
    """设备名 → 当前语言标签（模块级，Backend/Card/App 共用）。"""
    return t("rear_name" if dev == "rear" else "kbd_name")


def set_lang(lang):
    global _LANG
    _LANG = lang if lang in _STRINGS else "zh"


def set_theme(name):
    global _THEME
    _THEME = name if name in THEMES else "dark"


def theme():
    return THEMES[_THEME]
# ---------- 配置持久化 ----------
def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            v = json.load(f)
            if isinstance(v, dict):
                return v
    except Exception as e:
        sys.stderr.write(f"lightctl: read {path}: {e}\n")
    return default


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        sys.stderr.write(f"lightctl: write {path}: {e}\n")
        return False


def load_settings():
    return _read_json(SETTINGS_FILE, {})


def save_settings(s):
    _write_json(SETTINGS_FILE, s)


def load_state():
    return _read_json(STATE_FILE, {})


def save_state(s):
    _write_json(STATE_FILE, s)


def set_autostart(enabled):
    try:
        if enabled:
            os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
            exe = os.path.abspath(sys.argv[0])
            content = (
                "[Desktop Entry]\nType=Application\nName=lightctl\n"
                f"Exec=python3 {exe}\nTerminal=false\n"
                "X-GNOME-Autostart-enabled=true\nStartupNotify=true\n"
            )
            with open(AUTOSTART_FILE, "w", encoding="utf-8") as f:
                f.write(content)
        elif os.path.exists(AUTOSTART_FILE):
            os.remove(AUTOSTART_FILE)
        return True
    except Exception as e:
        sys.stderr.write(f"lightctl: autostart: {e}\n")
        return False


def autostart_enabled():
    return os.path.exists(AUTOSTART_FILE)


# ---------- 协议层 ----------
_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


class _Desc(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("value", ctypes.c_uint8 * 4096)]


def _has_aura(fd):
    sz = ctypes.c_uint32(0)
    if _libc.ioctl(fd, HIDIOCGRDESCSIZE, ctypes.byref(sz)) != 0:
        return False
    d = _Desc()
    d.size = sz.value
    if _libc.ioctl(fd, HIDIOCGRDESC, ctypes.byref(d)) != 0:
        return False
    val = bytes(d.value[:d.size])
    return any(val[i] == 0x85 and val[i + 1] == AURA_ID
               for i in range(len(val) - 1))


def find_aura_nodes():
    out = {}
    for ue in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        try:
            with open(ue) as f:
                txt = f.read()
        except OSError:
            continue
        if "00000B05" not in txt:
            continue
        hidid = next((ln for ln in txt.splitlines() if ln.startswith("HID_ID=")), "")
        if not hidid:
            continue
        prod = hidid.split("=")[-1].split(":")[-1].upper().lstrip("0")
        if prod not in (PID_REAR, PID_KEYBOARD):
            continue
        # uevent 路径 …/hidrawN/device/uevent → hidrawN
        dev = os.path.basename(os.path.dirname(os.path.dirname(ue)))
        path = "/dev/" + dev
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError:
            continue
        try:
            if _has_aura(fd):
                name = "rear" if prod == PID_REAR else "keyboard"
                out[name] = path
        finally:
            os.close(fd)
    return out


def _pkt(*body):
    b = bytearray(REPORT_SIZE)
    b[0] = AURA_ID
    for i, x in enumerate(body, 1):
        b[i] = x
    return bytes(b)


class AuraDevice:
    """一个 Aura HID 设备（按 PID 打开的单个 hidraw 节点）。"""

    def __init__(self, path):
        self.path = path

    def _write_seq(self, packets, delay=0.01):
        fd = os.open(self.path, os.O_RDWR)
        try:
            for i, p in enumerate(packets):
                os.write(fd, p)
                if i < len(packets) - 1:
                    time.sleep(delay)
        finally:
            os.close(fd)

    @staticmethod
    def _init_pkts():
        # 首字节 0x5d 是 Report ID；"]ASUS Tech.Inc." 的 ] 即 0x5d
        return [
            _pkt(CMD_WAKE),
            b"]ASUS Tech.Inc.",
            _pkt(CMD_CFG, 0x20, 0x31, 0x00, 0x1A),
            _pkt(CMD_DYN, 0x03, 0x01),
        ]

    @staticmethod
    def _power_pkt(keyb, bar, lid, rear):
        return _pkt(CMD_POWER, 0x01, keyb, bar, lid, rear, 0xFF)

    @staticmethod
    def _brightness_pkt(level):
        return _pkt(CMD_BRIGHT, BRIGHT_HDR[0], BRIGHT_HDR[1], level)

    @staticmethod
    def _mode_pkt(zone, mode, r, g, b, speed, r2=0, g2=0, b2=0):
        """SetMode：[0x5D,0xB3,zone,mode,r,g,b,speed,dir,rand,r2,g2,b2]。
        rand：全 0 色→0xFF(设备自选)，breathe→0x01(双色)，否则 0x00。"""
        if r == 0 and g == 0 and b == 0:
            rand = 0xFF
        elif mode == MODES["breathe"]:
            rand = 0x01
        else:
            rand = 0x00
        return _pkt(CMD_MODE, zone, mode, r, g, b, speed, 0x00, rand,
                    r2, g2, b2)

    @staticmethod
    def _commit_pkts():
        return [_pkt(CMD_SET), _pkt(CMD_APPLY)]

    def init(self):
        self._write_seq(self._init_pkts())

    def set_power(self, keyb, bar, lid, rear):
        self._write_seq([self._power_pkt(keyb, bar, lid, rear)])

    def set_brightness(self, level):
        self._write_seq([self._brightness_pkt(level)])

    def turn_off(self):
        # 一次 fd 序列完成 init+power+brightness（原 3 次 open/close）
        self._write_seq(
            self._init_pkts() + [self._power_pkt(*POWER_OFF),
                                 self._brightness_pkt(0)],
            delay=0.02)

    def turn_on(self, level=3, zone=0, mode="static",
                color=(0, 0, 0), speed="normal",
                color2=(0, 0, 0)):
        """开灯 + 设色/模式。color=(0,0,0) 表示设备自选色。
        zone 决定 SetMode 字节（本设备只认自己的 zone）。"""
        r, g, b = color
        r2, g2, b2 = color2
        pkts = (self._init_pkts()
                + [self._power_pkt(*POWER_ON), self._brightness_pkt(level)]
                + [self._mode_pkt(zone, MODES[mode], r, g, b,
                                  SPEEDS[speed], r2, g2, b2)]
                + self._commit_pkts())
        self._write_seq(pkts, delay=0.02)


class Backend:
    def __init__(self):
        self._lock = threading.Lock()
        self.nodes = find_aura_nodes()

    def refresh(self):
        with self._lock:
            self.nodes = find_aura_nodes()
        return self.nodes

    def _apply(self, names, on, level, light=None):
        """C9: 取锁→遍历→turn→收集 OSError。返回 (ok, errs)。
        light: dict(mode/color/speed/color2)，仅 on=True 时用。"""
        light = light or {}
        errs = []
        with self._lock:
            for name in names:
                if name not in self.nodes:
                    errs.append(_dev_label(name))
                    continue
                dev = AuraDevice(self.nodes[name])
                try:
                    if on:
                        dev.turn_on(
                            level,
                            zone=DEVICE_ZONE.get(name, 0),
                            mode=light.get("mode", "static"),
                            color=light.get("color", (0, 0, 0)),
                            speed=light.get("speed", "normal"),
                            color2=light.get("color2", (0, 0, 0)))
                    else:
                        dev.turn_off()
                except OSError as e:
                    errs.append(f"{_dev_label(name)}: {e}")
        return (not errs), errs

    def set_device(self, name, on, level=3, light=None):
        ok, errs = self._apply([name], on, level, light)
        if not ok:
            # 只有一个错且是"缺设备"→统一文案（原 set_device 语义）
            if len(errs) == 1 and errs[0] == _dev_label(name):
                return False, t("err_no_dev")
            return False, "; ".join(errs)
        return True, f"{_dev_label(name)} → {t('state_on' if on else 'state_off')}"

    def set_all(self, on, level=3, light=None):
        ok, errs = self._apply(DEVICES, on, level, light)
        if not ok:
            return False, "; ".join(errs)
        return True, t("all_on_msg") if on else t("all_off_msg")

    def list_nodes(self):
        with self._lock:
            return dict(self.nodes)
# ---------- 图标（PIL 程序化绘制） ----------
def _make_icon_bytes(state="on", size=64):
    """画一个灯泡/灯条图标，返回 PNG bytes。state: on/off/partial。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size
    if state == "on":
        glow, body, base = "#ffd166", "#ffe9a8", "#f4a261"
    elif state == "off":
        glow, body, base = "#45475a", "#585b70", "#313244"
    else:
        glow, body, base = "#f9e2af", "#fab387", "#a6520a"
    cx, cy = s // 2, int(s * 0.42)
    r = int(s * 0.26)
    # 光晕
    for gr in range(r + int(s * 0.16), r, -3):
        a = max(0, min(255, int(70 * (1 - (gr - r) / (s * 0.16)))))
        d.ellipse([cx - gr, cy - gr, cx + gr, cy + gr],
                  fill=(255, 209, 102, a) if state == "on" else (0, 0, 0, 0))
    # 灯泡身
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=body, outline=glow, width=2)
    # 灯座
    bw, bh = int(r * 0.9), int(r * 0.5)
    by = cy + r - 2
    d.rounded_rectangle([cx - bw // 2, by, cx + bw // 2, by + bh],
                        radius=3, fill=base, outline=glow, width=1)
    # 灯丝
    d.line([(cx - int(r * 0.3), cy - int(r * 0.1)),
            (cx, cy + int(r * 0.2)),
            (cx + int(r * 0.3), cy - int(r * 0.1))],
           fill=glow if state != "off" else "#6c6f85", width=2)
    # 状态点
    if state == "off":
        d.ellipse([s - 16, 4, s - 4, 16], fill="#f38ba8")
    else:
        d.ellipse([s - 16, 4, s - 4, 16], fill="#a6e3a1")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_ICON_CACHE = {}


def _icon_path(state="on", size=64):
    """图标 PNG 路径；命中缓存/磁盘则直接返回（P11，C1 合并 get_icon）。"""
    key = (state, size)
    path = os.path.join(CONFIG_DIR, f"icon_{state}_{size}.png")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    if key not in _ICON_CACHE:
        _ICON_CACHE[key] = _make_icon_bytes(state, size)
    data = _ICON_CACHE[key]
    if not data:
        return None
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as e:
        sys.stderr.write(f"lightctl: icon write {path}: {e}\n")
        return None


# ---------- 托盘（AyatanaAppIndicator3 + GTK3 主循环线程） ----------
def _tray_available():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3  # noqa: F401
        return True
    except Exception:
        return False


class Tray:
    """系统托盘图标。在独立线程跑 GTK 主循环。"""

    def __init__(self, app):
        self.app = app
        self.ind = None
        self._available = _tray_available()
        self._state = "on"
        self._Gtk = None
        self._GLib = None

    def start(self):
        if not self._available or self.ind is not None:
            return
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import Gtk, GLib, AyatanaAppIndicator3 as AppInd

        def _build():
            self._Gtk = Gtk
            self._GLib = GLib
            ind = AppInd.Indicator.new(
                "lightctl", "preferences-desktop-display",
                AppInd.IndicatorCategory.HARDWARE)
            ind.set_status(AppInd.IndicatorStatus.ACTIVE)
            ind.set_title(t("tray_tip") if False else APP_NAME)
            menu = Gtk.Menu()

            def _mi(label, cb):
                item = Gtk.MenuItem.new_with_label(label)
                item.connect("activate", cb)
                menu.append(item)
                return item

            self.mi_rear = _mi(t("rear_zone"), self._on_rear)
            self.mi_kbd = _mi(t("kbd_zone"), self._on_kbd)
            menu.append(Gtk.SeparatorMenuItem())
            self.mi_show = _mi(t("show_win"), self._on_show)
            _mi(t("settings"), self._on_settings)
            menu.append(Gtk.SeparatorMenuItem())
            _mi(t("quit"), self._on_quit)
            menu.show_all()
            ind.set_menu(menu)
            self.ind = ind
            self._update_icon(self._state)

        GLib.idle_add(_build)
        self._thread = threading.Thread(target=Gtk.main, daemon=True)
        self._thread.start()

    def _call(self, fn):
        """在 GTK 线程执行 fn。"""
        if self._GLib is not None:
            self._GLib.idle_add(fn)

    def _update_icon(self, state):
        self._state = state
        if not self.ind:
            return
        path = _icon_path(state, 48)
        def _do():
            if path:
                self.ind.set_icon_full(path, APP_NAME)
            self._refresh_labels()
            return False
        self._call(_do)

    def _refresh_labels(self):
        # 用菜单项标签反映当前状态
        st = self.app.state
        def _do():
            try:
                rear_on = st.get("rear", {}).get("on", True)
                kbd_on = st.get("keyboard", {}).get("on", True)
                self.mi_rear.set_label(
                    f"{t('rear_zone')}: {t('state_on' if rear_on else 'state_off')}")
                self.mi_kbd.set_label(
                    f"{t('kbd_zone')}: {t('state_on' if kbd_on else 'state_off')}")
                # show/hide 随窗口可见性切换（D3 hide_win）
                visible = bool(self.app.state is not None) and \
                    self.app.winfo_viewable()
                self.mi_show.set_label(
                    t("hide_win") if visible else t("show_win"))
            except Exception:
                pass
            return False
        self._call(_do)

    # 托盘回调跑在 GTK 线程，Tk 只能被创建线程访问 —— 一律 after(0,...) 投递回主线程
    def _on_rear(self, *_):
        self.app.after(0, self.app.toggle_from_tray, "rear")

    def _on_kbd(self, *_):
        self.app.after(0, self.app.toggle_from_tray, "keyboard")

    def _on_show(self, *_):
        # 窗口可见→隐藏，不可见→显示（D3 hide_win）
        def _toggle():
            if self.app.winfo_viewable():
                self.app.withdraw()
            else:
                self.app.show_window()
        self.app.after(0, _toggle)

    def _on_settings(self, *_):
        def _open():
            self.app.show_window()
            self.app.open_settings()
        self.app.after(0, _open)

    def _on_quit(self, *_):
        self.app.after(0, self.app.shutdown)

    def stop(self):
        if self.ind is None:
            return
        ind, self.ind = self.ind, None
        Gtk, GLib = getattr(self, "_Gtk", None), getattr(self, "_GLib", None)
        if GLib is None or Gtk is None:
            return

        def _do():
            try:
                from gi.repository import AyatanaAppIndicator3 as AppInd
                ind.set_status(AppInd.IndicatorStatus.PASSIVE)
            except Exception:
                pass
            try:
                Gtk.main_quit()
            except Exception:
                pass
            return False

        GLib.idle_add(_do)



# ---------- UI：控制卡 ----------
def _lbl(parent, text, bg, fg, font=("Sans", 10)):
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=font)


def _btn(parent, text, cmd, bg, fg, hl, width=4, padx=(0, 6)):
    return tk.Button(parent, text=text, width=width, command=cmd,
                     bg=bg, fg=fg, relief="flat", padx=2, pady=2,
                     activebackground=hl, activeforeground=fg)


def _combo(parent, var, values, cb, width=5):
    c = ttk.Combobox(parent, textvariable=var, values=values,
                     state="readonly", width=width, font=("Sans", 10))
    c.bind("<<ComboboxSelected>>", cb)
    return c


class Card:
    def __init__(self, parent, dev, on_change):
        self.dev = dev
        self.on_change = on_change
        self.on_key, self.lv_key = "on", "high"   # key 驱动状态（C10）
        self.mode_key = "static"
        self.speed_key = "normal"
        self.color = (0, 0, 0)   # (0,0,0)=设备自选色
        th = theme()
        self.frame = tk.LabelFrame(
            parent, text=" " + _dev_label(dev) + " ",
            bg=th["CARD_BG"], fg=th["FG"], font=("Sans", 11, "bold"),
            padx=12, pady=10,
        )
        self.frame.pack(fill="x", padx=14, pady=6)

        r1 = tk.Frame(self.frame, bg=th["CARD_BG"]); r1.pack(fill="x")
        _lbl(r1, t("switch"), th["CARD_BG"], th["FG_DIM"]).pack(side="left")
        self.sw_var = tk.StringVar(value=t("on"))
        self.sw = _combo(r1, self.sw_var, [t("on"), t("off")], self._toggle)
        self.sw.pack(side="right")

        r2 = tk.Frame(self.frame, bg=th["CARD_BG"]); r2.pack(fill="x", pady=(8, 0))
        _lbl(r2, t("brightness"), th["CARD_BG"], th["FG_DIM"]).pack(side="left")
        self.lv_var = tk.StringVar(value=t("high"))
        self.lv = _combo(r2, self.lv_var, [t(x) for x in LEVELS], self._level)
        self.lv.pack(side="right")

        r3 = tk.Frame(self.frame, bg=th["CARD_BG"]); r3.pack(fill="x", pady=(10, 0))
        self.btns = {code: _btn(
            r3, t(code),
            lambda c=code: self.on_change(self.dev, "level", c),
            th["BTN_BG"], th["FG"], th["HL_BG"]) for code in LEVELS}
        for b in self.btns.values():
            b.pack(side="left", padx=(0, 6))

        # 模式 + 速度（C: color/mode 控制）
        r4 = tk.Frame(self.frame, bg=th["CARD_BG"]); r4.pack(fill="x", pady=(10, 0))
        _lbl(r4, t("mode"), th["CARD_BG"], th["FG_DIM"]).pack(side="left")
        self.mode_var = tk.StringVar(value=t("mode_static"))
        self.mode_combo = _combo(r4, self.mode_var,
                                 [t("mode_" + m) for m in MODE_LIST],
                                 self._mode, width=8)
        self.mode_combo.pack(side="right")
        _lbl(r4, t("speed"), th["CARD_BG"], th["FG_DIM"]).pack(side="left", padx=(12, 0))
        self.speed_var = tk.StringVar(value=t("speed_normal"))
        self.speed_combo = _combo(r4, self.speed_var,
                                  [t("speed_" + s) for s in SPEED_LIST],
                                  self._speed, width=5)
        self.speed_combo.pack(side="right")

        # 颜色：hex 输入 + 色块预览
        r5 = tk.Frame(self.frame, bg=th["CARD_BG"]); r5.pack(fill="x", pady=(8, 0))
        _lbl(r5, t("color"), th["CARD_BG"], th["FG_DIM"]).pack(side="left")
        self.color_var = tk.StringVar(value="auto")
        self.color_entry = tk.Entry(r5, textvariable=self.color_var, width=9,
                                    bg=th["ENTRY_BG"], fg=th["FG"],
                                    insertbackground=th["FG"],
                                    font=("Mono", 10))
        self.color_entry.pack(side="right")
        self.color_entry.bind("<Return>", self._color)
        self.color_entry.bind("<FocusOut>", self._color)
        self.color_sw = tk.Label(r5, text="  ", width=3,
                                 bg=th["ENTRY_BG"], relief="solid", bd=1)
        self.color_sw.pack(side="right", padx=(0, 6))

        self.path_lbl = _lbl(self.frame, "", th["CARD_BG"], th["FG_DIM"],
                             font=("Mono", 8))
        self.path_lbl.config(anchor="w")
        self.path_lbl.pack(fill="x", pady=(8, 0))

        self.badge = _lbl(self.frame, "", th["CARD_BG"], th["FG_OK"],
                          font=("Sans", 9, "bold"))
        self.badge.config(anchor="w")
        self.badge.pack(fill="x", pady=(4, 0))

    def _toggle(self, _e=None):
        self.on_key = "on" if self.sw_var.get() == t("on") else "off"
        self.on_change(self.dev, "toggle", self.on_key == "on")

    def _level(self, _e=None):
        # 由当前显示文本反查 key（值集顺序与 LEVELS 一致）
        idx = [t(x) for x in LEVELS].index(self.lv_var.get()) \
            if self.lv_var.get() in [t(x) for x in LEVELS] else 3
        self.lv_key = LEVELS[idx]
        self.on_change(self.dev, "level", self.lv_key)

    def _mode(self, _e=None):
        idx = [t("mode_" + m) for m in MODE_LIST].index(self.mode_var.get()) \
            if self.mode_var.get() in [t("mode_" + m) for m in MODE_LIST] else 0
        self.mode_key = MODE_LIST[idx]
        self.on_change(self.dev, "light", None)

    def _speed(self, _e=None):
        idx = [t("speed_" + s) for s in SPEED_LIST].index(self.speed_var.get()) \
            if self.speed_var.get() in [t("speed_" + s) for s in SPEED_LIST] else 1
        self.speed_key = SPEED_LIST[idx]
        self.on_change(self.dev, "light", None)

    @staticmethod
    def _parse_color(s):
        # auto/empty -> (0,0,0); RRGGBB or #RRGGBB -> (r,g,b); invalid -> None
        s = s.strip().lstrip("#")
        if not s or s.lower() == "auto":
            return (0, 0, 0)
        if len(s) == 6:
            try:
                return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                pass
        return None   # 非法

    def _color(self, _e=None):
        rgb = self._parse_color(self.color_var.get())
        if rgb is None:
            # 非法输入：回滚到上次值
            self.color_var.set(self._color_str())
            return
        self.color = rgb
        self._sync_sw()
        self.on_change(self.dev, "light", None)

    def _color_str(self):
        if self.color == (0, 0, 0):
            return "auto"
        return "%02x%02x%02x" % self.color

    def _sync_sw(self):
        # update swatch preview
        if self.color == (0, 0, 0):
            self.color_sw.config(bg=theme()["ENTRY_BG"])
        else:
            self.color_sw.config(bg="#%02x%02x%02x" % self.color)

    def set_state(self, on, level):
        self.on_key = "on" if on else "off"
        self.lv_key = level
        self.sw_var.set(t(self.on_key))
        self.lv_var.set(t(level))
        # 同步模式/速度/颜色控件
        if hasattr(self, "mode_var"):
            self.mode_var.set(t("mode_" + self.mode_key))
            self.speed_var.set(t("speed_" + self.speed_key))
            if hasattr(self, "color_var"):
                self.color_var.set(self._color_str())
                self._sync_sw()
        th = theme()
        self.badge.config(
            text="● " + (t("state_on") if on else t("state_off")),
            fg=th["FG_OK"] if on else th["FG_ERR"])

    def set_path(self, path):
        self.path_lbl.config(
            text=t("hidraw_detected", p=path) if path else t("hidraw_missing"))

    def retranslate(self):
        self.frame.config(text=" " + _dev_label(self.dev) + " ")
        self.sw.config(values=[t("on"), t("off")])
        self.lv.config(values=[t(x) for x in LEVELS])
        for code, b in self.btns.items():
            b.config(text=t(code))
        if hasattr(self, "mode_combo"):
            self.mode_combo.config(values=[t("mode_" + m) for m in MODE_LIST])
            self.speed_combo.config(values=[t("speed_" + s) for s in SPEED_LIST])
        self.set_state(self.on_key == "on", self.lv_key)

    def apply_theme(self):
        """C4: 热改卡片配色（LabelFrame 及子控件均可 config），不重建。"""
        th = theme()
        self.frame.config(bg=th["CARD_BG"], fg=th["FG"])
        for w in self._walk(self.frame):
            cls = w.winfo_class()
            try:
                if cls == "Frame":
                    w.config(bg=th["CARD_BG"])
                elif cls == "Label":
                    # 路径/徽章文字色按角色
                    fg = th["FG_OK"] if w is self.badge else th["FG_DIM"]
                    w.config(bg=th["CARD_BG"], fg=fg)
                elif cls == "Button":
                    w.config(bg=th["BTN_BG"], fg=th["FG"],
                             activebackground=th["HL_BG"],
                             activeforeground=th["FG"])
            except tk.TclError:
                pass
        # 徽章色按当前状态
        self.set_state(self.on_key == "on", self.lv_key)

    @staticmethod
    def _walk(w):
        out = []
        for c in w.winfo_children():
            out.append(c)
            out.extend(Card._walk(c))
        return out



# ---------- UI：设置对话框 ----------
def _row(parent, label_key, var, values, cb, pad):
    """一行 = 左标签 + 右下拉（C5 工厂）。label_key 是 i18n 键，存进 tk 属性供 retranslate 用。"""
    fr = tk.Frame(parent, bg=theme()["BG"])
    fr.pack(fill="x", **pad)
    lb = _lbl(fr, t(label_key), theme()["BG"], theme()["FG"])
    lb.pack(side="left")
    lb._i18n_key = label_key
    c = ttk.Combobox(fr, textvariable=var, values=values,
                     state="readonly", width=8)
    c.pack(side="right")
    c.bind("<<ComboboxSelected>>", cb)


def _check(parent, key, var, cmd, pad):
    cb = tk.Checkbutton(parent, text=t(key), variable=var,
                        bg=theme()["BG"], fg=theme()["FG"],
                        selectcolor=theme()["ENTRY_BG"],
                        activebackground=theme()["BG"], font=("Sans", 10),
                        command=cmd)
    cb._i18n_key = key
    cb.pack(fill="x", **pad)


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(t("settings_title"))
        self.resizable(False, False)
        self.configure(bg=theme()["BG"])
        self.transient(app)
        self.grab_set()

        s = app.settings
        th = theme()
        pad = {"padx": 16, "pady": 8}

        # 语言 / 主题（C5 row 工厂）
        self.lang_var = tk.StringVar(value=s.get("lang", "zh"))
        _row(self, "language", self.lang_var, ["zh", "en"],
             self._on_lang, pad)
        self.theme_var = tk.StringVar(value=s.get("theme", "dark"))
        _row(self, "theme", self.theme_var, ["dark", "light"],
             self._on_theme, pad)

        # 托盘 / 自启（C5 check 工厂）
        self.tray_var = tk.BooleanVar(value=s.get("tray", True))
        _check(self, "tray", self.tray_var, self._on_tray, pad)
        self.auto_var = tk.BooleanVar(value=autostart_enabled())
        _check(self, "autostart", self.auto_var, self._on_autostart, pad)

        # 版本
        ver_lbl = tk.Label(self, text=f"{t('version')} {APP_VERSION}",
                           bg=th["BG"], fg=th["FG_DIM"], font=("Sans", 9))
        ver_lbl._i18n_key = "version"
        ver_lbl.pack(fill="x", padx=16, pady=(4, 8))

        self._fb_lbl = tk.Label(self, text="", bg=th["BG"], fg=th["FG_OK"],
                                font=("Sans", 9), anchor="w")
        self._fb_lbl.pack(fill="x", padx=16)

        close_btn = tk.Button(self, text=t("close"), command=self.destroy,
                              bg=th["BTN_BG"], fg=th["FG"], relief="flat",
                              padx=16, pady=4)
        close_btn._i18n_key = "close"
        close_btn.pack(pady=(0, 12))

    def retranslate(self):
        """B5/S4: 对话框跟随语言切换（用 _i18n_key 属性定位控件）。"""
        self.title(t("settings_title"))
        for w in self._all_widgets(self):
            key = getattr(w, "_i18n_key", None)
            if not key:
                continue
            if key == "version":
                w.config(text=f"{t('version')} {APP_VERSION}")
            else:
                w.config(text=t(key))

    def retheme(self):
        """B5/S4: 对话框跟随主题（bg + 控件配色）。"""
        th = theme()
        self.configure(bg=th["BG"])
        for w in self._all_widgets(self):
            try:
                cls = w.winfo_class()
            except Exception:
                continue
            if cls == "Label":
                w.config(bg=th["BG"], fg=th["FG"])
            elif cls == "Button":
                w.config(bg=th["BTN_BG"], fg=th["FG"],
                         activebackground=th["HL_BG"], activeforeground=th["FG"])
            elif cls == "Checkbutton":
                w.config(bg=th["BG"], fg=th["FG"], selectcolor=th["ENTRY_BG"],
                         activebackground=th["BG"])
            elif cls == "Frame":
                w.config(bg=th["BG"])

    @staticmethod
    def _all_widgets(w):
        out = []
        for c in w.winfo_children():
            out.append(c)
            out.extend(SettingsDialog._all_widgets(c))
        return out

    def _on_lang(self, _e=None):
        self.app.settings["lang"] = self.lang_var.get()
        save_settings(self.app.settings)
        set_lang(self.app.settings["lang"])
        self.app.retranslate()
        self.retranslate()

    def _on_theme(self, _e=None):
        self.app.settings["theme"] = self.theme_var.get()
        save_settings(self.app.settings)
        set_theme(self.app.settings["theme"])
        self.app.retheme()
        self.retheme()

    def _on_tray(self):
        self.app.settings["tray"] = self.tray_var.get()
        save_settings(self.app.settings)
        self.app.apply_tray()

    def _on_autostart(self):
        enabled = self.auto_var.get()
        if set_autostart(enabled):
            self._feedback(t("autostart_on" if enabled else "autostart_off"))
        else:
            messagebox.showerror(t("settings_title"),
                                 t("fail", m=AUTOSTART_FILE), parent=self)

    def _feedback(self, msg):
        """对话框内短暂显示操作反馈。"""
        self._fb_lbl.config(text=msg)
        self.after(2500, lambda: self._fb_lbl.config(text=""))


# ---------- UI：主窗口 ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        set_lang(self.settings.get("lang", "zh"))
        set_theme(self.settings.get("theme", "dark"))

        self.title(t("app_title"))
        self.resizable(False, False)
        th = theme()
        self.configure(bg=th["BG"])

        self.backend = Backend()
        self.state = load_state()
        for d in DEVICES:
            self.state.setdefault(d, {"on": True, "level": "high"})

        self._set_window_icon()

        self._busy = False
        self.cards = {}
        self._themed = []   # (widget, role) 注册表，retheme 遍历（C7）
        self._build_ui()
        self._restore_ui()
        self._refresh_status()

        # 托盘
        self.tray = Tray(self)
        if self.settings.get("tray", True):
            self.tray.start()

        # 关窗 → 隐藏到托盘（若托盘开），否则退出
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_window_icon(self):
        p = _icon_path(self._overall_state(), 64)
        if p:
            try:
                self._tkimg = tk.PhotoImage(file=p)
                self.iconphoto(True, self._tkimg)
            except Exception:
                pass

    def _overall_state(self):
        ons = [self.state.get(d, {}).get("on", True) for d in DEVICES]
        if all(ons):
            return "on"
        if not any(ons):
            return "off"
        return "partial"

    def _reg(self, widget, role):
        """登记控件主题角色，retheme 统一刷新（C7）。"""
        self._themed.append((widget, role))
        return widget

    def _build_ui(self):
        th = theme()
        # 顶栏：标题 + 设置按钮
        self._top = tk.Frame(self, bg=th["BG"])
        self._top.pack(fill="x", padx=14, pady=(10, 4))
        top = self._top
        self.title_lbl = tk.Label(top, text=t("app_title"), fg=th["ACCENT"],
                                  bg=th["BG"], anchor="w",
                                  font=("Sans", 13, "bold"))
        self._reg(self.title_lbl, "title")
        self.title_lbl.pack(side="left")
        self.set_btn = tk.Button(top, text="⚙ " + t("settings"),
                                 command=self.open_settings,
                                 bg=th["BTN_BG"], fg=th["FG"], relief="flat",
                                 padx=8, pady=2)
        self._reg(self.set_btn, "btn")
        self.set_btn.pack(side="right")

        self.backend_lbl = tk.Label(top, text=t("backend"), fg=th["FG_OK"],
                                    bg=th["BG"], anchor="w", font=("Sans", 9))
        self._reg(self.backend_lbl, "backend")
        self.backend_lbl.pack(fill="x")

        self.status_lbl = tk.Label(top, text="", fg=th["FG"], bg=th["BG"],
                                   anchor="w", font=("Sans", 9))
        self._reg(self.status_lbl, "status")
        self.status_lbl.pack(fill="x", pady=(2, 0))

        # 两张卡
        for d in DEVICES:
            self.cards[d] = Card(self, d, self._on_change)

        # 底部按钮（C6 工厂）
        self._bot = tk.Frame(self, bg=th["BG"])
        self._bot.pack(fill="x", padx=14, pady=(4, 8))
        bot = self._bot
        mk = lambda txt, cmd: tk.Button(
            bot, text=txt, command=cmd, bg=th["BTN_BG"], fg=th["FG"],
            relief="flat", padx=10, pady=4,
            activebackground=th["HL_BG"], activeforeground=th["FG"])
        self.all_on_btn = self._reg(mk(t("all_on"), lambda: self._all(True)), "btn")
        self.all_on_btn.pack(side="left")
        self.all_off_btn = self._reg(mk(t("all_off"), lambda: self._all(False)), "btn")
        self.all_off_btn.pack(side="left", padx=(8, 0))
        self.refresh_btn = self._reg(mk(t("refresh"), self._refresh_status), "btn")
        self.refresh_btn.pack(side="right")

        self.log_lbl = tk.Label(self, text="", fg=th["FG_INFO"], bg=th["BG"],
                                anchor="w", font=("Sans", 9), padx=14)
        self._reg(self.log_lbl, "log")
        self.log_lbl.pack(fill="x", pady=(0, 10))

    def _restore_ui(self):
        for d in DEVICES:
            st = self.state[d]
            self.cards[d].set_state(st.get("on", True), st.get("level", "high"))

    def _refresh_status(self):
        nodes = self.backend.refresh()
        parts = [f"{d}={nodes[d]}" for d in DEVICES if d in nodes]
        missing = [d for d in DEVICES if d not in nodes]
        if not parts:
            # 一个 Aura 设备都没找到（D3 no_backend）
            self.status_lbl.config(text=t("no_backend"))
        else:
            txt = t("status_fmt", n=len(parts), list="  ".join(parts))
            if missing:
                txt += t("missing", m=",".join(missing))
            self.status_lbl.config(text=txt)
        for d in DEVICES:
            self.cards[d].set_path(nodes.get(d))
        self._sync_tray_icon()

    def _on_change(self, dev, kind, value):
        if kind == "toggle":
            if value:
                lv = self.cards[dev].lv_var.get()
                inv = {t(x): x for x in LEVELS}
                lv = inv.get(lv, "high")
                self._exec(dev, True, lv)
            else:
                self._exec(dev, False, "off")
        elif kind == "level":
            if value == "off":
                self._exec(dev, False, "off")
            else:
                self._exec(dev, True, value)
        elif kind == "light":
            # 模式/颜色/速度改动：仅当灯已开时重下发
            st = self.state.get(dev, {"on": True})
            if st.get("on", True):
                lv = self.cards[dev].lv_key
                self._exec(dev, True, lv if lv != "off" else "high")

    def _run_async(self, call, on_ok, on_fail, rollback):
        """公共骨架：忙碌检查 → 线程执行 call() → 主线程回调（C8）。

        call: () -> (ok, msg)  在后台线程跑
        on_ok(msg)/on_fail(msg): 主线程回调
        rollback: () -> None   忙时或失败时把 UI 回滚到已知状态
        """
        if self._busy:
            rollback()
            self.log_lbl.config(text=t("executing"))
            return
        self._busy = True
        self.log_lbl.config(text=t("executing"))

        def worker():
            ok, msg = call()
            def done():
                self._busy = False
                (on_ok if ok else on_fail)(msg)
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _exec(self, dev, on, level):
        lvl_num = {"off": 0, "low": 1, "medium": 2, "high": 3}.get(level, 3)
        c = self.cards.get(dev)
        light = None
        if c is not None and on:
            light = {"mode": getattr(c, "mode_key", "static"),
                     "color": getattr(c, "color", (0, 0, 0)),
                     "speed": getattr(c, "speed_key", "normal")}

        def rollback():
            st = self.state.get(dev, {"on": True, "level": "high"})
            self.cards[dev].set_state(st.get("on", True), st.get("level", "high"))

        def ok(msg):
            self.state[dev] = {"on": on, "level": level}
            save_state(self.state)
            self.cards[dev].set_state(on, level)
            self.log_lbl.config(
                text=f"{_dev_label(dev)} → {t(level if on else 'off')}")
            self._sync_tray_icon()

        def fail(msg):
            self.log_lbl.config(text=t("fail", m=msg))
            rollback()

        self._run_async(lambda: self.backend.set_device(dev, on, lvl_num, light),
                        ok, fail, rollback)

    def _dev_label(self, dev):
        return _dev_label(dev)

    def _all(self, on):
        lvl = 3 if on else 0
        level_key = "high" if on else "off"

        def rollback():
            for d in DEVICES:
                st = self.state.get(d, {"on": True, "level": "high"})
                self.cards[d].set_state(st.get("on", True), st.get("level", "high"))

        def ok(msg):
            for d in DEVICES:
                self.state[d] = {"on": on, "level": level_key}
                self.cards[d].set_state(on, level_key)
            save_state(self.state)
            self.log_lbl.config(text=msg)
            self._sync_tray_icon()

        def fail(msg):
            self.log_lbl.config(text=t("fail", m=msg))
            rollback()

        self._run_async(lambda: self.backend.set_all(on, lvl),
                        ok, fail, rollback)

    # ---------- 托盘联动 ----------
    def _sync_tray_icon(self):
        st = self._overall_state()
        if getattr(self, "tray", None) and self.tray.ind:
            self.tray._update_icon(st)
        self._set_window_icon()

    def toggle_from_tray(self, dev):
        cur = self.state.get(dev, {}).get("on", True)
        lvl = self.state.get(dev, {}).get("level", "high")
        if cur:
            self._exec(dev, False, "off")
        else:
            self._exec(dev, True, lvl if lvl != "off" else "high")

    def apply_tray(self):
        if self.settings.get("tray", True):
            self.tray.start()
        else:
            self.tray.stop()

    def open_settings(self):
        """S6 单例：已有对话框则聚焦，避免 grab_set 冲突。"""
        for w in self.winfo_children():
            if isinstance(w, SettingsDialog):
                w.deiconify()
                w.lift()
                w.focus_force()
                return
        SettingsDialog(self)

    def show_window(self):
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.focus_force()
        # 短暂置顶后清除，避免永久悬浮（P10）
        self.after(400, lambda: self.attributes("-topmost", False))

    def _on_close(self):
        if self.settings.get("tray", True) and self.tray.ind:
            self.withdraw()
        else:
            self.shutdown()

    def shutdown(self):
        """安全退出（不覆盖 Tk 内建 quit，P9）。"""
        try:
            self.tray.stop()
        except Exception:
            pass
        self.destroy()

    # ---------- 语言/主题即时切换 ----------
    def retranslate(self):
        self.title(t("app_title"))
        self.title_lbl.config(text=t("app_title"))
        self.set_btn.config(text="⚙ " + t("settings"))
        self.backend_lbl.config(text=t("backend"))
        self.all_on_btn.config(text=t("all_on"))
        self.all_off_btn.config(text=t("all_off"))
        self.refresh_btn.config(text=t("refresh"))
        for d in DEVICES:
            self.cards[d].retranslate()
        self._refresh_status()

    def retheme(self):
        th = theme()
        self.configure(bg=th["BG"])
        # 容器 Frame（P5）+ 注册表遍历（C7）
        self._top.config(bg=th["BG"])
        self._bot.config(bg=th["BG"])
        fg_by_role = {"title": th["ACCENT"], "backend": th["FG_OK"],
                      "status": th["FG"], "log": th["FG_INFO"]}
        for w, role in self._themed:
            w.config(bg=th["BG"])
            if role == "btn":
                w.config(bg=th["BTN_BG"], fg=th["FG"],
                         activebackground=th["HL_BG"], activeforeground=th["FG"])
            elif role in fg_by_role:
                w.config(fg=fg_by_role[role])
        # C4: 卡片热改配色，不销毁重建（避免 pack 乱序 + 保留状态）
        for d in DEVICES:
            self.cards[d].apply_theme()
        self._refresh_status()
# ---------- 自测 ----------
def selftest():
    """不开 GUI 验证链路：找设备 → 关后盖(键盘不动) → 开后盖 → 关键盘 → 全开。"""
    _i18n_check()
    print("OK   i18n keys consistent")
    # 协议字节校验（_mode_pkt/_commit_pkts/_power_pkt 结构）
    mp = AuraDevice._mode_pkt(1, MODES["static"], 255, 0, 0,
                              SPEEDS["normal"])
    assert mp[0] == AURA_ID and mp[1] == CMD_MODE and mp[2] == 1, \
        f"mode pkt hdr: {mp[:3].hex()}"
    assert mp[3] == 0 and mp[4:7] == bytes([255, 0, 0]), f"mode color: {mp[:7].hex()}"
    assert mp[9] == 0x00, f"static rand: {mp[9]:#x}"
    # 全 0 色 → rand=0xFF（设备自选）
    z = AuraDevice._mode_pkt(0, MODES["cycle"], 0, 0, 0, SPEEDS["slow"])
    assert z[9] == 0xFF, f"cycle rand: {z[9]:#x}"
    # breathe → rand=0x01
    br = AuraDevice._mode_pkt(0, MODES["breathe"], 0, 255, 255,
                              SPEEDS["fast"], 0, 0, 255)
    assert br[9] == 0x01, f"breathe rand: {br[9]:#x}"
    cks = AuraDevice._commit_pkts()
    assert len(cks) == 2 and cks[0][1] == CMD_SET and cks[1][1] == CMD_APPLY
    pp = AuraDevice._power_pkt(*POWER_ON)
    assert pp[1] == CMD_POWER and pp[3:7] == bytes(POWER_ON)
    print("OK   protocol bytes (mode/commit/power)")
    b = Backend()
    nodes = b.list_nodes()
    print("节点:", nodes)
    bad = 0
    if "rear" not in nodes:
        print("FAIL: 未找到后盖设备 (PID 0x18C6)")
        bad += 1
    if "keyboard" not in nodes:
        print("FAIL: 未找到键盘设备 (PID 0x1A30)")
        bad += 1
    if bad:
        return 1

    steps = [
        ("关后盖(键盘不动)", lambda: b.set_device("rear", False)),
        ("开后盖", lambda: b.set_device("rear", True, 3)),
        ("关键盘", lambda: b.set_device("keyboard", False)),
        ("全开", lambda: b.set_all(True, 3)),
    ]
    for name, fn in steps:
        ok, msg = fn()
        mark = "OK  " if ok else "FAIL"
        print(f"{mark} {name}: {msg}")
        if not ok:
            bad += 1
    print("自测通过" if bad == 0 else f"自测失败 {bad} 步")
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser(prog=APP_NAME, description=t("app_title"))
    ap.add_argument("--selftest", action="store_true",
                    help="run link self-test and exit")
    ap.add_argument("--version", action="version",
                    version=f"{APP_NAME} {APP_VERSION}")
    ap.add_argument("--geometry", metavar="WxH+X+Y",
                    help="window geometry (default: centered)")
    ap.add_argument("--open-settings", action="store_true",
                    help="open the settings dialog on start")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    app = App()
    try:
        app.update_idletasks()
        if args.geometry:
            app.geometry(args.geometry)
        else:
            w, h = app.winfo_width(), app.winfo_height()
            sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
            app.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
    except tk.TclError:
        pass
    if args.open_settings:
        app.after(600, app.open_settings)
    app.mainloop()


if __name__ == "__main__":
    main()