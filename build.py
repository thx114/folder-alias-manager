"""
打包脚本 - 将文件夹别名管理器打包为可执行文件
"""
import os
import sys
import subprocess
import shutil


def clean_build():
    """清理之前的构建文件"""
    dirs_to_remove = ['build', 'dist']
    for dir_name in dirs_to_remove:
        if os.path.exists(dir_name):
            print(f"删除 {dir_name} 目录...")
            shutil.rmtree(dir_name, ignore_errors=True)
    
    files_to_remove = [f for f in os.listdir('.') if f.endswith('.spec')]
    for file in files_to_remove:
        if os.path.exists(file):
            print(f"删除 {file}...")
            os.remove(file)


def build_helper():
    """构建提权辅助工具"""
    print("打包提权辅助工具...")
    
    args = [
        'pyinstaller',
        '--name=DesktopIniHelper',
        '--onefile',
        '--clean',
        '--noconfirm',
        '--uac-admin',
        '--windowed',
        'desktop_ini_helper.py'
    ]
    
    try:
        result = subprocess.run(args, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode != 0:
            print(f"提权工具打包失败: {result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"提权工具打包失败: {e}")
        return False


def build_exe():
    """使用 PyInstaller 打包主程序"""
    print("开始打包主程序...")
    
    args = [
        'pyinstaller',
        '--name=FolderAliasManager',
        '--windowed',
        '--onefile',
        '--clean',
        '--noconfirm',
        '--noconsole',
        'folder_alias_manager.py'
    ]
    
    try:
        result = subprocess.run(args, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode != 0:
            print(f"错误: {result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"打包失败: {e}")
        return False


def main():
    print("=" * 50)
    print("文件夹别名管理器 - 打包工具")
    print("=" * 50)
    
    # 检查 PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("正在安装 PyInstaller...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])
    
    # 清理
    clean_build()
    
    # 先打包提权辅助工具
    if not build_helper():
        print("\n警告：提权工具打包失败，主程序仍将继续打包")
    
    # 再打包主程序
    if build_exe():
        print("\n" + "=" * 50)
        print("打包成功！")
        print(f"主程序: {os.path.abspath('dist/FolderAliasManager.exe')}")
        if os.path.exists('dist/DesktopIniHelper.exe'):
            print(f"提权工具: {os.path.abspath('dist/DesktopIniHelper.exe')}")
        print("=" * 50)
    else:
        print("\n打包失败！")
        sys.exit(1)


if __name__ == '__main__':
    main()
