# shellcheck shell=bash
# run.sh 與 build.sh 共用：選出要用的 Python，並確認 requirements.txt 的套件都已安裝。
# 由呼叫端 source，呼叫前先 cd 到 repo 根目錄。
# 選法：有 .venv 就用 .venv/bin/python，否則用 python3。

_readme_hint() {
    echo "請照 README.zh-TW.md 的「在 Linux 上從原始碼執行」建立 .venv。" >&2
    echo "See \"Run from source on Linux\" in README.md to create .venv." >&2
}

if [[ -x .venv/bin/python ]]; then
    PYTHON=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
else
    echo "找不到 python3。" >&2
    _readme_hint
    exit 1
fi

# 確認 requirements.txt 列的每個套件都裝在 $PYTHON 裡（只看有沒有裝，不比對版本）
require_packages() {
    if ! "$PYTHON" - requirements.txt <<'PY'
import re
import sys
from importlib import metadata

missing = []
with open(sys.argv[1], encoding="utf-8") as requirements:
    for line in requirements:
        name = re.split(r"[\s<>=!~;\[#]", line.strip(), maxsplit=1)[0]
        if not name:
            continue
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
if missing:
    print(f"缺少套件：{', '.join(missing)}（{sys.executable}）", file=sys.stderr)
    sys.exit(1)
PY
    then
        _readme_hint
        exit 1
    fi
}
