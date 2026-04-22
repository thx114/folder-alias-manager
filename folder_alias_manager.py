import os
import sys
import json
import ctypes
from ctypes import wintypes
import shutil
import tempfile
import traceback
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Tuple
import subprocess

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QComboBox, QHeaderView, QMessageBox, QStyledItemDelegate,
    QAbstractItemView, QStyle, QStyleOptionComboBox, QDialog, QFileDialog,
    QListView, QTreeView, QCheckBox, QMenu, QProgressDialog, QListWidget, QListWidgetItem
)
from PyQt6.QtCore import Qt, QUrl, QMimeData, pyqtSignal, QSize
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QPixmap, QImage, QColor

# 配置日志记录
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('folder_alias_manager.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# Windows API constants
SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST = 0x0000


def is_admin() -> bool:
    """检查是否具有管理员权限"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_as_admin():
    """以管理员权限重新运行程序"""
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 1
    )
    sys.exit()


def refresh_shell():
    """刷新资源管理器以显示更改 - 使用安全的方法"""
    # 只使用 SHChangeNotify，这是最安全可靠的官方方法
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    
    # 额外通知桌面刷新（如果有桌面窗口）
    try:
        hwnd_shell = ctypes.windll.user32.FindWindowW("Progman", None)
        if hwnd_shell:
            # 发送 WM_SETTINGCHANGE 消息
            ctypes.windll.user32.SendMessageTimeoutW(
                hwnd_shell, 0x001A, 0, 0,
                0x0002, 100, None  # SMTO_ABORTIFHUNG | 100ms 超时
            )
    except:
        pass


def force_take_ownership(file_path: str):
    """强制获取文件/文件夹权限（不递归）"""
    if os.path.exists(file_path):
        subprocess.run(
            f'icacls "{file_path}" /grant *S-1-1-0:F',
            shell=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        os.chmod(file_path, 0o666)
        subprocess.run(
            f'attrib -r -h -s "{file_path}"',
            shell=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )


def extract_icon_from_exe(exe_path: str, icon_index: int = 0, size: int = 32) -> Optional[QIcon]:
    """从 exe/dll 文件中提取图标 - 使用 PyQt6 的 QFileIconProvider"""
    try:
        # 使用 QFileIconProvider - 这是最成熟稳定的方案
        from PyQt6.QtWidgets import QFileIconProvider
        
        icon_provider = QFileIconProvider()
        
        # 对于 exe/dll 文件，使用 QFileInfo 获取图标
        from PyQt6.QtCore import QFileInfo
        file_info = QFileInfo(exe_path)
        
        if file_info.exists():
            # 获取文件图标
            icon = icon_provider.icon(file_info)
            if icon and not icon.isNull():
                return icon
        
        # 备用方案：尝试从文件中提取图标
        # 使用 Windows Shell 的 ExtractIconEx
        large_icon = wintypes.HICON()
        small_icon = wintypes.HICON()
        
        result = ctypes.windll.shell32.ExtractIconExW(
            exe_path, icon_index,
            ctypes.byref(large_icon),
            ctypes.byref(small_icon),
            1
        )
        
        if result <= 0:
            return None
        
        icon_handle = large_icon.value or small_icon.value
        if not icon_handle:
            return None
        
        # 使用 Qt 的 fromPixmap 方法
        try:
            # 尝试从 HICON 创建 QPixmap
            from PyQt6.QtGui import QBitmap
            pixmap = QApplication.primaryScreen().grabWindow(int(icon_handle))
            if not pixmap.isNull():
                icon = QIcon(pixmap)
                ctypes.windll.user32.DestroyIcon(icon_handle)
                if small_icon.value and small_icon.value != icon_handle:
                    ctypes.windll.user32.DestroyIcon(small_icon.value)
                return icon
        except:
            pass
        
        ctypes.windll.user32.DestroyIcon(icon_handle)
        if small_icon.value and small_icon.value != icon_handle:
            ctypes.windll.user32.DestroyIcon(small_icon.value)
            
        return None
        
    except Exception as e:
        logger.debug(f"提取图标失败 {exe_path}: {e}")
        return None


def find_exe_files(folder_path: str) -> List[str]:
    """查找文件夹中的所有 exe 文件"""
    exe_files = []
    try:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith('.exe'):
                    exe_files.append(os.path.join(root, file))
    except Exception as e:
        logger.error(f"搜索 exe 文件失败：{e}")
    return exe_files


def get_exe_icons(exe_path: str, max_icons: int = 5) -> List[Tuple[int, QIcon]]:
    """获取 exe 文件中的所有图标 - 带缓存和去重
    
    Args:
        max_icons: 最大提取图标数量，避免过多图标导致性能问题
    """
    icons = []
    try:
        # 限制提取的图标数量，避免过多
        for i in range(max_icons):
            icon = extract_icon_from_exe(exe_path, i)
            if icon and not icon.isNull():
                icons.append((i, icon))
            else:
                # 如果连续失败，停止尝试
                if i > 0:
                    break
    except Exception as e:
        logger.error(f"获取图标失败 {exe_path}: {e}")
    return icons


def icons_are_same(icon1: QIcon, icon2: QIcon) -> bool:
    """判断两个图标是否相同"""
    if icon1.isNull() and icon2.isNull():
        return True
    if icon1.isNull() or icon2.isNull():
        return False
    
    # 比较不同尺寸下的图标
    for size in [16, 32, 48, 64]:
        pixmap1 = icon1.pixmap(size, size)
        pixmap2 = icon2.pixmap(size, size)
        
        if pixmap1.isNull() and pixmap2.isNull():
            continue
        if pixmap1.isNull() or pixmap2.isNull():
            return False
        
        # 转换为 QImage 进行比较
        image1 = pixmap1.toImage()
        image2 = pixmap2.toImage()
        
        if image1.size() != image2.size():
            return False
        
        # 比较像素数据
        if image1.bits().asstring(image1.sizeInBytes()) == image2.bits().asstring(image2.sizeInBytes()):
            return True
    
    return False


def read_desktop_ini(folder_path: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """读取 desktop.ini 文件，返回 (别名，图标路径，图标索引)"""
    ini_path = os.path.join(folder_path, 'desktop.ini')
    alias = None
    icon_path = None
    icon_index = 0
    
    if not os.path.exists(ini_path):
        return alias, icon_path, icon_index
    
    try:
        # 尝试多种编码，优先使用 GBK（Windows 中文系统默认）
        encodings = ['gbk', 'utf-8', 'utf-16', 'utf-16-le', 'utf-16-be']
        lines = []
        used_encoding = 'gbk'
        
        for encoding in encodings:
            try:
                with open(ini_path, 'r', encoding=encoding) as f:
                    lines = f.readlines()
                used_encoding = encoding
                break
            except:
                continue
        
        in_shell_class = False
        for line in lines:
            line = line.strip()
            if line == '[.ShellClassInfo]':
                in_shell_class = True
            elif line.startswith('[') and line.endswith(']'):
                in_shell_class = False
            elif in_shell_class:
                if line.startswith('LocalizedResourceName='):
                    alias = line[len('LocalizedResourceName='):]
                elif line.startswith('IconResource='):
                    icon_value = line[len('IconResource='):]
                    if ',' in icon_value:
                        icon_path, index_str = icon_value.rsplit(',', 1)
                        try:
                            icon_index = int(index_str)
                        except:
                            icon_index = 0
                    else:
                        icon_path = icon_value
    except Exception as e:
        logger.error(f"读取 desktop.ini 失败：{e}")
    
    return alias, icon_path, icon_index


def run_attrib_command(command: str) -> bool:
    """运行 attrib 命令，隐藏终端窗口"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10
        )
        return result.returncode == 0
    except:
        return False


