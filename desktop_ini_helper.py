"""
Desktop.ini 辅助工具 - 以管理员权限修改 desktop.ini
通过 ShellExecuteExW + runas 由主程序调用，自动触发 UAC 提权

支持两种模式：
1. 批量模式：--batch <input_json_path>
   主程序将所有待处理项写入 JSON 文件，本工具批量处理后写结果到 JSON
2. 单项模式：<folder_path> <alias> [icon_path] [icon_index]
   兼容旧版命令行调用方式
"""
import os
import sys
import json
import ctypes
import shutil
import logging
import subprocess
from pathlib import Path

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('folder_alias_manager.log', encoding='utf-8'),
    ]
)
logger = logging.getLogger(__name__)


def force_take_ownership(file_path: str):
    """强制获取文件/文件夹权限（不递归）"""
    if os.path.exists(file_path):
        subprocess.run(
            f'icacls "{file_path}" /grant *S-1-1-0:F',
            shell=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        try:
            os.chmod(file_path, 0o666)
        except:
            pass
        subprocess.run(
            f'attrib -r -h -s "{file_path}"',
            shell=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )


def write_desktop_ini(folder_path: str, alias: str, icon_path: str = None, icon_index: int = 0) -> bool:
    """写入 desktop.ini 文件"""
    ini_path = os.path.join(folder_path, 'desktop.ini')
    temp_path = os.path.join(folder_path, 'desktop.tmp')
    
    try:
        attrib_cmds = []
        attrib_cmds.append(f'attrib -r "{folder_path}"')
        
        if os.path.exists(ini_path):
            attrib_cmds.append(f'attrib -r -h -s "{ini_path}"')
            subprocess.run(
                f'icacls "{ini_path}" /grant *S-1-1-0:F',
                shell=True, capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        
        subprocess.run(
            f'icacls "{folder_path}" /grant *S-1-1-0:F',
            shell=True, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        subprocess.run(
            ' && '.join(attrib_cmds),
            shell=True, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        lines = []
        encodings = ['gbk', 'utf-8', 'utf-16']
        used_encoding = 'gbk'
        
        if os.path.exists(ini_path):
            for encoding in encodings:
                try:
                    with open(ini_path, 'r', encoding=encoding) as f:
                        lines = f.readlines()
                    used_encoding = encoding
                    break
                except:
                    continue
        
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
        
        if not shell_class_found:
            new_lines.append('[.ShellClassInfo]\n')
        
        if not alias_updated and alias:
            for i, line in enumerate(new_lines):
                if line.strip() == '[.ShellClassInfo]':
                    new_lines.insert(i + 1, f'LocalizedResourceName={alias}\n')
                    break
        
        if not icon_updated and icon_path:
            for i, line in enumerate(new_lines):
                if line.strip() == '[.ShellClassInfo]':
                    insert_pos = i + 1
                    for j in range(i + 1, len(new_lines)):
                        if new_lines[j].strip().startswith('['):
                            break
                        insert_pos = j + 1
                    new_lines.insert(insert_pos, f'IconResource={icon_path},{icon_index}\n')
                    break
        
        with open(temp_path, 'w', encoding=used_encoding) as f:
            f.writelines(new_lines)
        
        if os.path.exists(ini_path):
            os.remove(ini_path)
        shutil.move(temp_path, ini_path)
        
        subprocess.run(
            f'attrib +h +s "{ini_path}" && attrib +r "{folder_path}"',
            shell=True, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        logger.info(f"成功写入 desktop.ini: {folder_path}")
        return True
        
    except Exception as e:
        logger.error(f"写入 desktop.ini 失败：{e}")
        return False


def refresh_shell():
    """刷新资源管理器"""
    SHCNE_ASSOCCHANGED = 0x08000000
    SHCNF_IDLIST = 0x0000
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)


def process_batch(input_file: str):
    """批量模式：从 JSON 文件读取任务列表，处理后将结果写回"""
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    items = data.get('items', [])
    output_file = data.get('output_file', '')
    results = []
    
    for item in items:
        folder_path = item.get('folder_path', '')
        alias = item.get('alias', '')
        icon_path = item.get('icon_path', '') or None
        icon_index = item.get('icon_index', 0)
        name = item.get('name', '')
        
        logger.info(f"提权写入 desktop.ini: {folder_path}")
        success = write_desktop_ini(folder_path, alias, icon_path, icon_index)
        results.append({
            'folder_path': folder_path,
            'name': name,
            'success': success
        })
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({'results': results}, f, ensure_ascii=False)


def main():
    """主函数"""
    if len(sys.argv) >= 3 and sys.argv[1] == '--batch':
        input_file = sys.argv[2]
        if not os.path.exists(input_file):
            logger.error(f"批量输入文件不存在：{input_file}")
            sys.exit(1)
        process_batch(input_file)
        refresh_shell()
        sys.exit(0)
    
    if len(sys.argv) >= 3:
        folder_path = sys.argv[1]
        alias = sys.argv[2]
        icon_path = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
        icon_index = int(sys.argv[4]) if len(sys.argv) > 4 else 0
        
        success = write_desktop_ini(folder_path, alias, icon_path, icon_index)
        if success:
            refresh_shell()
            print("SUCCESS")
            sys.exit(0)
        else:
            print("FAILED")
            sys.exit(1)
    
    print("用法:")
    print("  批量模式: desktop_ini_helper.exe --batch <input_json_path>")
    print("  单项模式: desktop_ini_helper.exe <folder_path> <alias> [icon_path] [icon_index]")
    sys.exit(1)


if __name__ == '__main__':
    main()
