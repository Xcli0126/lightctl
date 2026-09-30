#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lightctl — ROG Flow Z13 (GZ302EA) 灯光控制 GUI

两路独立控制，各自按 USB Product ID 精准路由，互不干扰：
  背光（后部灯条 / rear glow, USB 0b05:18c6, HIDID 0x18C6）
  键盘灯（keyboard,          USB 0b05:1a30, HIDID 0x1A30）

实现方式：直接向 /dev/hidrawN 写 64 字节 Aura 输出报告（Report ID 0x5d），
不再依赖 z13ctl 二进制。协议逆向自 g-helper (MIT) 与 z13ctl PROTOCOL.md。

设计要点（取自 g-helper-linux，修掉 z13ctl 的缺陷）：
  1. 按 PID 路由：后盖只写 0x18C6，键盘只写 0x1A30，杜绝误伤。
  2. Power 按位控制：[0x5D,0xBD,0x01,keyb,bar,lid,rear,0xFF]，
     关后盖 = bar/lid/rear 清 0、keyb 保持 0xFF，键盘完全不受影响。
  3. 后盖是独立 Aura 设备（PID 0x18C6），可单独设 mode/color。
  4. udev 免 root（z13ctl setup 装的规则已覆盖两个 PID）。
"""

import ctypes
import ctypes.util
import glob
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

APP_NAME = "lightctl"
STATE_FILE = os.path.expanduser("~/.config/lightctl/state.json")

# Aura 协议常量
AURA_ID = 0x5D
HIDIOCGRDESCSIZE = 0x80044801
HIDIOCGRDESC = 0x90044802
REPORT_SIZE = 64

# USB Product ID -> 逻辑设备名
PID_REAR = "18C6"
PID_KEYBOARD = "1A30"
DEVICES = ("rear", "keyboard")
DEVICE_ZH = {"rear": "背光（后部灯条）", "keyboard": "键盘灯"}

LEVELS = ["off", "low", "medium", "high"]
LEVEL_ZH = {"off": "关", "low": "低", "medium": "中", "high": "高"}
ZH_LEVEL = {v: k for k, v in LEVEL_ZH.items()}

BG = "#1e1e2e"
CARD_BG = "#313244"
BTN_BG = "#45475a"
FG = "#cdd6f4"
FG_DIM = "#a6adc8"
FG_OK = "#a6e3a1"
FG_ERR = "#f38ba8"
FG_INFO = "#89b4fa"

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


class _Desc(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("value", ctypes.c_uint8 * 4096)]


def _has_aura(fd):
    """检查该 hidraw 节点的报告描述符里有没有 Aura Report ID 0x5d。"""
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
    """扫描 /sys/class/hidraw，按 PID 分类返回 {dev: path}，只含带 Aura 的节点。"""
    out = {}
    for ue in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        try:
            txt = open(ue).read()
        except OSError:
            continue
        if "00000B05" not in txt:
            continue
        hidid = next((l for l in txt.splitlines() if l.startswith("HID_ID=")), "")
        if not hidid:
            continue
        prod = hidid.split("=")[-1].split(":")[-1].upper().lstrip("0")
        if prod not in (PID_REAR, PID_KEYBOARD):
            continue
        dev = ue.split("/")[4]
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
    """构造 64 字节 Aura 输出报告，首字节为 Report ID 0x5d。"""
    b = bytearray(REPORT_SIZE)
    b[0] = AURA_ID
    for i, x in enumerate(body, 1):
        b[i] = x
    return bytes(b)


class AuraDevice:
    """一个 Aura HID 设备（按 PID 打开的单个 hidraw 节点）。"""

    def __init__(self, path):
        self.path = path

    def _open(self):
        return os.open(self.path, os.O_RDWR)

    def _write(self, data):
        fd = self._open()
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def _write_seq(self, packets):
        fd = self._open()
        try:
            for p in packets:
                os.write(fd, p)
                time.sleep(0.01)
        finally:
            os.close(fd)

    def init(self):
        """Aura 初始化序列（唤醒 + 识别 + 配置 + Z13 动态灯效）。"""
        self._write_seq([
            _pkt(0xB9),
            b"]ASUS Tech.Inc.",
            _pkt(0x05, 0x20, 0x31, 0x00, 0x1A),
            _pkt(0xC0, 0x03, 0x01),
        ])

    def set_power(self, keyb, bar, lid, rear):
        """按位设置 power 区。关后盖时 bar/lid/rear=0、keyb=0xFF。"""
        self._write(_pkt(0xBD, 0x01, keyb, bar, lid, rear, 0xFF))

    def set_brightness(self, level):
        """设置亮度 0-3（只对键盘设备有意义，后盖设备忽略）。"""
        self._write(_pkt(0xBA, 0xC5, 0xC4, level))

    def set_mode(self, zone, mode, r, g, b, r2=0, g2=0, b2=0,
                 speed=0xEB, rand=0):
        """设置某 zone 的颜色/模式，随后 commit（SET + APPLY）。"""
        if r == 0 and g == 0 and b == 0 and mode != 1:
            rand = 0xFF
        elif mode == 1:
            rand = 0x01
        self._write_seq([
            _pkt(0xB3, zone, mode, r, g, b, speed, 0x00, rand, r2, g2, b2),
            _pkt(0xB5),
            _pkt(0xB4),
        ])

    def turn_off(self):
        """关掉这个设备控制的灯：power 全关 + 亮度 0。"""
        self.init()
        time.sleep(0.05)
        self.set_power(0x00, 0x00, 0x00, 0x00)
        self.set_brightness(0)

    def turn_on(self, level=3):
        """打开这个设备：power 全开 + 恢复亮度。"""
        self.init()
        time.sleep(0.05)
        self.set_power(0xFF, 0x1F, 0xFF, 0xFF)
        self.set_brightness(level)


class Backend:
    """按 PID 路由到具体 Aura 设备，串行执行避免并发写。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.nodes = find_aura_nodes()

    def refresh(self):
        with self._lock:
            self.nodes = find_aura_nodes()
        return self.nodes

    def _dev(self, name):
        path = self.nodes.get(name)
        if not path:
            return None
        return AuraDevice(path)

    def set_device(self, name, on, level=3):
        """开/关单个设备。name in DEVICES。"""
        with self._lock:
            if name not in self.nodes:
                return False, f"未找到 {name} 设备"
            dev = AuraDevice(self.nodes[name])
            try:
                if on:
                    dev.turn_on(level)
                else:
                    dev.turn_off()
                return True, f"{DEVICE_ZH.get(name, name)} → {'开' if on else '关'}"
            except OSError as e:
                return False, str(e)

    def set_all(self, on, level=3):
        """开/关全部设备，返回 (ok, msg)。"""
        errs = []
        with self._lock:
            for name in DEVICES:
                if name not in self.nodes:
                    errs.append(f"缺 {name}")
                    continue
                dev = AuraDevice(self.nodes[name])
                try:
                    if on:
                        dev.turn_on(level)
                    else:
                        dev.turn_off()
                except OSError as e:
                    errs.append(f"{name}: {e}")
        if errs:
            return False, "; ".join(errs)
        return True, "全部已打开" if on else "全部已关闭"

    def list_nodes(self):
        with self._lock:
            return dict(self.nodes)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
            if isinstance(s, dict):
                return s
    except Exception:
        pass
    return {}