def run_elevated_process(exe_path: str, params: str, timeout_ms: int = 60000) -> str:
    """以管理员权限运行程序 - 使用 ShellExecuteExW 触发 UAC 提权
    
    subprocess.run() 使用 CreateProcess，无法从非提权进程启动
    requireAdministrator 清单的 EXE（返回 ERROR_ELEVATION_REQUIRED 740）。
    必须使用 ShellExecuteExW + runas 动词才能正确触发 UAC。
    
    Returns:
        'ok' - 成功完成
        'cancelled' - 用户取消了 UAC 提示
        'timeout' - 等待超时
        'error_X' - 其他错误（X 为 Windows 错误码）
    """
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_HIDE = 0
    
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ('cbSize', wintypes.DWORD),
            ('fMask', ctypes.c_ulong),
            ('hwnd', wintypes.HWND),
            ('lpVerb', wintypes.LPCWSTR),
            ('lpFile', wintypes.LPCWSTR),
            ('lpParameters', wintypes.LPCWSTR),
            ('lpDirectory', wintypes.LPCWSTR),
            ('nShow', wintypes.INT),
            ('hInstApp', wintypes.HINSTANCE),
            ('lpIDList', ctypes.c_void_p),
            ('lpClass', wintypes.LPCWSTR),
            ('hkeyClass', wintypes.HKEY),
            ('dwHotKey', wintypes.DWORD),
            ('hIconOrMonitor', wintypes.HANDLE),
            ('hProcess', wintypes.HANDLE),
        ]
    
    execute_info = SHELLEXECUTEINFOW()
    execute_info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    execute_info.fMask = SEE_MASK_NOCLOSEPROCESS
    execute_info.hwnd = None
    execute_info.lpVerb = 'runas'
    execute_info.lpFile = exe_path
    execute_info.lpParameters = params
    execute_info.lpDirectory = None
    execute_info.nShow = SW_HIDE
    
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(execute_info)):
        error = ctypes.GetLastError()
        if error == 1223:
            return 'cancelled'
        return f'error_{error}'
    
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    
    wait_result = ctypes.windll.kernel32.WaitForSingleObject(
        execute_info.hProcess, timeout_ms
    )
    ctypes.windll.kernel32.CloseHandle(execute_info.hProcess)
    
    if wait_result == WAIT_TIMEOUT:
        return 'timeout'
    if wait_result == WAIT_OBJECT_0:
        return 'ok'
    return f'wait_error_{wait_result}'


