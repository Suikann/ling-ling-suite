# -*- coding: utf-8 -*-
"""
drive.file 與桌面 Picker 的實測腳本（丟棄式，不進主線）

只用來回答 #72 的四個測試項目；步驟見同目錄的 drive-file-picker-test.md。
每次呼叫的結果附加到 <狀態目錄>/log.jsonl，最後用 report 產出可貼進 ticket 的摘要。

用法（在 repo 根目錄，以 .venv 的 Python 執行）：
    python docs/notes/drive_file_picker_probe.py --client-secret <JSON> <指令> [參數]

指令：
    login                    只要求 drive.file、不開 Picker，取得權杖
    pick [--sheet|--files]   開桌面 Picker；預設選資料夾，--sheet 只顯示試算表，--files 只顯示 PDF
    tree <根資料夾ID> [--expect <ID> ...] [--refresh]
                             遞迴列出根資料夾底下的資料夾與檔案，並以 ID 逐一 files.get
    write <資料夾ID> <PDF ID>  在手動建立的資料夾與 PDF 上做寫入動作，做完復原
    sheet <試算表ID>          讀試算表、新增再刪除一個工作表
    old-sheet                用舊範圍（spreadsheets、drive）建一份試算表，只給沒有舊譜庫試算表的測試帳號用
    report                   把紀錄檔整理成 Markdown 摘要
"""
import argparse
import datetime
import http.server
import importlib.metadata
import json
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
OLD_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
PDF_MIME = "application/pdf"
LOGIN_TIMEOUT_SECONDS = 300
# 瀏覽器同時登入多個 Google 帳號時，預先帶入譜庫帳號；由 --login-hint 設定
LOGIN_HINT = ""
PACKAGES = ["google-auth", "google-auth-oauthlib", "google-api-python-client", "oauthlib"]

# 最小的合法單頁 PDF，寫入測試上傳用
TINY_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 72 72]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


# ── 紀錄 ──────────────────────────────────────────────

class Log:
    """把每一步的結果附加到 log.jsonl，同時印到終端機"""

    def __init__(self, state_dir: str, step: str):
        self.path = os.path.join(state_dir, "log.jsonl")
        self.step = step

    def write(self, kind: str, **fields):
        entry = {
            "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "step": self.step,
            "kind": kind,
            **fields,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        shown = {k: v for k, v in fields.items() if v not in (None, "", [])}
        print(f"[{kind}] " + json.dumps(shown, ensure_ascii=False))


def http_error_fields(e: HttpError) -> dict:
    """取出 HTTP 狀態碼與 Google 的錯誤 reason（第 4 項：403 還是 404）"""
    reason = ""
    message = ""
    try:
        body = json.loads(e.content.decode("utf-8"))
        err = body.get("error", {})
        message = err.get("message", "")
        details = err.get("errors") or []
        if details:
            reason = details[0].get("reason", "")
    except Exception:
        message = str(e)
    return {"ok": False, "status": e.resp.status, "reason": reason, "message": message}


def call(log: Log, kind: str, func, **fields):
    """執行一次 API 呼叫並記錄成功或失敗，回傳結果（失敗回傳 None）"""
    try:
        result = func()
    except HttpError as e:
        log.write(kind, **fields, **http_error_fields(e))
        return None
    log.write(kind, **fields, ok=True)
    return result


# ── 登入 ──────────────────────────────────────────────

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """接一次瀏覽器的重新導向，保留完整網址（run_local_server 不回傳它）"""

    captured = None
    done = threading.Event()

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in params and "error" not in params:
            self.send_response(404)
            self.end_headers()
            return
        type(self).captured = self.path
        type(self).done.set()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<p>已收到，可以關閉這個分頁回到終端機。</p>".encode("utf-8"))

    def log_message(self, *args):
        pass


def browser_login(client_secret: str, scopes: list, extra_params: dict) -> tuple:
    """開系統瀏覽器授權，回傳 (Credentials, 重新導向帶回的參數)"""
    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    _CallbackHandler.captured = None
    _CallbackHandler.done.clear()
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"
    flow = Flow.from_client_secrets_file(client_secret, scopes, redirect_uri=redirect_uri)
    state = secrets.token_urlsafe(16)
    if LOGIN_HINT:
        extra_params = {**extra_params, "login_hint": LOGIN_HINT}
    auth_url, _ = flow.authorization_url(state=state, access_type="offline", **extra_params)

    print("在瀏覽器完成授權；沒有自動開啟時，手動開這個網址：")
    print(auth_url)
    webbrowser.open(auth_url, new=1)

    threading.Thread(target=server.serve_forever, daemon=True).start()
    _CallbackHandler.done.wait(LOGIN_TIMEOUT_SECONDS)
    server.shutdown()
    server.server_close()
    if _CallbackHandler.captured is None:
        sys.exit("等候瀏覽器逾時")

    params = urllib.parse.parse_qs(urllib.parse.urlparse(_CallbackHandler.captured).query)
    flat = {k: v[0] for k, v in params.items()}
    if "error" in flat:
        sys.exit(f"授權失敗：{flat}")
    if flat.get("state") != state:
        sys.exit("state 不符，放棄這次授權")
    # 回傳的 scope 與要求的不同時 oauthlib 會報錯；範圍另由 tokeninfo 檢查
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    flow.fetch_token(code=flat["code"])
    return flow.credentials, flat


def token_scopes(creds: Credentials) -> list:
    """向 Google 查這個 access token 實際帶的範圍"""
    resp = requests.get(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"access_token": creds.token},
        timeout=30,
    )
    resp.raise_for_status()
    return sorted(resp.json().get("scope", "").split())