def save_state(s):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class Card:
    """一路灯光的控制卡。"""

    def __init__(self, parent, dev, on_change):
        self.dev = dev
        self.on_change = on_change

        self.frame = tk.LabelFrame(
            parent, text=f" {DEVICE_ZH[dev]} ",
            bg=CARD_BG, fg=FG, font=("Sans", 11, "bold"),
            padx=12, pady=10,
        )
        self.frame.pack(fill="x", padx=14, pady=6)

        r1 = tk.Frame(self.frame, bg=CARD_BG)
        r1.pack(fill="x")
        tk.Label(r1, text="开关", bg=CARD_BG, fg=FG_DIM,
                 font=("Sans", 10)).pack(side="left")
        self.sw_var = tk.StringVar(value="开")
        sw = ttk.Combobox(r1, textvariable=self.sw_var, values=["开", "关"],
                          state="readonly", width=5, font=("Sans", 10))
        sw.pack(side="right")
        sw.bind("<<ComboboxSelected>>", self._toggle)

        r2 = tk.Frame(self.frame, bg=CARD_BG)
        r2.pack(fill="x", pady=(8, 0))
        tk.Label(r2, text="亮度", bg=CARD_BG, fg=FG_DIM,
                 font=("Sans", 10)).pack(side="left")
        self.lv_var = tk.StringVar(value="高")
        lv = ttk.Combobox(r2, textvariable=self.lv_var,
                          values=[LEVEL_ZH[x] for x in LEVELS],
                          state="readonly", width=5, font=("Sans", 10))
        lv.pack(side="right")
        lv.bind("<<ComboboxSelected>>", self._level)

        r3 = tk.Frame(self.frame, bg=CARD_BG)
        r3.pack(fill="x", pady=(10, 0))
        for txt, code in (("关", "off"), ("低", "low"), ("中", "medium"), ("高", "high")):
            tk.Button(r3, text=txt, width=4,
                      command=lambda c=code: self.on_change(self.dev, "level", c),
                      bg=BTN_BG, fg=FG, relief="flat", padx=2, pady=2
                      ).pack(side="left", padx=(0, 6))

        # 设备路径显示
        self.path_lbl = tk.Label(self.frame, text="", bg=CARD_BG, fg=FG_DIM,
                                 font=("Mono", 8), anchor="w")
        self.path_lbl.pack(fill="x", pady=(8, 0))

    def _toggle(self, _event=None):
        self.on_change(self.dev, "toggle", self.sw_var.get() == "开")

    def _level(self, _event=None):
        self.on_change(self.dev, "level", ZH_LEVEL.get(self.lv_var.get(), "high"))

    def set_state(self, on, level):
        self.sw_var.set("开" if on else "关")
        self.lv_var.set(LEVEL_ZH.get(level, "高"))

    def set_path(self, path):
        self.path_lbl.config(text=f"hidraw: {path}" if path else "hidraw: 未检测到")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — Z13 灯光控制")
        self.resizable(False, False)
        self.configure(bg=BG)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.lift()
        self.focus_force()

        self.backend = Backend()
        self.state = load_state()
        for d in DEVICES:
            self.state.setdefault(d, {"on": True, "level": "high"})

        self._busy = False
        self._build_ui()
        self._restore_ui()
        self._refresh_status()

    def _build_ui(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(top, text="后端：Aura HID 直写（按 PID 路由）", fg=FG_OK,
                 bg=BG, anchor="w", font=("Sans", 9)).pack(fill="x")
        self.status_lbl = tk.Label(top, text="", fg=FG, bg=BG, anchor="w",
                                   font=("Sans", 9))
        self.status_lbl.pack(fill="x", pady=(4, 0))

        self.cards = {d: Card(self, d, self._on_change) for d in DEVICES}

        bot = tk.Frame(self, bg=BG)
        bot.pack(fill="x", padx=14, pady=(4, 8))
        tk.Button(bot, text="全部打开", command=lambda: self._all(True),
                  bg=BTN_BG, fg=FG, relief="flat", padx=10, pady=4).pack(side="left")
        tk.Button(bot, text="全部关闭", command=lambda: self._all(False),
                  bg=BTN_BG, fg=FG, relief="flat", padx=10, pady=4).pack(side="left", padx=(8, 0))
        tk.Button(bot, text="刷新状态", command=self._refresh_status,
                  bg=BTN_BG, fg=FG, relief="flat", padx=10, pady=4).pack(side="right")

        self.log_lbl = tk.Label(self, text="", fg=FG_INFO, bg=BG, anchor="w",
                                font=("Sans", 9), padx=14)
        self.log_lbl.pack(fill="x", pady=(0, 10))

    def _restore_ui(self):
        for d in DEVICES:
            st = self.state[d]
            self.cards[d].set_state(st.get("on", True), st.get("level", "high"))

    def _refresh_status(self):
        nodes = self.backend.refresh()
        parts = [f"{d}={nodes[d]}" for d in DEVICES if d in nodes]
        missing = [d for d in DEVICES if d not in nodes]
        txt = f"Aura 接口 {len(parts)}/2：" + "  ".join(parts)
        if missing:
            txt += f"  (缺: {','.join(missing)})"
        self.status_lbl.config(text=txt)
        for d in DEVICES:
            self.cards[d].set_path(nodes.get(d))

    def _on_change(self, dev, kind, value):
        if kind == "toggle":
            if value:
                lv = ZH_LEVEL.get(self.cards[dev].lv_var.get(), "high")
                self._exec(dev, True, lv, f"{DEVICE_ZH[dev]} → {LEVEL_ZH[lv]}")
            else:
                self._exec(dev, False, "off", f"{DEVICE_ZH[dev]} → 关")
        elif kind == "level":
            lv = value
            if lv == "off":
                self._exec(dev, False, "off", f"{DEVICE_ZH[dev]} → 关")
            else:
                self._exec(dev, True, lv, f"{DEVICE_ZH[dev]} → {LEVEL_ZH[lv]}")

    def _exec(self, dev, on, level, ok_msg):
        if self._busy:
            return
        self._busy = True
        self.log_lbl.config(text="执行中…")
        lvl_num = {"off": 0, "low": 1, "medium": 2, "high": 3}.get(level, 3)

        def worker():
            ok, msg = self.backend.set_device(dev, on, lvl_num)
            def done():
                self._busy = False
                if ok:
                    self.state[dev] = {"on": on, "level": level}
                    save_state(self.state)
                    self.cards[dev].set_state(on, level)
                    self.log_lbl.config(text=ok_msg)
                else:
                    self.log_lbl.config(text=f"失败：{msg}")
                    st = self.state[dev]
                    self.cards[dev].set_state(st["on"], st["level"])
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _all(self, on):
        if self._busy:
            return
        self._busy = True
        self.log_lbl.config(text="全部打开…" if on else "全部关闭…")
        lvl = 3 if on else 0
        level_key = "high" if on else "off"

        def worker():
            ok, msg = self.backend.set_all(on, lvl)
            def done():
                self._busy = False
                if ok:
                    for d in DEVICES:
                        self.state[d] = {"on": on, "level": level_key}
                        self.cards[d].set_state(on, level_key)
                    save_state(self.state)
                    self.log_lbl.config(text=msg)
                else:
                    self.log_lbl.config(text=f"失败：{msg}")
                    for d in DEVICES:
                        st = self.state[d]
                        self.cards[d].set_state(st["on"], st["level"])
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()


def selftest():
    """无 GUI 链路自测：按 PID 找设备 + 关后盖(键盘不动) + 开后盖 + 全开。"""
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
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    app = App()
    try:
        app.update_idletasks()
        w, h = app.winfo_width(), app.winfo_height()
        sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
        app.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
    except tk.TclError:
        pass
    app.mainloop()


if __name__ == "__main__":
    main()