def get_helper_path() -> Optional[Path]:
    """获取提权辅助工具路径 - 兼容 PyInstaller 打包和开发环境"""
    if getattr(sys, 'frozen', False):
        helper_path = Path(sys.executable).parent / 'DesktopIniHelper.exe'
    else:
        helper_path = Path(__file__).parent / 'dist' / 'DesktopIniHelper.exe'
    
    if helper_path.exists():
        return helper_path
    return None


def write_desktop_ini(folder_path: str, alias: str, icon_path: Optional[str], icon_index: int = 0) -> bool:
    """写入 desktop.ini 文件 - 不弹出终端窗口"""
    ini_path = os.path.join(folder_path, 'desktop.ini')
    temp_path = os.path.join(folder_path, 'desktop.tmp')
    
    try:
        run_attrib_command(f'attrib -r "{folder_path}"')
        
        if os.path.exists(ini_path):
            force_take_ownership(ini_path)
        
        force_take_ownership(folder_path)
        lines = []
        encodings = ['gbk', 'utf-8', 'utf-16']
        used_encoding = 'gbk'  # 默认使用 GBK 编码
        
        if os.path.exists(ini_path):
            for encoding in encodings:
                try:
                    with open(ini_path, 'r', encoding=encoding) as f:
                        lines = f.readlines()
                    used_encoding = encoding
                    break
                except:
                    continue
        
        # 解析并更新内容
        new_lines = []
        in_shell_class = False
        shell_class_found = False
        alias_updated = False
        icon_updated = False
        
        for line in lines:
            stripped = line.strip()
            
            if stripped == '[.ShellClassInfo]':
                in_shell_class = True
                shell_class_found = True
                new_lines.append(line)
            elif stripped.startswith('[') and stripped.endswith(']'):
                # 添加缺失的配置
                if in_shell_class:
                    if not alias_updated and alias:
                        new_lines.append(f'LocalizedResourceName={alias}\n')
                        alias_updated = True
                    if not icon_updated and icon_path:
                        new_lines.append(f'IconResource={icon_path},{icon_index}\n')
                        icon_updated = True
                in_shell_class = False
                new_lines.append(line)
            elif in_shell_class:
                if stripped.startswith('LocalizedResourceName='):
                    if alias:
                        new_lines.append(f'LocalizedResourceName={alias}\n')
                    alias_updated = True
                elif stripped.startswith('IconResource='):
                    if icon_path:
                        new_lines.append(f'IconResource={icon_path},{icon_index}\n')
                    icon_updated = True
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        
        # 如果没有找到 [.ShellClassInfo] 部分，添加它
        if not shell_class_found:
            new_lines.append('[.ShellClassInfo]\n')
        
        # 添加缺失的配置
        if not alias_updated and alias:
            # 找到 [.ShellClassInfo] 的位置并插入
            for i, line in enumerate(new_lines):
                if line.strip() == '[.ShellClassInfo]':
                    new_lines.insert(i + 1, f'LocalizedResourceName={alias}\n')
                    break
        
        if not icon_updated and icon_path:
            for i, line in enumerate(new_lines):
                if line.strip() == '[.ShellClassInfo]':
                    # 在 LocalizedResourceName 后插入，如果没有则在下一行
                    insert_pos = i + 1
                    for j in range(i + 1, len(new_lines)):
                        if new_lines[j].strip().startswith('['):
                            break
                        insert_pos = j + 1
                    new_lines.insert(insert_pos, f'IconResource={icon_path},{icon_index}\n')
                    break
        
        # 写入临时文件
        with open(temp_path, 'w', encoding=used_encoding) as f:
            f.writelines(new_lines)
        
        # 替换原文件
        if os.path.exists(ini_path):
            os.remove(ini_path)
        shutil.move(temp_path, ini_path)
        
        run_attrib_command(f'attrib +h +s "{ini_path}" && attrib +r "{folder_path}"')
        
        return True
        
    except PermissionError as e:
        logger.error(f"写入 desktop.ini 权限不足：{e}")
        raise
    except Exception as e:
        logger.error(f"写入 desktop.ini 失败：{e}")
        return False


@dataclass
class FolderItem:
    """文件夹项目数据类"""
    path: str
    name: str
    alias: str
    exe_files: List[str]
    icons: List[Tuple[str, int, QIcon]]  # (exe 路径，图标索引，图标)
    selected_icon_index: int  # 在 icons 列表中的索引
    current_icon_path: Optional[str]
    current_icon_index: int
    folder_icon_cache: Optional[QIcon] = None  # 文件夹图标缓存


class IconComboBox(QComboBox):
    """带图标的下拉框"""
    icon_selected = pyqtSignal(str, int)  # (icon_path, icon_index)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(24, 24))
        self.setMinimumHeight(32)
        self.currentIndexChanged.connect(self.on_current_index_changed)
    
    def on_current_index_changed(self, index: int):
        """处理当前索引变化"""
        if index < 0:
            return
        
        # 检查是否是特殊选项
        item_data = self.itemData(index, Qt.ItemDataRole.UserRole)
        if item_data:
            if item_data == 'system':
                # 打开系统图标选择对话框
                self.setCurrentIndex(0)  # 重置选择
                dialog = SystemIconDialog(self.window())
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    path, idx = dialog.get_selected_icon()
                    if path:
                        self.icon_selected.emit(path, idx)
            elif item_data == 'custom':
                # 打开文件选择对话框
                self.setCurrentIndex(0)  # 重置选择
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "选择图标文件",
                    "",
                    "可执行文件 (*.exe);;动态链接库 (*.dll);;所有文件 (*.*)"
                )
                if file_path:
                    # 尝试提取第一个图标
                    icon = extract_icon_from_exe(file_path, 0)
                    if icon and not icon.isNull():
                        self.icon_selected.emit(file_path, 0)


