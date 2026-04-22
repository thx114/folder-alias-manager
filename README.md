# Folder Alias Manager

Windows 文件夹别名与图标管理工具。通过修改 `desktop.ini` 自定义文件夹在资源管理器中显示的名称和图标，支持拖放操作、自动提取 EXE 图标、智能提权写入。

## 功能特性

- **拖放导入** — 直接拖入文件夹，或点击选择
- **别名修改** — 自定义文件夹在资源管理器中显示的名称
- **图标自定义** — 从文件夹内 EXE/DLL 自动提取图标，或选择系统图标、自定义图标文件
- **图标去重** — 像素级比较，自动跳过重复图标
- **智能提权** — 遇到权限不足的文件夹自动调用辅助工具提权写入，仅弹一次 UAC 提示
- **多编码兼容** — 自动处理 GBK / UTF-8 / UTF-16 编码的 desktop.ini
- **Shell 刷新** — 写入后自动通知资源管理器刷新显示

## 快速开始

### 运行环境

- Windows 10 / 11
- Python 3.10+

### 安装依赖

```bash
pip install -r requirements.txt
```

### 直接运行

```bash
python folder_alias_manager.py
```

### 打包为 EXE

```bash
python build.py
```

打包产物在 `dist/` 目录下：

| 文件                       | 说明               |
| ------------------------ | ---------------- |
| `FolderAliasManager.exe` | 主程序（普通权限，支持拖放）   |
| `DesktopIniHelper.exe`   | 提权辅助工具（需与主程序同目录） |

## 使用方法

1. 启动程序，将文件夹拖入窗口（或点击拖放区域选择）
2. 在表格中编辑文件夹别名
3. 从图标下拉框中选择图标（支持从 EXE 提取、系统图标、自定义图标文件）
4. 点击 **保存更改**
5. 如果部分文件夹需要管理员权限，会自动弹出 UAC 提示

## 架构

```
┌─────────────────────────────┐
│   FolderAliasManager.exe    │  普通用户权限，支持拖放
│   folder_alias_manager.py   │
│                             │
│   1. 尝试直接写入             │
│   2. 遇到 PermissionError    │
│      → 收集失败项             │
│      → 调用提权工具（批量）     │
└──────────┬──────────────────┘
           │ ShellExecuteExW + runas
           ▼
┌─────────────────────────────┐
│   DesktopIniHelper.exe      │  管理员权限
│   desktop_ini_helper.py     │
│                             │
│   读取 JSON → 批量写入        │
│   → 写结果 JSON → 退出        │
└─────────────────────────────┘
```

**双进程设计**：主程序保持普通权限运行（确保拖放功能正常），仅在遇到权限不足时通过 `ShellExecuteExW` 启动辅助工具提权写入，所有待处理项合并为一次 UAC 提示。

## 项目结构

```
├── folder_alias_manager.py   # 主程序（GUI + 核心逻辑）
├── desktop_ini_helper.py     # 提权辅助工具
├── build.py                  # PyInstaller 打包脚本
├── requirements.txt          # 依赖声明
├── .gitignore
└── desktop.ini               # 示例配置
```

## 依赖

| 包                  | 用途      |
| ------------------ | ------- |
| PyQt6 >= 6.11.0    | GUI 框架  |
| PyInstaller >= 6.0 | 打包为 EXE |

## 注意事项

- `DesktopIniHelper.exe` 必须与 `FolderAliasManager.exe` 放在同一目录下
- desktop.ini 生效需要文件夹具有只读属性（程序会自动设置）
- 修改后可能需要稍等片刻或刷新资源管理器才能看到变化
- 部分系统保护目录（如 `C:\Windows`）即使提权也可能无法修改

## License

MIT
