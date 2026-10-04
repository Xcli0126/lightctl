# lightctl

ROG Flow Z13 (GZ302EA) 灯光控制 GUI —— 独立开关背光（后部灯条）和键盘灯，互不干扰。

## 功能

- 两路独立开关 + 各 4 档亮度（关/低/中/高），互不干扰（关后盖不灭键盘）
- **颜色 + 灯效模式**：每路可独立选 颜色（hex / auto）和模式（常亮/呼吸/循环/彩虹/频闪）+ 速度（慢/中/快）
- **状态栏（托盘）常驻**：图标随灯光状态变色（绿=全开 / 红=全关 / 橙=部分），菜单可直接开关两路灯
- **设置**：界面语言（中/英）、主题（深色/浅色）、托盘开关、开机自启动，全部即时生效
- 窗口图标 + 托盘图标由 PIL 程序化绘制（灯泡 + 状态点），无外部图片资源
- 关窗自动隐藏到托盘（托盘开启时），从托盘菜单退出
- `--selftest` 不开 GUI 验证全链路，`--version` 看版本

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

lightctl 取了 g-helper 的协议精华（按 PID 路由 + 按位 power），核心用纯 Python 单文件实现，无 z13ctl/.NET/C++ 依赖；托盘和图标用了 PyGObject/Pillow，缺失时自动降级不影响主功能。

## 实现要点

- **按 USB Product ID 路由**：后盖只写 `0x18c6` 的 hidraw，键盘只写 `0x1a30`，从根上杜绝误伤（z13ctl 是全广播靠 zone 字节碰运气）
- **按位 power 控制**：`[0x5D,0xBD,0x01,keyb,bar,lid,rear,0xFF]`，关后盖 = `bar/lid/rear` 清 0、`keyb` 保持 `0xFF`，键盘完全不受影响
- **Aura HID 直写**：64 字节输出报告（Report ID `0x5d`），直接写 `/dev/hidrawN`，不需要 z13ctl 二进制、不需要 .NET、不需要 root（靠 udev 规则）
- **协议逆向自** [g-helper](https://github.com/seerge/g-helper)（MIT）和 [z13ctl PROTOCOL.md](https://github.com/dahui/z13ctl)
- **开灯序列**：与 z13ctl `Apply()` 一致 —— 每个物理设备发**两个 zone** 的 SetMode+Commit（12 包）。只发自己 zone 的 9 包实测会让灯保持全关（2026-10-03 实测）

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

# 5.（可选）装桌面入口 / 应用菜单
install -Dm755 lightctl.py ~/.local/bin/lightctl
sed "s|^Exec=.*|Exec=$HOME/.local/bin/lightctl|" lightctl.desktop \
    > ~/.local/share/applications/lightctl.desktop
```

## 使用

- **开关**下拉：开 / 关单路灯
- **亮度**下拉 + **关/低/中/高**快捷按钮：两路各 4 档（后盖灯是独立 mode/color 设备，亮度档对其效果可能有限）
- **全部打开 / 全部关闭**：两路一起（带上各自卡片当前选的模式/速度/颜色；一台成功一台失败时只回滚失败的那台，日志提示"部分完成"）
- **刷新状态**：重新扫描 hidraw 设备（磁吸键盘重插后用）
- **⚙ 设置**：切换语言、主题、托盘常驻、开机自启
- 托盘图标：左键点菜单开关两路灯 / 显示主窗口 / 打开设置 / 退出；主窗口关闭时最小化到托盘

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

开关状态存 `~/.config/lightctl/state.json`（带 `schema` 版本号，加载时自动丢弃旧版遗留键并校验取值），
重启 GUI 自动恢复上次的开关/亮度显示。配置文件用「临时文件 + rename」原子写入，断电或被杀不会留半截 JSON。

## 系统要求

- Linux + systemd（udev）
- Python 3（tkinter / ctypes / json 标准库；托盘需 PyGObject + AyatanaAppIndicator3，图标需 Pillow，均非必需——缺失时自动降级）
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