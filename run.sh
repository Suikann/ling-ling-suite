#!/usr/bin/env bash
# 從原始碼啟動泠靈小工具：有 .venv 就用它，否則用 python3；缺套件時報錯
set -euo pipefail
cd "$(dirname "$0")"
source scripts/python_env.sh
require_packages
exec "$PYTHON" src/main.py "$@"