class DropArea(QLabel):
    """拖放区域"""
    folders_dropped = pyqtSignal(list)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(100)
        self.setStyleSheet("""
            DropArea {
                border: 3px dashed #888;
                border-radius: 10px;
                background-color: #f0f0f0;
                color: #666;
                font-size: 14px;
            }
            DropArea:hover {
                border-color: #2196F3;
                background-color: #e3f2fd;
            }
        """)
        self.setText("拖放文件夹到这里\n或点击选择文件夹")
        self.setAcceptDrops(True)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("""
                DropArea {
                    border: 3px dashed #2196F3;
                    border-radius: 10px;
                    background-color: #e3f2fd;
                    color: #666;
                    font-size: 14px;
                }
            """)
    
    def dragLeaveEvent(self, event):
        self.setStyleSheet("""
            DropArea {
                border: 3px dashed #888;
                border-radius: 10px;
                background-color: #f0f0f0;
                color: #666;
                font-size: 14px;
            }
            DropArea:hover {
                border-color: #2196F3;
                background-color: #e3f2fd;
            }
        """)
    
    def dropEvent(self, event: QDropEvent):
        self.dragLeaveEvent(event)
        urls = event.mimeData().urls()
        folders = []
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                folders.append(path)
        if folders:
            self.folders_dropped.emit(folders)
    
    def mousePressEvent(self, event):
        # 使用 QFileDialog 选择文件夹 - 支持多选
        fd = QFileDialog()
        fd.setFileMode(QFileDialog.FileMode.Directory)
        fd.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        
        # 查找并设置 QListView 和 QTreeView 为多选模式
        list_view = fd.findChild(QListView, 'listView')
        if list_view:
            list_view.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        
        tree_view = fd.findChild(QTreeView)
        if tree_view:
            tree_view.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        
        if fd.exec():
            folders = fd.selectedFiles()
            if folders:
                self.folders_dropped.emit(folders)


