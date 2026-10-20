#!/usr/bin/env bash
# 把 PyInstaller 建好的資料夾版 dist/LingLingSuite/ 包成 dist/LingLingSuite-x86_64.AppImage 與它的 .zsync。
# CD 的 Linux 工作跑完 LingLingSuite.spec 後呼叫本檔；本機要試時先跑 ./build.sh，再跑 scripts/build_appimage.sh。
#
# - appimagetool 固定 1.9.1、runtime 固定 type2-runtime 20251108，下載後都核對 SHA-256，對不上就停。
# - runtime 以本機檔交給 appimagetool（APPIMAGETOOL_RUNTIME_FILE），不讓它自己去抓會變動的 continuous 版。
# - appimagetool 以 extract-and-run 執行，建置的主機不需要 FUSE。
# - 內嵌 GitHub 發行版的更新資訊，appimagetool 依它以內附的 zsyncmake 產生 .zsync。
#
# 環境變數：
#   APPIMAGETOOL_RUNTIME_FILE  runtime 的本機檔；沒設時下載固定版本到工具快取資料夾。兩種都核對檢查碼
#   APPIMAGE_TOOLS_DIR         下載工具的快取資料夾，預設 build/appimage-tools
#   PYTHON                     產生佔位圖示用的 Python，預設 python3
set -euo pipefail
cd "$(dirname "$0")/.."

readonly APPIMAGETOOL_URL=https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage
readonly APPIMAGETOOL_SHA256=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
readonly RUNTIME_URL=https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64
readonly RUNTIME_SHA256=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d

# latest 只認正式發行版：發行版勾了「預發行版」時，已安裝的 AppImage 檢查不到它
readonly UPDATE_INFORMATION="gh-releases-zsync|Suikann|ling-ling-suite|latest|LingLingSuite-x86_64.AppImage.zsync"
readonly OUTPUT_NAME=LingLingSuite-x86_64.AppImage

# .desktop 的檔名、Icon、StartupWMClass 都是 src/core/constants.py 的 APP_ID（tests/test_linux_desktop_entry.py 核對）
readonly DESKTOP_FILE=packaging/linux/lingling-suite.desktop
readonly APP_RUN=packaging/linux/AppRun
# 程式圖示（#91）：交件後填上 repo 裡圖示檔的路徑（.png 或 .svg）。空著時放全透明的佔位圖，
# 因為 appimagetool 找不到 .desktop 的 Icon 指的檔就不肯打包
readonly ICON_FILE=""

readonly BUNDLE=dist/LingLingSuite
readonly APPDIR=build/LingLingSuite.AppDir
TOOLS_DIR="${APPIMAGE_TOOLS_DIR:-build/appimage-tools}"

# 檔案的 SHA-256 等於期望值時成功
sha256_matches() {
    local file=$1 expected=$2
    [[ -f "$file" ]] && echo "$expected  $file" | sha256sum --check --status
}

# 下載固定版本的檔到 dest 並核對檢查碼；快取裡已有、檢查碼也對時不重新下載
fetch_verified() {
    local url=$1 expected=$2 dest=$3
    if sha256_matches "$dest" "$expected"; then
        return
    fi
    mkdir -p "$(dirname "$dest")"
    curl -fsSL --retry 3 -o "$dest.part" "$url"
    if ! sha256_matches "$dest.part" "$expected"; then
        rm -f "$dest.part"
        echo "檢查碼不符：$url" >&2
        exit 1
    fi
    mv "$dest.part" "$dest"
}

warn() {
    if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
        echo "::warning::$1"
    else
        echo "警告：$1" >&2
    fi
}

# 寫出 256x256、全透明的 PNG（只用標準函式庫）
write_placeholder_icon() {
    "${PYTHON:-python3}" - "$1" <<'PY'
import struct
import sys
import zlib

size = 256
pixels = b"".join(b"\x00" + b"\x00\x00\x00\x00" * size for _ in range(size))


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(pixels, 9))
    + chunk(b"IEND", b"")
)
with open(sys.argv[1], "wb") as out:
    out.write(png)
PY
}

if [[ ! -x "$BUNDLE/LingLingSuite" ]]; then
    echo "找不到 $BUNDLE/LingLingSuite，請先以 LingLingSuite.spec 建出資料夾版（./build.sh）。" >&2
    exit 1
fi

appimagetool="$TOOLS_DIR/appimagetool-x86_64.AppImage"
fetch_verified "$APPIMAGETOOL_URL" "$APPIMAGETOOL_SHA256" "$appimagetool"
chmod +x "$appimagetool"

if [[ -n "${APPIMAGETOOL_RUNTIME_FILE:-}" ]]; then
    if ! sha256_matches "$APPIMAGETOOL_RUNTIME_FILE" "$RUNTIME_SHA256"; then
        echo "APPIMAGETOOL_RUNTIME_FILE 不是固定版本的 runtime：$APPIMAGETOOL_RUNTIME_FILE" >&2
        exit 1
    fi
else
    APPIMAGETOOL_RUNTIME_FILE="$TOOLS_DIR/runtime-x86_64"
    fetch_verified "$RUNTIME_URL" "$RUNTIME_SHA256" "$APPIMAGETOOL_RUNTIME_FILE"
fi

echo "組出 AppDir..."
icon_name="$(basename "$DESKTOP_FILE" .desktop)"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a "$BUNDLE" "$APPDIR/usr/lib/LingLingSuite"
install -m 755 "$APP_RUN" "$APPDIR/AppRun"
install -m 644 "$DESKTOP_FILE" "$APPDIR/$icon_name.desktop"
if [[ -n "$ICON_FILE" ]]; then
    install -m 644 "$ICON_FILE" "$APPDIR/$icon_name.${ICON_FILE##*.}"
else
    warn "還沒有程式圖示（#91），AppImage 用全透明的佔位圖示"
    write_placeholder_icon "$APPDIR/$icon_name.png"
fi

echo "打包 AppImage..."
rm -f "dist/$OUTPUT_NAME" "dist/$OUTPUT_NAME.zsync"
appimagetool_path="$(realpath "$appimagetool")"
runtime_path="$(realpath "$APPIMAGETOOL_RUNTIME_FILE")"
appdir_path="$(realpath "$APPDIR")"
# 在 dist/ 裡執行：.zsync 寫在目前的資料夾，裡面記的下載網址是與它同層的 AppImage 檔名
(
    cd dist
    APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$appimagetool_path" \
        --runtime-file "$runtime_path" \
        --updateinformation "$UPDATE_INFORMATION" \
        "$appdir_path" "$OUTPUT_NAME"
)

for produced in "dist/$OUTPUT_NAME" "dist/$OUTPUT_NAME.zsync"; do
    if [[ ! -s "$produced" ]]; then
        echo "appimagetool 沒有產出 $produced" >&2
        exit 1
    fi
done
echo "建置完成：dist/$OUTPUT_NAME、dist/$OUTPUT_NAME.zsync"
