#!/usr/bin/env bash
# 在 Linux 上建置資料夾版 dist/LingLingSuite/：裝 PyInstaller、跑測試、跑 LingLingSuite.spec
# 選 Python 的方式與 run.sh 相同（有 .venv 就用它，否則用 python3）
set -euo pipefail
cd "$(dirname "$0")"
source scripts/python_env.sh
require_packages

echo "安裝建置工具..."
if "$PYTHON" -m pip --version >/dev/null 2>&1; then
    "$PYTHON" -m pip install pyinstaller pytest
elif command -v uv >/dev/null 2>&1; then
    uv pip install --python "$PYTHON" pyinstaller pytest
else
    echo "$PYTHON 沒有 pip，裝不了 PyInstaller。" >&2
    exit 1
fi

echo "執行測試..."
"$PYTHON" -m pytest tests/ -q --tb=short

echo "建置資料夾版..."
"$PYTHON" -m PyInstaller --noconfirm LingLingSuite.spec

echo "建置完成：dist/LingLingSuite/"
