# lightctl

ROG Flow Z13 (GZ302EA) 灯光控制 GUI —— 独立开关背光（后部灯条）和键盘灯，互不干扰。

## 解决什么问题

这台机器有两路独立的 Aura 灯光，各自是独立的 USB HID 设备：

| 灯 | USB Product ID | hidraw | 说明 |
|----|----------------|--------|------|
| 背光（后部灯条） | `0b05:18c6` | `/dev/hidraw9` | 机身后盖的发光窗，独立 Aura 设备 |
| 键盘灯 | `0b05:1a30` | `/dev/hidraw4` | 磁吸键盘背光 |

常见工具（z13ctl 的 `off`、OpenRGB 的全关）会把两路一起灭掉。**lightctl 让你只关后盖灯、键盘照常亮**，或者反过来。

## 为什么不用现成工具

拉了四个同类项目读了源码，结论：

- **z13ctl** —— `off` 是全关（power 包四字节全 0），没有"只关后盖"的粒度
- **g-helper-linux** —— 有按位 power 控制和后盖独立设备，但整个是 C#/Avalonia/.NET，为一个开关装 .NET 不值
- **roguex** —— C++/GTK，重
- **AsusTUFBacklitColorChanger** —— 走 sysfs `kbd_rgb_mode`，Z13 没这个节点（实测为空），且全程 `pkexec`

lightctl 取了 g-helper 的协议精华（按 PID 路由 + 按位 power），用纯 Python 单文件实现，零第三方依赖。

## 实现要点

- **按 USB Product ID 路由**：后盖只写 `0x18c6` 的 hidraw，键盘只写 `0x1a30`，从根上杜绝误伤（z13ctl 是全广播靠 zone 字节碰运气）
- **按位 power 控制**：`[0x5D,0xBD,0x01,keyb,bar,lid,rear,0xFF]`，关后盖 = `bar/lid/rear` 清 0、`keyb` 保持 `0xFF`，键盘完全不受影响
- **Aura HID 直写**：64 字节输出报告（Report ID `0x5d`），直接写 `/dev/hidrawN`，不需要 z13ctl 二进制、不需要 .NET、不需要 root（靠 udev 规则）
- **协议逆向自** [g-helper](https://github.com/seerge/g-helper)（MIT）和 [z13ctl PROTOCOL.md](https://github.com/dahui/z13ctl)

## 安装

```bash
# 1. 克隆
git clone https://github.com/YOUR_NAME/lightctl.git
cd lightctl

# 2. 装 udev 规则（免 root 访问 hidraw，一次性）
sudo tee /etc/udev/rules.d/99-lightctl.rules > /dev/null <<'EOF'
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="18c6", MODE="0660", GROUP="users"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="1a30", MODE="0660", GROUP="users"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=hidraw

# 3. 确保自己在 users 组（通常默认就在）
id -nG | grep -qw users || sudo usermod -aG users $USER

# 4. 跑
python3 lightctl.py
```

## 使用

- **开关**下拉：开 / 关单路灯
- **亮度**下拉 + **关/低/中/高**快捷按钮：键盘灯 4 档；后盖灯只响应开关（它没有亮度概念，是独立 mode/color 设备）
- **全部打开 / 全部关闭**：两路一起
- **刷新状态**：重新扫描 hidraw 设备（磁吸键盘重插后用）

## 自测

不开 GUI 验证链路：

```bash
python3 lightctl.py --selftest
```

输出四步全 OK 即链路通：

```
节点: {'keyboard': '/dev/hidraw4', 'rear': '/dev/hidraw9'}
OK   关后盖(键盘不动): 背光（后部灯条） → 关
OK   开后盖: 背光（后部灯条） → 开
OK   关键盘: 键盘灯 → 关
OK   全开: 全部已打开
自测通过
```

## 状态持久化

开关状态存 `~/.config/lightctl/state.json`，重启 GUI 自动恢复上次的开关/亮度显示。

## 系统要求

- Linux + systemd（udev）
- Python 3（只用标准库：tkinter / ctypes / json）
- ASUS ROG Flow Z13 2025 (GZ302EA)，其它机型的 Aura 设备理论上也走这套协议，但没实测

## 卸载

```bash
sudo rm /etc/udev/rules.d/99-lightctl.rules
sudo udevadm control --reload-rules
rm ~/.local/share/applications/lightctl.desktop   # 如果装了桌面入口
```

## 许可

MIT。协议逆向自 g-helper（MIT）与 z13ctl。

## 致谢

- [seerge/g-helper](https://github.com/seerge/g-helper) —— Aura 协议与后盖灯独立设备的发现
- [dahui/z13ctl](https://github.com/dahui/z13ctl) —— PROTOCOL.md 与 udev setup 思路