class FolderAliasManager(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        self.folder_items: List[FolderItem] = []
        self.init_ui()
        
        # 启用主窗口的拖放功能
        self.setAcceptDrops(True)
    
    def dragEnterEvent(self, event):
        """处理拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()
    
    def dropEvent(self, event):
        """处理拖拽释放事件"""
        urls = event.mimeData().urls()
        folders = []
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                folders.append(path)
        if folders:
            self.on_folders_dropped(folders)
    
    def init_ui(self):
        self.setWindowTitle("文件夹别名管理器")
        self.setMinimumSize(900, 600)
        
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 设置主窗口背景色为浅灰色
        central_widget.setStyleSheet("""
            QWidget {
                background-color: #f5f5f5;
                color: #333;
            }
        """)
        
        # 拖放区域
        self.drop_area = DropArea()
        self.drop_area.folders_dropped.connect(self.on_folders_dropped)
        layout.addWidget(self.drop_area)
        
        # 文件夹列表
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["文件夹名称", "别名", "图标", "路径"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(2, 200)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        # 设置行高为 28px，与下拉框匹配
        self.table.verticalHeader().setDefaultSectionSize(28)
        # 设置编辑框样式
        self.table.setStyleSheet(self.table.styleSheet() + """
            QLineEdit {
                background-color: white;
                color: #333;
                border: 2px solid #2196F3;
                padding: 2px;
            }
        """)
        self.table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #ddd;
                border-radius: 5px;
                gridline-color: #ddd;
                background-color: white;
            }
            QTableWidget::item {
                padding: 2px;
                background-color: white;
                color: #333;
            }
            QTableWidget::item:selected {
                background-color: #2196F3;
                color: white;
            }
            QHeaderView::section {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4a90d9, stop:1 #357abd);
                padding: 8px;
                border: 1px solid #ddd;
                font-weight: bold;
                color: white;
            }
            QTableCornerButton::section {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4a90d9, stop:1 #357abd);
                border: 1px solid #ddd;
            }
        """)
        layout.addWidget(self.table)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        # 左下角复选框
        self.chk_auto_read_icons = QCheckBox("导入文件夹时自动读取图标")
        self.chk_auto_read_icons.setChecked(True)
        self.chk_auto_read_icons.setStyleSheet("""
            QCheckBox {
                font-size: 13px;
                color: #333;
            }
        """)
        button_layout.addWidget(self.chk_auto_read_icons)
        
        button_layout.addStretch()
        
        self.btn_clear = QPushButton("清空列表")
        self.btn_clear.clicked.connect(self.clear_list)
        self.btn_clear.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
        """)
        
        self.btn_refresh = QPushButton("刷新图标")
        self.btn_refresh.clicked.connect(self.refresh_icons)
        self.btn_refresh.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """)
        
        button_layout.addWidget(self.btn_clear)
        button_layout.addWidget(self.btn_refresh)
        
        self.btn_save = QPushButton("保存更改")
        self.btn_save.clicked.connect(self.save_changes)
        self.btn_save.setStyleSheet("""
            QPushButton {
                padding: 10px 30px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        button_layout.addWidget(self.btn_save)
        
        layout.addLayout(button_layout)
    
    def on_folders_dropped(self, folders: List[str]):
        """处理拖放的文件夹"""
        if not folders:
            return
        
        # 显示加载对话框
        progress = QProgressDialog("正在处理文件夹...", "取消", 0, len(folders), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setWindowTitle("加载中")
        progress.setMinimumDuration(0)
        progress.show()
        
        try:
            for i, folder_path in enumerate(folders):
                # 检查是否已存在
                if any(item.path == folder_path for item in self.folder_items):
                    progress.setValue(i + 1)
                    continue
                
                self.add_folder(folder_path)
                progress.setValue(i + 1)
                
                # 检查是否取消
                if progress.wasCanceled():
                    break
        finally:
            progress.close()
        
        self.update_table()
    
    def add_folder(self, folder_path: str):
        """添加文件夹到列表"""
        folder_name = os.path.basename(folder_path)
        
        # 读取现有的 desktop.ini
        alias, icon_path, icon_index = read_desktop_ini(folder_path)
        if not alias:
            alias = folder_name
        
        # 查找 exe 文件
        exe_files = find_exe_files(folder_path)
        
        # 提取所有图标（带去重）
        icons: List[Tuple[str, int, QIcon]] = []
        
        # 添加默认文件夹图标
        default_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        icons.append(("", -1, default_icon))
        
        # 如果勾选了自动读取图标，提取 exe 图标（去重）
        MAX_ICONS_PER_FOLDER = 50  # 每个文件夹最多显示 50 个图标
        
        if self.chk_auto_read_icons.isChecked():
            for exe_path in exe_files:
                # 检查是否已达到最大图标数量
                if len(icons) >= MAX_ICONS_PER_FOLDER:
                    logger.info(f"已达到最大图标数量限制 ({MAX_ICONS_PER_FOLDER})，停止加载 {exe_path} 的图标")
                    break
                
                exe_icons = get_exe_icons(exe_path, max_icons=3)  # 每个 exe 最多提取 3 个图标
                for idx, icon in exe_icons:
                    # 检查是否已达到最大图标数量
                    if len(icons) >= MAX_ICONS_PER_FOLDER:
                        break
                    
                    # 检查是否与已有图标重复
                    is_duplicate = False
                    for existing_path, existing_idx, existing_icon in icons:
                        if icons_are_same(icon, existing_icon):
                            is_duplicate = True
                            logger.debug(f"跳过重复图标：{exe_path}[{idx}]")
                            break
                    
                    if not is_duplicate:
                        icons.append((exe_path, idx, icon))
        
        # 确定当前选中的图标
        selected_icon_index = 0
        if icon_path:
            for i, (path, idx, _) in enumerate(icons):
                if path == icon_path and idx == icon_index:
                    selected_icon_index = i
                    break
        
        item = FolderItem(
            path=folder_path,
            name=folder_name,
            alias=alias or folder_name,
            exe_files=exe_files,
            icons=icons,
            selected_icon_index=selected_icon_index,
            current_icon_path=icon_path,
            current_icon_index=icon_index,
            folder_icon_cache=default_icon  # 缓存文件夹图标
        )
        
        self.folder_items.append(item)
    
    def update_table(self):
        """更新表格显示"""
        self.table.setRowCount(len(self.folder_items))
        
        for row, item in enumerate(self.folder_items):
            # 文件夹名称
            name_item = QTableWidgetItem(item.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, name_item)
            
            # 别名（可编辑）
            alias_item = QTableWidgetItem(item.alias)
            self.table.setItem(row, 1, alias_item)
            
            # 图标下拉框
            icon_combo = IconComboBox()
            icon_combo.setStyleSheet("""
                QComboBox {
                    border: 1px solid #ccc;
                    border-radius: 3px;
                    padding: 2px;
                    background-color: white;
                    color: #333;
                    min-height: 24px;
                    max-height: 24px;
                }
                QComboBox::drop-down {
                    border: none;
                    width: 20px;
                }
                QComboBox::down-arrow {
                    image: none;
                    border: none;
                }
                QComboBox:hover {
                    border: 1px solid #999;
                }
                QComboBox QAbstractItemView {
                    background-color: white;
                    color: #333;
                    border: 1px solid #ccc;
                    selection-background-color: #2196F3;
                    selection-color: white;
                    min-height: 30px;
                }
                QComboBox QAbstractItemView::item {
                    height: 30px;
                    background-color: white;
                    padding: 2px;
                }
                QComboBox QAbstractItemView::item:selected {
                    background-color: #2196F3;
                    color: white;
                }
                QComboBox QAbstractItemView::item:hover {
                    background-color: #e3f2fd;
                }
            """)
            icon_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            icon_combo.setMinimumHeight(24)
            icon_combo.setMaximumHeight(24)
            for i, (path, idx, icon) in enumerate(item.icons):
                if path:
                    exe_name = os.path.basename(path)
                    icon_combo.addItem(icon, f"{exe_name} [{idx}]")
                else:
                    icon_combo.addItem(icon, "默认文件夹图标")
            
            # 添加分隔符
            icon_combo.insertSeparator(icon_combo.count())
            
            # 添加特殊选项
            system_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirHomeIcon)
            icon_combo.addItem(system_icon, "🌐 其他 - 系统图标")
            icon_combo.setItemData(icon_combo.count() - 1, 'system', Qt.ItemDataRole.UserRole)
            
            custom_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
            icon_combo.addItem(custom_icon, "📁 其他 - 自定义图标")
            icon_combo.setItemData(icon_combo.count() - 1, 'custom', Qt.ItemDataRole.UserRole)
            
            icon_combo.setCurrentIndex(item.selected_icon_index)
            icon_combo.currentIndexChanged.connect(
                lambda idx, r=row: self.on_icon_changed(r, idx)
            )
            icon_combo.icon_selected.connect(
                lambda path, idx, r=row: self.on_custom_icon_selected(r, path, idx)
            )
            self.table.setCellWidget(row, 2, icon_combo)
            
            # 路径
            path_item = QTableWidgetItem(item.path)
            path_item.setFlags(path_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            path_item.setToolTip(item.path)
            self.table.setItem(row, 3, path_item)
    
    def on_icon_changed(self, row: int, index: int):
        """图标选择改变"""
        if 0 <= row < len(self.folder_items):
            self.folder_items[row].selected_icon_index = index
    
    def on_custom_icon_selected(self, row: int, icon_path: str, icon_index: int):
        """处理自定义图标选择（带去重）"""
        if 0 <= row < len(self.folder_items):
            item = self.folder_items[row]
            # 提取图标
            icon = extract_icon_from_exe(icon_path, icon_index)
            if icon and not icon.isNull():
                # 检查是否与已有图标重复
                is_duplicate = False
                for existing_path, existing_idx, existing_icon in item.icons:
                    if icons_are_same(icon, existing_icon):
                        is_duplicate = True
                        logger.debug(f"跳过重复图标：{icon_path}[{icon_index}]")
                        # 如果重复，直接选中已有的那个
                        for i, (p, idx, _) in enumerate(item.icons):
                            if p == existing_path and idx == existing_idx:
                                item.selected_icon_index = i
                                break
                        break
                
                if not is_duplicate:
                    # 添加到图标列表
                    item.icons.append((icon_path, icon_index, icon))
                    item.selected_icon_index = len(item.icons) - 1
                
                # 更新表格
                self.update_table()
    
    def show_context_menu(self, pos):
        """显示右键菜单"""
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        
        menu = QMenu(self)
        
        open_action = menu.addAction("📂 打开文件夹位置")
        open_action.triggered.connect(lambda: self.open_folder_location(row))
        
        menu.exec(self.table.viewport().mapToGlobal(pos))
    
    def open_folder_location(self, row: int):
        """打开文件夹位置"""
        if 0 <= row < len(self.folder_items):
            folder_path = self.folder_items[row].path
            import subprocess
            subprocess.Popen(f'explorer.exe "{folder_path}"', shell=True)
    
    def clear_list(self):
        """清空列表"""
        self.folder_items.clear()
        self.update_table()
    
    def refresh_icons(self):
        """刷新所有图标（带去重）"""
        for item in self.folder_items:
            # 保存当前选择的图标
            current_selected = None
            if 0 <= item.selected_icon_index < len(item.icons):
                current_selected = item.icons[item.selected_icon_index]
            
            # 重新提取图标（带去重）
            icons: List[Tuple[str, int, QIcon]] = []
            default_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
            icons.append(("", -1, default_icon))
            
            MAX_ICONS_PER_FOLDER = 50  # 每个文件夹最多显示 50 个图标
            
            for exe_path in item.exe_files:
                # 检查是否已达到最大图标数量
                if len(icons) >= MAX_ICONS_PER_FOLDER:
                    logger.info(f"已达到最大图标数量限制 ({MAX_ICONS_PER_FOLDER})，停止加载 {exe_path} 的图标")
                    break
                
                exe_icons = get_exe_icons(exe_path, max_icons=3)
                for idx, icon in exe_icons:
                    # 检查是否已达到最大图标数量
                    if len(icons) >= MAX_ICONS_PER_FOLDER:
                        break
                    
                    # 检查是否与已有图标重复
                    is_duplicate = False
                    for existing_path, existing_idx, existing_icon in icons:
                        if icons_are_same(icon, existing_icon):
                            is_duplicate = True
                            logger.debug(f"跳过重复图标：{exe_path}[{idx}]")
                            break
                    
                    if not is_duplicate:
                        icons.append((exe_path, idx, icon))
            
            # 如果有自定义图标，保留（也要去重）
            for icon_data in item.icons:
                path, idx, icon_obj = icon_data
                if path and path not in [exe for exe in item.exe_files]:
                    # 这是自定义选择的图标，保留
                    icon = extract_icon_from_exe(path, idx)
                    if icon and not icon.isNull():
                        # 检查是否重复
                        is_duplicate = False
                        for existing_path, existing_idx, existing_icon in icons:
                            if icons_are_same(icon, existing_icon):
                                is_duplicate = True
                                break
                        
                        if not is_duplicate and len(icons) < MAX_ICONS_PER_FOLDER:
                            icons.append(icon_data)
            
            item.icons = icons
            
            # 尝试恢复选择
            if current_selected:
                for i, icon_data in enumerate(icons):
                    if icon_data[0] == current_selected[0] and icon_data[1] == current_selected[1]:
                        item.selected_icon_index = i
                        break
                else:
                    item.selected_icon_index = 0
            else:
                item.selected_icon_index = 0
        
        self.update_table()
    
    def save_changes(self):
        """保存更改"""
        success_count = 0
        failed_items = []
        permission_denied_items = []
        
        for row, item in enumerate(self.folder_items):
            # 获取别名
            alias_item = self.table.item(row, 1)
            alias = alias_item.text() if alias_item else item.name
            
            # 获取选中的图标
            icon_combo = self.table.cellWidget(row, 2)
            selected_index = icon_combo.currentIndex() if icon_combo else 0
            
            icon_path = None
            icon_index = 0
            if 0 <= selected_index < len(item.icons):
                path, idx, _ = item.icons[selected_index]
                if path:  # 不是默认图标
                    icon_path = path
                    icon_index = idx
            
            try:
                if write_desktop_ini(item.path, alias, icon_path, icon_index):
                    success_count += 1
                else:
                    failed_items.append(item.name)
            except PermissionError:
                permission_denied_items.append((item.name, item.path, alias, icon_path, icon_index))
        
        # 刷新资源管理器
        refresh_shell()
        
        # 如果有权限拒绝的项目，尝试使用提权工具
        if permission_denied_items:
            self.try_elevated_write(permission_denied_items, success_count, failed_items)
        elif failed_items:
            QMessageBox.warning(
                self,
                "部分失败",
                f"成功：{success_count} 个文件夹\n失败：{len(failed_items)} 个\n\n"
                f"失败的文件夹：{', '.join(failed_items)}"
            )
        else:
            QMessageBox.information(
                self,
                "保存成功",
                f"成功更新了 {success_count} 个文件夹的别名和图标！\n"
                "更改可能需要刷新资源管理器才能完全生效。"
            )
    
    def try_elevated_write(self, permission_denied_items, success_count, failed_items):
        """使用提权工具批量写入需要管理员权限的文件夹
        
        核心改进：
        1. 使用 ShellExecuteExW + runas 替代 subprocess.run() 触发 UAC
        2. 批量处理所有权限拒绝项，只弹一次 UAC 提示
        3. 通过 JSON 临时文件通信，避免管道跨权限边界问题
        """
        helper_path = get_helper_path()
        
        if not helper_path:
            msg = f"成功：{success_count} 个文件夹\n"
            if failed_items:
                msg += f"失败：{len(failed_items)} 个：{', '.join(failed_items)}\n"
            msg += f"\n以下文件夹需要管理员权限才能修改：\n"
            msg += ', '.join([name for name, _, _, _, _ in permission_denied_items])
            msg += "\n\n未找到提权辅助工具 DesktopIniHelper.exe。\n"
            msg += "请确保该文件与主程序在同一目录下。"
            QMessageBox.warning(self, "需要管理员权限", msg)
            return
        
        input_file = None
        output_file = None
        
        try:
            items_data = []
            for name, path, alias, icon_path, icon_index in permission_denied_items:
                items_data.append({
                    'folder_path': path,
                    'alias': alias,
                    'icon_path': icon_path or '',
                    'icon_index': icon_index,
                    'name': name
                })
            
            input_file = os.path.join(tempfile.gettempdir(), f'fam_input_{os.getpid()}.json')
            output_file = os.path.join(tempfile.gettempdir(), f'fam_output_{os.getpid()}.json')
            
            if os.path.exists(output_file):
                os.remove(output_file)
            
            with open(input_file, 'w', encoding='utf-8') as f:
                json.dump({'items': items_data, 'output_file': output_file}, f, ensure_ascii=False)
            
            params = f'--batch "{input_file}"'
            result = run_elevated_process(str(helper_path), params, timeout_ms=120000)
            
            elevated_success = 0
            elevated_failed = []
            
            if result == 'cancelled':
                elevated_failed = [name for name, _, _, _, _ in permission_denied_items]
                logger.warning("用户取消了 UAC 提权提示")
            elif result == 'timeout':
                elevated_failed = [name for name, _, _, _, _ in permission_denied_items]
                logger.error("提权操作超时")
            elif result == 'ok':
                if os.path.exists(output_file):
                    try:
                        with open(output_file, 'r', encoding='utf-8') as f:
                            results = json.load(f)
                        for item_result in results.get('results', []):
                            if item_result.get('success'):
                                elevated_success += 1
                            else:
                                elevated_failed.append(item_result.get('name', '未知'))
                    except Exception as e:
                        elevated_failed = [name for name, _, _, _, _ in permission_denied_items]
                        logger.error(f"读取提权结果失败：{e}")
                else:
                    elevated_failed = [name for name, _, _, _, _ in permission_denied_items]
                    logger.error("提权工具未生成结果文件")
            else:
                elevated_failed = [name for name, _, _, _, _ in permission_denied_items]
                logger.error(f"提权启动失败：{result}")
        except Exception as e:
            elevated_success = 0
            elevated_failed = [name for name, _, _, _, _ in permission_denied_items]
            logger.error(f"提权操作异常：{e}")
        finally:
            for f in [input_file, output_file]:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except:
                        pass
        
        refresh_shell()
        
        total_success = success_count + elevated_success
        if elevated_failed or failed_items:
            msg = f"成功：{total_success} 个文件夹\n"
            if failed_items:
                msg += f"写入失败：{len(failed_items)} 个：{', '.join(failed_items)}\n"
            if elevated_failed:
                msg += f"提权失败：{len(elevated_failed)} 个：{', '.join(elevated_failed)}\n"
            msg += "\n对于提权失败的文件夹，请右键程序选择'以管理员身份运行'。"
            QMessageBox.warning(self, "部分失败", msg)
        else:
            QMessageBox.information(
                self,
                "保存成功",
                f"成功更新了 {total_success} 个文件夹的别名和图标！\n"
                "更改可能需要刷新资源管理器才能完全生效。"
            )


class SystemIconDialog(QDialog):
    """系统图标选择对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_icon_path = None
        self.selected_icon_index = 0
        self.init_ui()
    
    def init_ui(self):
        self.setWindowTitle("选择系统图标")
        self.setMinimumSize(600, 500)
        self.setModal(True)
        
        layout = QVBoxLayout(self)
        
        # 系统图标列表 - 使用网格布局
        self.icon_list = QListWidget()
        self.icon_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.icon_list.setIconSize(QSize(32, 32))
        self.icon_list.setGridSize(QSize(80, 80))
        self.icon_list.setSpacing(10)
        self.icon_list.setUniformItemSizes(True)
        self.icon_list.setMovement(QListWidget.Movement.Static)
        self.icon_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.icon_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ccc;
                background-color: white;
                color: #333;
            }
            QListWidget::item {
                background-color: white;
                color: #333;
                border-radius: 5px;
                padding: 5px;
            }
            QListWidget::item:selected {
                background-color: #2196F3;
                color: white;
            }
            QListWidget::item:hover {
                background-color: #e3f2fd;
            }
        """)
        
        # 加载系统图标
        self.load_system_icons()
        
        layout.addWidget(self.icon_list)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_ok = QPushButton("确定")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_ok.setStyleSheet("""
            QPushButton {
                padding: 10px 30px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                padding: 10px 30px;
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
        """)
        
        btn_layout.addWidget(self.btn_ok)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)
    
    def load_system_icons(self):
        """加载 Windows 系统图标"""
        # 常见的系统图标文件
        system_files = [
            ("shell32.dll", "系统图标"),
            ("imageres.dll", "Windows 图标"),
            ("ddores.dll", "设备图标"),
        ]
        
        for dll_name, description in system_files:
            dll_path = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'System32', dll_name)
            if os.path.exists(dll_path):
                # 添加分组标题
                header_item = QListWidgetItem(f"📁 {description} - {dll_name}")
                header_item.setFlags(header_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                header_item.setBackground(QColor('#f0f0f0'))
                header_item.setForeground(QColor('#333'))
                self.icon_list.addItem(header_item)
                
                # 提取并添加图标（前 50 个）
                for i in range(50):
                    icon = extract_icon_from_exe(dll_path, i)
                    if icon and not icon.isNull():
                        item = QListWidgetItem()
                        item.setIcon(icon)
                        item.setData(Qt.ItemDataRole.UserRole, (dll_path, i))
                        item.setToolTip(f"{dll_name} [{i}]")
                        self.icon_list.addItem(item)
    
    def get_selected_icon(self) -> Tuple[Optional[str], int]:
        """获取选中的图标"""
        return self.selected_icon_path, self.selected_icon_index
    
    def accept(self):
        selected_items = self.icon_list.selectedItems()
        if selected_items:
            item = selected_items[0]
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                self.selected_icon_path, self.selected_icon_index = data
        super().accept()


def main():
    try:
        logger.info("=" * 50)
        logger.info("文件夹别名管理器启动")
        logger.info("=" * 50)
        
        logger.info("创建 QApplication...")
        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        
        # 设置应用程序样式
        app.setStyleSheet("""
            QMainWindow {
                background-color: #fafafa;
            }
            QWidget {
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            }
        """)
        
        logger.info("创建主窗口...")
        window = FolderAliasManager()
        logger.info("显示窗口...")
        window.show()
        logger.info("进入主循环...")
        sys.exit(app.exec())
        
    except Exception as e:
        logger.error(f"程序发生严重错误：{e}")
        logger.error(traceback.format_exc())
        # 显示错误对话框
        try:
            error_app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(
                None,
                "程序错误",
                f"程序发生错误：{e}\n\n"
                f"详细信息：\n{traceback.format_exc()}\n\n"
                f"请查看 folder_alias_manager.log 文件获取完整日志。"
            )
        except:
            pass
        sys.exit(1)


if __name__ == '__main__':
    main()