def ensure_drive_file_only(creds: Credentials, log: Log):
    """權杖若帶 drive.file 以外的 Drive／Sheets 範圍，測試結果就不成立，直接中止"""
    scopes = token_scopes(creds)
    extra = [s for s in scopes if s != DRIVE_FILE and ("drive" in s or "spreadsheets" in s)]
    log.write("scope-check", scopes=scopes, ok=not extra)
    if extra:
        sys.exit(
            "權杖帶了 drive.file 以外的範圍，測試不成立。到 "
            "https://myaccount.google.com/connections 移除這個 app 的存取權後重跑，"
            "並在結果欄註明。"
        )


def save_creds(state_dir: str, name: str, creds: Credentials):
    path = os.path.join(state_dir, name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def load_creds(state_dir: str, name: str, refresh: bool = False) -> Credentials:
    path = os.path.join(state_dir, name)
    if not os.path.isfile(path):
        sys.exit(f"找不到 {path}，先跑 login")
    creds = Credentials.from_authorized_user_file(path)
    if refresh or not creds.valid:
        creds.refresh(Request())
        save_creds(state_dir, name, creds)
    return creds


def log_account(creds: Credentials, log: Log):
    """記下授權的是哪個帳號（只印在終端機，不寫進紀錄檔，避免貼到公開 ticket）"""
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    about = call(log, "about.get", lambda: drive.about().get(fields="user").execute())
    if about:
        print(f"授權帳號：{about['user'].get('emailAddress')}")


def cmd_login(args, log: Log):
    creds, params = browser_login(
        args.client_secret, [DRIVE_FILE], {"prompt": "consent"},
    )
    log.write("redirect-params", keys=sorted(k for k in params if k != "code"))
    ensure_drive_file_only(creds, log)
    save_creds(args.state_dir, "token.json", creds)
    log_account(creds, log)


def cmd_pick(args, log: Log):
    extra = {"prompt": "consent", "trigger_onepick": "true", "allow_multiple": "true"}
    if args.sheet:
        extra["mimetypes"] = SHEET_MIME
    elif args.files:
        extra["mimetypes"] = PDF_MIME
    else:
        extra["allow_folder_selection"] = "true"
    log.write("picker-params", **{k: v for k, v in extra.items()})
    creds, params = browser_login(args.client_secret, [DRIVE_FILE], extra)
    picked = [i for i in params.get("picked_file_ids", "").split(",") if i]
    # 參數名稱以實際帶回的為準，一併記下
    log.write(
        "picker-result",
        picked_file_ids=picked,
        redirect_keys=sorted(k for k in params if k != "code"),
        ok=bool(picked),
    )
    ensure_drive_file_only(creds, log)
    save_creds(args.state_dir, "token.json", creds)
    log_account(creds, log)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    for file_id in picked:
        meta = call(
            log, "files.get(picked)",
            lambda fid=file_id: drive.files().get(fileId=fid, fields="id,name,mimeType").execute(),
            id=file_id,
        )
        if meta:
            print(f"  選到：{meta['name']}（{meta['mimeType']}）")


# ── 讀取與寫入測試 ────────────────────────────────────

def probe_file(drive, log: Log, file_id: str, label: str):
    """以 ID 讀中繼資料，PDF 再下載前 1 KB 確認內容讀得到"""
    meta = call(
        log, "files.get",
        lambda: drive.files().get(
            fileId=file_id,
            fields="id,name,mimeType,md5Checksum,capabilities(canEdit,canRename,canAddChildren)",
        ).execute(),
        id=file_id, via=label,
    )
    if not meta or meta["mimeType"] != PDF_MIME:
        return meta
    request = drive.files().get_media(fileId=file_id)
    request.headers["Range"] = "bytes=0-1023"
    head = call(log, "files.get(media)", request.execute, id=file_id, name=meta["name"])
    if head is not None:
        log.write("pdf-header", id=file_id, ok=head.startswith(b"%PDF"))
    return meta


def cmd_tree(args, log: Log):
    creds = load_creds(args.state_dir, "token.json", refresh=args.refresh)
    ensure_drive_file_only(creds, log)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    root = probe_file(drive, log, args.root, "root")
    seen = set()
    queue = [(args.root, root["name"] if root else args.root, 0)]
    counts = {"folders": 0, "pdfs": 0, "others": 0}
    while queue:
        folder_id, name, depth = queue.pop(0)
        print("  " * depth + f"[{name}]")
        listing = call(
            log, "files.list",
            lambda fid=folder_id: drive.files().list(
                q=f"'{fid}' in parents and trashed = false",
                fields="files(id,name,mimeType)",
                pageSize=1000,
            ).execute(),
            parent=folder_id, parent_name=name,
        )
        if listing is None:
            continue
        children = listing.get("files", [])
        log.write("files.list-count", parent=folder_id, parent_name=name, count=len(children))
        for child in children:
            seen.add(child["id"])
            if child["mimeType"] == FOLDER_MIME:
                counts["folders"] += 1
                queue.append((child["id"], child["name"], depth + 1))
            else:
                counts["pdfs" if child["mimeType"] == PDF_MIME else "others"] += 1
                print("  " * (depth + 1) + child["name"])
                probe_file(drive, log, child["id"], "list")
    log.write("tree-summary", root=args.root, **counts)

    for file_id in args.expect or []:
        meta = probe_file(drive, log, file_id, "expect")
        log.write("expect", id=file_id, listed=file_id in seen, readable=meta is not None)


def cmd_write(args, log: Log):
    """建子資料夾、上傳、改名、換內容、移動，最後把手動的 PDF 復原、刪掉建立的東西"""
    creds = load_creds(args.state_dir, "token.json")
    ensure_drive_file_only(creds, log)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    files = drive.files()

    sub = call(
        log, "create-subfolder",
        lambda: files.create(
            body={"name": "泠靈實測-子資料夾", "mimeType": FOLDER_MIME, "parents": [args.folder]},
            fields="id",
        ).execute(),
        parent=args.folder,
    )
    up = call(
        log, "upload-into-folder",
        lambda: files.create(
            body={"name": "泠靈實測-上傳.pdf", "parents": [args.folder]},
            media_body=MediaInMemoryUpload(TINY_PDF, mimetype=PDF_MIME),
            fields="id",
        ).execute(),
        parent=args.folder,
    )

    meta = call(
        log, "get-manual-pdf",
        lambda: files.get(fileId=args.pdf, fields="name,parents").execute(),
        id=args.pdf,
    )
    if meta:
        original = meta["name"]
        renamed = call(
            log, "rename-manual-pdf",
            lambda: files.update(fileId=args.pdf, body={"name": original + "（改名測試）"}).execute(),
            id=args.pdf,
        )
        if renamed:
            call(
                log, "rename-back",
                lambda: files.update(fileId=args.pdf, body={"name": original}).execute(),
                id=args.pdf,
            )
        content = call(log, "download-manual-pdf", lambda: files.get_media(fileId=args.pdf).execute(), id=args.pdf)
        if content is not None:
            # 同樣的內容傳回去，Drive 只多一個舊版，檔案本身不變
            call(
                log, "update-content-manual-pdf",
                lambda: files.update(
                    fileId=args.pdf, media_body=MediaInMemoryUpload(content, mimetype=PDF_MIME),
                ).execute(),
                id=args.pdf,
            )
        if sub:
            old_parents = ",".join(meta.get("parents", []))
            moved = call(
                log, "move-manual-pdf",
                lambda: files.update(
                    fileId=args.pdf, addParents=sub["id"], removeParents=old_parents,
                ).execute(),
                id=args.pdf,
            )
            if moved:
                call(
                    log, "move-back",
                    lambda: files.update(
                        fileId=args.pdf, addParents=old_parents, removeParents=sub["id"],
                    ).execute(),
                    id=args.pdf,
                )

    for created, kind in ((up, "delete-uploaded"), (sub, "delete-subfolder")):
        if created:
            call(log, kind, lambda c=created: files.delete(fileId=c["id"]).execute(), id=created["id"])


def cmd_sheet(args, log: Log):
    creds = load_creds(args.state_dir, "token.json")
    ensure_drive_file_only(creds, log)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()

    call(
        log, "drive.files.get(sheet)",
        lambda: drive.files().get(fileId=args.sheet, fields="id,name").execute(),
        id=args.sheet,
    )
    info = call(
        log, "spreadsheets.get",
        lambda: sheets.get(spreadsheetId=args.sheet, fields="sheets.properties.title").execute(),
        id=args.sheet,
    )
    if info is None:
        return
    first = info["sheets"][0]["properties"]["title"]
    call(
        log, "values.get",
        lambda: sheets.values().get(spreadsheetId=args.sheet, range=f"'{first}'!A1:Z3").execute(),
        id=args.sheet,
    )
    added = call(
        log, "batchUpdate(addSheet)",
        lambda: sheets.batchUpdate(
            spreadsheetId=args.sheet,
            body={"requests": [{"addSheet": {"properties": {"title": "泠靈實測"}}}]},
        ).execute(),
        id=args.sheet,
    )
    if added:
        sheet_id = added["replies"][0]["addSheet"]["properties"]["sheetId"]
        call(
            log, "batchUpdate(deleteSheet)",
            lambda: sheets.batchUpdate(
                spreadsheetId=args.sheet,
                body={"requests": [{"deleteSheet": {"sheetId": sheet_id}}]},
            ).execute(),
            id=args.sheet,
        )


def cmd_old_sheet(args, log: Log):
    """比照 sheets_service.create_catalog_spreadsheet，用舊範圍建一份試算表"""
    creds, _ = browser_login(args.client_secret, OLD_SCOPES, {"prompt": "consent"})
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()
    result = call(
        log, "spreadsheets.create(old-scopes)",
        lambda: sheets.create(body={
            "properties": {"title": "泠靈實測 舊範圍試算表"},
            "sheets": [{"properties": {"title": "曲目"}}],
        }).execute(),
    )
    if result:
        print(f"試算表 ID：{result['spreadsheetId']}")
    # 舊範圍的權杖不保存，之後一律只用 drive.file


# ── 摘要 ──────────────────────────────────────────────

def cmd_report(args, log: Log):
    path = os.path.join(args.state_dir, "log.jsonl")
    if not os.path.isfile(path):
        sys.exit("還沒有紀錄")
    with open(path, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    versions = ", ".join(f"{p} {importlib.metadata.version(p)}" for p in PACKAGES)
    print(f"- 套件：Python {sys.version.split()[0]}, {versions}")
    print(f"- 期間：{entries[0]['time']} 至 {entries[-1]['time']}\n")
    print("| 時間 | 步驟 | 動作 | 結果 | 細節 |")
    print("| --- | --- | --- | --- | --- |")
    skip = {"time", "step", "kind", "ok", "message"}
    for e in entries:
        result = {True: "成功", False: "失敗"}.get(e.get("ok"), "")
        detail = "; ".join(f"{k}={v}" for k, v in e.items() if k not in skip)
        print(f"| {e['time'][11:19]} | {e['step']} | {e['kind']} | {result} | {detail} |")


def main():
    parser = argparse.ArgumentParser(description="drive.file 與桌面 Picker 的實測")
    parser.add_argument("--client-secret", default="client_secret.json", help="桌面用戶端的 JSON")
    parser.add_argument(
        "--state-dir",
        default=os.path.join(os.path.expanduser("~"), ".cache", "lingling-picker-test"),
        help="權杖與紀錄檔放的地方（測完整個刪掉）",
    )
    parser.add_argument("--step", default="", help="寫進紀錄的步驟名稱，例如 1、2、3a")
    parser.add_argument("--login-hint", default="", help="預先帶入的 Google 帳號 email")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login")
    p = sub.add_parser("pick")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--sheet", action="store_true")
    group.add_argument("--files", action="store_true")
    p = sub.add_parser("tree")
    p.add_argument("root")
    p.add_argument("--expect", nargs="*")
    p.add_argument("--refresh", action="store_true", help="先用 refresh token 換新的 access token")
    p = sub.add_parser("write")
    p.add_argument("folder")
    p.add_argument("pdf")
    p = sub.add_parser("sheet")
    p.add_argument("sheet")
    sub.add_parser("old-sheet")
    sub.add_parser("report")
    args = parser.parse_args()
    global LOGIN_HINT
    LOGIN_HINT = args.login_hint

    if args.command in ("login", "pick", "old-sheet") and not os.path.isfile(args.client_secret):
        sys.exit(
            f"找不到用戶端 JSON：{os.path.abspath(args.client_secret)}\n"
            "到 Cloud Console → API 和服務 → 憑證，在泠靈用的「電腦版應用程式」OAuth 用戶端"
            "按下載 JSON，存成上面這個路徑，或用 --client-secret 指定。"
        )
    os.makedirs(args.state_dir, mode=0o700, exist_ok=True)
    log = Log(args.state_dir, args.step or args.command)
    handlers = {
        "login": cmd_login, "pick": cmd_pick, "tree": cmd_tree, "write": cmd_write,
        "sheet": cmd_sheet, "old-sheet": cmd_old_sheet, "report": cmd_report,
    }
    handlers[args.command](args, log)


if __name__ == "__main__":
    main()
