# -*- mode: python ; coding: utf-8 -*-
"""
泠靈小工具的 PyInstaller 打包定義

repo 裡唯一一份打包參數：CD、build.bat、build.sh 都只跑這份，產出資料夾版
dist/LingLingSuite/。資源檔收進 PyInstaller 的資源目錄（sys._MEIPASS）。

repo 根目錄有 client_secret.json 時（CD 由 GOOGLE_CLIENT_SECRET 寫入），收進資源目錄的根層；
沒有時照常建置，只是這個版本不能登入 Google。版號檔 version.txt 同樣由 CD 寫入、收進根層，
沒有時「關於」顯示開發版。

使用範例：
    pyinstaller --noconfirm LingLingSuite.spec
"""
import os
import sys

ROOT = SPECPATH
SRC = os.path.join(ROOT, "src")

datas = [(os.path.join(SRC, "assets"), "assets")]

client_secret = os.path.join(ROOT, "client_secret.json")
if os.path.isfile(client_secret):
    datas.append((client_secret, "."))
else:
    print(
        "LingLingSuite.spec: repo 根目錄沒有 client_secret.json，這個版本不能登入 Google",
        file=sys.stderr,
    )

# 版號檔：CD 把 git tag 寫進 repo 根目錄的 version.txt，收進資源目錄的根層（與 core.constants.VERSION_FILE 一致）；
# 沒有時「關於」顯示開發版
version_file = os.path.join(ROOT, "version.txt")
if os.path.isfile(version_file):
    datas.append((version_file, "."))
else:
    print("LingLingSuite.spec: repo 根目錄沒有 version.txt，「關於」會顯示開發版", file=sys.stderr)

a = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    datas=datas,
    hiddenimports=["PySide6.QtSvg", "PIL", "PIL.Image"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LingLingSuite",
    console=False,
    # 程式圖示（#92）到位前不套任何圖示
    icon="NONE",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="LingLingSuite",
)
