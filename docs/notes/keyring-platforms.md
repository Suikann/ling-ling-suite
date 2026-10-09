# keyring 跨平台行為查證

- 來源 ticket：[#49](https://github.com/Suikann/ling-ling-suite/issues/49)（地圖 [#43](https://github.com/Suikann/ling-ling-suite/issues/43)，前置決定見 [#46](https://github.com/Suikann/ling-ling-suite/issues/46)）
- 調查日期：2026-10-10
- 版本基準：`keyring` 25.7.0（2025-11-16 發布，調查時的最新版）、`pywin32-ctypes` 0.2.3、`SecretStorage` 3.5.0、`jeepney` 0.9.0、`keyrings.alt` 5.0.2、`google-auth` 2.61.0、`google-auth-oauthlib` 1.5.0、PyInstaller 6.22.3、`pyinstaller-hooks-contrib` 2026.8
- 實驗環境：WSL2 上的 Ubuntu 22.04.5（`systemd=true`、WSLg），Python 3.10.12。CD 用的是 Python 3.12，差別只在 `keyring` 讀 entry points 時改用標準庫 `importlib.metadata`（`keyring/compat/py312.py`），不影響下面的結論。
- 行號以上述版本的 wheel 內容為準。

## 結論摘要

| 問題 | 答案 |
| --- | --- |
| Windows 單筆上限 | `CredentialBlob` 最多 2560 bytes（`CRED_MAX_CREDENTIAL_BLOB_SIZE`，5×512）。`keyring` 一律存成 UTF-16，所以實際上限是 1280 個 UTF-16 字元。超過時 `keyring` 不檢查、不分段，直接把 `CredWrite` 的錯誤往外丟：`win32ctypes.pywin32.pywintypes.error`（winerror 1783，「The stub received bad data」）。這個例外不是 `KeyringError` 的子類別。 |
| Linux 沒有 Secret Service | 只裝 `keyring` 本身時，自動選到 `keyring.backends.fail.Keyring`（priority 0），之後每次 get、set、delete 都丟 `NoKeyringError`。chainer 要有兩個以上 priority > 0 的 backend 才會被選。環境裡若另外裝了 `keyrings.alt`，會**默默**改選 `PlaintextKeyring`，把密碼以 base64 寫進 `~/.local/share/python_keyring/keyring_pass.cfg`。可靠的偵測方式：不要用自動選擇，自己指定 backend 類別、檢查 `viable`，所有 keyring 呼叫都用 `except Exception` 包起來。 |
| Hyprland 與 WSL | Hyprland 預設不帶 Secret Service，結果與上一列相同（以 `dbus-run-session` 模擬，實測選到 `fail.Keyring`）。只要裝了 `gnome-keyring` 套件，即使沒啟動，也會因為 D-Bus 可以自動啟動它而判定為可用，第一次存取時才跳出解鎖或建立 keyring 的視窗。WSL 實測：session bus 的 socket 不存在，`SecretService` 不可用，選到 `fail.Keyring`。 |
| PyInstaller | PyInstaller 本身內建 `hook-keyring.py`（不在 hooks-contrib），會收集 `keyring.backends.*` 並複製 `keyring` 的 metadata（含 `entry_points.txt`），不必另加參數。實測打包後抓得到 entry points；拿掉 metadata 後只剩 `fail`，等於「看起來沒有安全儲存」。`keyrings.alt` 即使裝在建置環境裡也不會被打包進去。 |
| 只存 refresh token 如何重建 | `Credentials(token=None, refresh_token=rt, token_uri=..., client_id=..., client_secret=..., scopes=...)` 後呼叫 `refresh(Request())`；`client_id`、`client_secret` 從隨程式打包的 `client_secret.json` 取得。注意：這樣建出來的物件 `expired` 是 `False`（沒有 `expiry`），現行 `get_credentials` 用 `expired` 判斷要不要 refresh，會因此漏掉。改用 `from_authorized_user_info` 則會自動設成已過期。 |

## 1. Windows 認證管理員的大小上限與超過時的行為

### 上限

- `CREDENTIAL.CredentialBlobSize`：「This member cannot be larger than CRED_MAX_CREDENTIAL_BLOB_SIZE (5\*512) bytes.」也就是 2560 bytes。（[CREDENTIALW 文件](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)）
- 同一份文件的其他上限：generic 型別的 `TargetName` 最多 32767 字元、`UserName` 最多 513 字元、`Comment` 最多 256 字元。
- `CRED_TYPE_GENERIC` 的 blob 內容「is defined by the application」，Windows 不規定編碼。

### keyring 怎麼寫入

- `keyring.backends.Windows.WinVaultKeyring._set_password` 把字串直接當成 `CredentialBlob` 交給 `win32cred.CredWrite`，沒有任何長度檢查（`keyring/backends/Windows.py:136-145`）。
- `pywin32-ctypes` 用 `ctypes.create_unicode_buffer` 把字串轉成 wchar 緩衝區，`CredentialBlobSize = sizeof(buffer) - sizeof(c_wchar)`（`win32ctypes/core/ctypes/_authentication.py:53-65`）。Windows 的 wchar 是 2 bytes，所以寫入的是 UTF-16，**每個 UTF-16 字元佔 2 bytes**。
- keyring 的 changelog 也明寫「Passwords are still stored as UTF-16.」（[NEWS.rst v21.8.0，#438](https://github.com/jaraco/keyring/blob/v25.7.0/NEWS.rst)）。讀取時先試 UTF-16 解碼，失敗再改用 UTF-8（`Windows.py:47-62`）。
- 換算：2560 bytes ÷ 2 = **1280 個 UTF-16 字元**。BMP 以外的字元（例如 emoji）一個佔 2 個 UTF-16 字元。

### 超過上限時

- Microsoft 的 `CredWriteW` 文件列出的錯誤碼沒有一個對應「blob 太大」（[CredWriteW 文件](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)）。**查無**官方說明 blob 過大時回傳哪個錯誤碼。
- 實際行為來自 keyring 的 issue [#355](https://github.com/jaraco/keyring/issues/355)「Obscure error when credential is longer than allowed」：
  - 2021-08 一位留言者用二分法找出上限是 1280 字元：`'a' * 1280` 可以存，`'a' * 1281` 失敗，錯誤是 `OSError: [WinError 1783] The stub received bad data.`，經 `pywin32-ctypes` 轉成 `win32ctypes.pywin32.pywintypes.error: (1783, 'CredWrite', 'The stub received bad data')`。2021-11 另一位在 Windows 10 Pro 21H1 上確認同樣是 1280。2020-02 也有人回報 Windows 10 與 Windows Server 2019 出現同一個 1783 錯誤。
  - 維護者 jaraco 表示想不出合適的修法，2020-04-30 關閉 issue。之後有人提議自動分段存放，沒有下文。
- keyring 25.7.0 仍然沒有攔截這個錯誤（`Windows.py:136-145`）。`win32ctypes.pywin32.pywintypes.error` 直接繼承 `Exception`（`win32ctypes/pywin32/pywintypes.py:15-31`），**不是** `keyring.errors.KeyringError`，所以只攔 `KeyringError` 會漏掉。
- `keyrings.alt` 有 `multi.MultipartKeyringWrapper`，會把密碼切成多段分開存放，就是為了 Windows Vault 的長度限制（`keyrings/alt/multi.py:8-16`）。它的 priority 是 0，不會被自動選上，要自己包裝。

### 套到泠靈

- Google 文件寫明 refresh token 最多 512 bytes，access token 最多 2048 bytes（[Google OAuth 2.0：Token size](https://developers.google.com/identity/protocols/oauth2#size)）。
- 實測（假資料，見附錄的實驗 E）：
  - 只存 refresh token：最多 512 字元，也就是 1024 bytes（UTF-16），在上限內。
  - 整份 `Credentials.to_json()`：access token 約 255 字元時是 1444 bytes；token 長度都取文件上限時是 5848 bytes，超過 2560。
  - 因此 #46「只存 refresh token」的決定在 Windows 上是必要的；#46 寫的「2560 bytes」換算成字串長度是 1280 字元。
- 附帶一提：`WinVaultKeyring` 預設的持久範圍是 `CRED_PERSIST_ENTERPRISE`（`Windows.py:30-32`）。依 Microsoft 文件，使用者有漫遊設定檔時，這個範圍的憑證會跟著帳號同步到其他電腦。可以設 `persist = 'local machine'` 改成只留在本機（`Windows.py:34-44`）。

## 2. Linux 沒有 Secret Service 時，keyring 選哪個 backend

### 選擇邏輯

`keyring.core._detect_backend`（`keyring/core.py:96-115`）依序：

1. `load_env()`：環境變數 `PYTHON_KEYRING_BACKEND` 指定的類別。
2. `load_config()`：設定檔 `keyringrc.cfg` 的 `default-keyring`；Linux 在 `$XDG_CONFIG_HOME/python_keyring/`，Windows 在 `%LOCALAPPDATA%\Python Keyring\`（`keyring/util/platform_.py`）。設定檔還能用 `keyring-path` 把任意路徑插進 `sys.path`（`core.py:196-202`）。
3. 以上都沒有，才從所有 `viable` 的 backend 裡挑 priority 最高的；一個都沒有時用 `fail.Keyring()`。

注意：`init_backend(limit=...)` 的 `limit` 只作用在第 3 步。環境變數或設定檔一旦有指定，`limit` 完全不起作用。實測：設 `PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring` 後，即使 `limit` 只允許 `WinVaultKeyring` 與 `SecretService.Keyring`，結果仍是 `PlaintextKeyring`（實驗 D）。

### 各 backend 的可用條件（priority）

| backend | priority | 不可用的條件 |
| --- | --- | --- |
| `SecretService.Keyring` | 5 | 沒有 `secretstorage`；連不上 session bus；`org.freedesktop.secrets` 既沒有人持有、也不在可自動啟動清單（`SecretService.py:33-50`、`secretstorage/__init__.py` 的 `check_service_availability`） |
| `kwallet.DBusKeyring` | 5.1（KDE）或 4.9 | 需要 `dbus-python`。它不是 keyring 的相依套件，pip 安裝常需要編譯（`kwallet.py:40-56`、[README「Installation - Linux」](https://github.com/jaraco/keyring/blob/v25.7.0/README.rst)） |
| `libsecret.Keyring` | 4.8 | 需要 PyGObject 與 libsecret，也不是相依套件（`libsecret.py:50-60`） |
| `chainer.ChainerBackend` | 10 或 -1 | 有兩個以上 priority > 0 的 backend 時是 10，否則是 -1（`chainer.py:21-45`） |
| `fail.Keyring` | 0 | 永遠可用，但每個操作都丟 `NoKeyringError`（`fail.py:17-30`） |

- keyring 在 Linux 的相依套件只有 `SecretStorage>=3.2` 與 `jeepney>=0.4.2`（wheel METADATA 的 `Requires-Dist`），所以實際上只有 `SecretService` 一個安全 backend 可用。
- `viable` 會攔下 `priority` 丟出的**任何** `Exception`（`backend.py:94-97`）。但直接讀 `SecretService.Keyring.priority` 不一定只丟 `RuntimeError`：session bus 的 socket 檔不存在時，`jeepney` 丟的 `FileNotFoundError` 不在 `secretstorage.dbus_init` 攔截的範圍內（只攔 `KeyError`、`ConnectionError`、`ValueError`），會原樣往外丟（實驗 A、D）。
- 官方定義：priority 大於 0、小於 1 叫「suitable」，大於等於 1 才叫「recommended」（`backend.py:73-89`）；`keyring.core.recommended(backend)` 就是檢查 `priority >= 1`（`core.py:85-86`）。

### 沒有 Secret Service 時的實際結果

- **只裝 keyring**：選到 `fail.Keyring`，`set_password` 丟 `keyring.errors.NoKeyringError: No recommended backend was available...`（實驗 A）。`NoKeyringError` 同時繼承 `KeyringError` 與 `RuntimeError`（`keyring/errors.py`）。
- **不會選到 chainer**：沒有其他 backend 時，chainer 的 priority 是 -1，低於 `fail` 的 0。
- **環境裡有 `keyrings.alt`**：
  - 它不是 keyring 的相依套件，README 形容它是「"alternate", possibly-insecure backends」（[README「Third-Party Backends」](https://github.com/jaraco/keyring/blob/v25.7.0/README.rst)）。Arch 的 `python-keyring` 把它列為 optdepends（[Arch python-keyring](https://archlinux.org/packages/extra/any/python-keyring/)）。
  - 只有 `keyrings.alt` 時：選到 `keyrings.alt.file.PlaintextKeyring`（priority 0.5），**沒有任何警告**。實測寫入後的檔案 `python_keyring/keyring_pass.cfg` 內容是 `probe = ZHVtbXktcmVmcmVzaC10b2tlbg==`，也就是 `dummy-refresh-token` 的 base64（實驗 B）。
  - 另外還裝了 `pycryptodome` 時（泠靈的 `requirements.txt` 就有）：`keyrings.alt.file.EncryptedKeyring`（0.6）也可用，加上 `PlaintextKeyring` 一共兩個，於是選到 chainer（priority 10），寫入時由 `EncryptedKeyring` 處理（實驗 C）。`EncryptedKeyring` 用 `getpass.getpass` 在終端機要求主密碼（`keyrings/alt/file.py:64-67, 177-182`），GUI 程式沒有終端機可以輸入。

### 可靠地偵測「這台電腦沒有安全儲存」

綜合上述原始碼與實驗，可靠的做法是：

1. **不用** `keyring.get_keyring()` 的自動選擇。它會受到 `PYTHON_KEYRING_BACKEND`、`keyringrc.cfg`、環境裡裝了哪些第三方 backend 影響，而且 `limit` 擋不住前兩者。
2. 依平台**明確指定**類別：Windows 用 `keyring.backends.Windows.WinVaultKeyring`，Linux 用 `keyring.backends.SecretService.Keyring`。用 `cls.viable` 判斷；不可用就當作「沒有安全儲存」。實驗 D 中這個做法在兩種情況都回傳 `None`。
   - 明確 import 這兩個模組還有一個好處：即使打包時漏掉 metadata，backend 類別也會因為被 import 而完成註冊（見第 4 節）。
3. `viable` 為真**不代表寫得進去**：
   - `SecretService` 只要服務「可被 D-Bus 自動啟動」就算可用（`check_service_availability` 也接受 `ListActivatableNames` 裡有它）。
   - 實際存取時才開 collection；collection 鎖著就呼叫 `unlock()`，使用者關掉提示會丟 `KeyringLocked`，開不了 collection 則丟 `InitError`（`SecretService.py:52-69`）。
   - Secret Service 規格本身也說 collection 可能上鎖、解鎖可能需要提示使用者，使用者可以 dismiss（[Locking and Unlocking](https://specifications.freedesktop.org/secret-service/latest/unlocking.html)、[Prompts and Prompting](https://specifications.freedesktop.org/secret-service/latest/prompts.html)）。
   - Windows 則可能丟 `pywintypes.error`（第 1 節）。
   - 因此所有 keyring 呼叫都要 `except Exception`，失敗就照 #46「不保存」處理。只攔 `KeyringError` 不夠。
4. Secret Service 規格「does not mandate any form of access control」（[What's not included in the API](https://specifications.freedesktop.org/secret-service/latest/ch10.html)）。安全儲存防的是「權杖以檔案形式被複製」，不是同一個使用者底下的其他程式。

## 3. Hyprland 與 WSL 的實際狀況

### Hyprland

- Hyprland wiki 的「Must-have」清單（通知 daemon、Pipewire、XDG Desktop Portal、Authentication Agent、Qt Wayland、字型）**沒有**任何 Secret Service 提供者（[must-have.md](https://github.com/hyprwm/hyprland-wiki/blob/main/content/useful-utilities/must-have.md)）。wiki 只在 uwsm 頁提到 GNOME Keyring：從 TTY 登入時，PAM 沒設 `pam_gnome_keyring.so`，「the keyring will not auto-unlock, and applications may prompt you to unlock it manually」（[uwsm.md「GNOME Keyring PAM setup」](https://github.com/hyprwm/hyprland-wiki/blob/main/content/useful-utilities/uwsm.md)）。
- **沒有任何提供者**：有 session bus、但沒有 `org.freedesktop.secrets`。在本機用 `dbus-run-session` 開一個乾淨的 session bus 模擬，`SecretService` 回報「The Secret Service daemon is neither running nor activatable through D-Bus」，結果選到 `fail.Keyring`，`set_password` 丟 `NoKeyringError`（實驗 A2）。
- **裝了 `gnome-keyring` 但沒啟動**：
  - Arch 的 `gnome-keyring` 套件附有 `usr/share/dbus-1/services/org.freedesktop.secrets.service`，也就是可被 D-Bus 自動啟動（[Arch gnome-keyring 檔案清單](https://archlinux.org/packages/extra/x86_64/gnome-keyring/)）。所以 `SecretService` 會被判定為可用。
  - 第一次存取時 daemon 被自動啟動。login keyring 沒有經 PAM 解鎖，會要求使用者解鎖或建立 keyring；提示視窗由 `gcr` 套件的 `gcr-prompter` 負責（[Arch gcr 檔案清單](https://archlinux.org/packages/extra/x86_64/gcr/)）。
  - 這個提示在 Hyprland 上的實際長相與行為**未實測**。
- **KWallet 6**：`ksecretd` 執行時會向 session bus 註冊 `org.freedesktop.secrets`（[kwalletfreedesktopservice.cpp](https://invent.kde.org/frameworks/kwallet/-/blob/master/src/runtime/ksecretd/kwalletfreedesktopservice.cpp)，約第 158 行）。但它的自動啟動檔登記的名稱是 `org.kde.secretservicecompat`，不是 `org.freedesktop.secrets`（[org.kde.secretservicecompat.service.in](https://invent.kde.org/frameworks/kwallet/-/blob/master/src/runtime/ksecretd/org.kde.secretservicecompat.service.in)）。據此推論：`ksecretd` 沒在執行時，`check_service_availability` 會判定不可用。此推論**未實測**。
- **KeePassXC**：要在設定裡勾選「Enable KeePassXC Freedesktop.org Secret Service Integration」，而且只在 KeePassXC 執行期間提供服務；同一時間只能有一個提供者。官方文件另外教使用者自己建立 `org.freedesktop.secrets.service`，讓 D-Bus 能自動啟動它（[KeePassXC SecretService.adoc](https://github.com/keepassxreboot/keepassxc/blob/develop/docs/topics/SecretService.adoc)）。Arch 的 `keepassxc` 套件本身沒有附這個檔（[Arch keepassxc 檔案清單](https://archlinux.org/packages/extra/x86_64/keepassxc/)）。

### WSL

本機實測（Ubuntu 22.04.5、WSL2、`/etc/wsl.conf` 設 `systemd=true`、有 WSLg）：

- `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`，但 `/run/user/1000/bus` **不存在**：沒有安裝 `dbus-user-session`，`/usr/lib/systemd/user/` 底下沒有 `dbus.socket`。
- `SecretService` 的 priority 丟 `FileNotFoundError` → 不可用 → 選到 `fail.Keyring` → `set_password` 丟 `NoKeyringError`（實驗 A）。
- 拿掉 `DBUS_SESSION_BUS_ADDRESS`（相當於沒開 systemd 的 WSL）時，原因變成「Unable to initialize SecretService: Environment variable DBUS_SESSION_BUS_ADDRESS is unset」，結果相同（實驗 A3）。
- Linux 端的 Python 用不到 Windows 認證管理員：`WinVaultKeyring` 回報「Requires Windows and pywin32」。
- 在 WSL 裡自行安裝並啟動 `gnome-keyring` 的情況**未實測**。keyring README 的「Using Keyring on headless Linux systems」說明了做法：`dbus-run-session` 加上 `gnome-keyring-daemon --unlock`（[README](https://github.com/jaraco/keyring/blob/v25.7.0/README.rst)）。

## 4. PyInstaller 打包時的注意事項

- keyring 用 entry points（group `keyring.backends`）找 backend，**連內建的 backend 也是**。keyring 自己的 `entry_points.txt` 列出 `KWallet`、`SecretService`、`Windows`、`chainer`、`libsecret`、`macOS`。
- `_load_plugins()` 讀 metadata 後 import 這些模組；backend 類別在模組被 import、類別定義時，由 metaclass 註冊（`backend.py:30-49, 225-260`）。換句話說，沒有 metadata、模組也沒被 import 的話，backend 就不存在。
- PyInstaller **本身**（不是 `pyinstaller-hooks-contrib`）內建 [`PyInstaller/hooks/hook-keyring.py`](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/PyInstaller/hooks/hook-keyring.py)：

  ```python
  hiddenimports = collect_submodules('keyring.backends')
  # Keyring performs backend plugin discovery using setuptools entry points, which are listed in the metadata. Therefore,
  # we need to copy the metadata, otherwise no backends will be found at run-time.
  datas = copy_metadata('keyring')
  ```

  `pyinstaller-hooks-contrib` 2026.8 沒有 keyring、`keyrings.alt`、`secretstorage`、`jeepney` 的 hook。Windows 端需要的 `win32ctypes.core` 也由 PyInstaller 內建的 `hook-win32ctypes.core.py` 處理，固定收集 ctypes 實作。
- PyInstaller 文件：`copy_metadata` 是為了讓 `importlib.metadata` 找得到 distribution，「PyInstaller does not collect these metadata files by default」；`collect_entry_point(name)` 會收集某個 entry point 的**所有**提供者，文件舉的例子之一就是 keyring（[PyInstaller hooks 文件](https://pyinstaller.org/en/stable/hooks.html)）。
- 實測（Linux、`--onedir`，指令與現行 CD 相同、不加任何 keyring 參數，見實驗 F）：
  - `dist/probe/_internal/keyring-25.7.0.dist-info/entry_points.txt` 有被打包進去。frozen 程式讀得到 6 個 entry points，`SecretService` 的錯誤訊息與未打包時相同，可見 `secretstorage` 與 `jeepney` 也被正確收集。
  - 手動刪掉 `_internal/keyring-25.7.0.dist-info` 後，entry points 變成空的，註冊的類別只剩 `chainer` 與 `fail`，選到 `fail.Keyring`。這種失敗**不會在啟動時報錯**，只會在第一次存取時丟 `NoKeyringError`，看起來就跟「沒有安全儲存」一樣。在 Windows 上，這會讓明明有認證管理員的電腦被當成沒有。
  - 建置環境裡裝了 `keyrings.alt`（再加 `pycryptodome`）時，未打包的執行結果是 chainer（Encrypted 加 Plaintext）；打包後卻沒有 `keyrings.alt`，選到 `fail.Keyring`。原因是沒有人 import 它、也沒有 hook 收集它。換句話說，**從原始碼執行（`run.bat`）與執行打包版的行為可能不同**。
- 注意事項：
  - 現行 CD 的參數（`cd.yml:35-45`）不需要為 keyring 額外加東西。
  - **不要**為了「保險」改用 `collect_entry_point('keyring.backends')` 或 `--collect-all keyring`：前者會把建置環境裡所有第三方 backend（包括 `keyrings.alt`）一起打包。
  - 照第 2 節明確 import `keyring.backends.Windows`／`keyring.backends.SecretService`，即使 metadata 遺失也不受影響。
  - Windows 版的打包結果**未實測**（本機沒有 Windows）。以上依 hook 原始碼與 Linux 實測推論。

## 5. 只存 refresh token 時重建完整憑證

### 需要的欄位

- 換發 access token 時必須有 `refresh_token`、`token_uri`、`client_id`、`client_secret`，缺一個就丟 `RefreshError`（`google/oauth2/credentials.py:419-429`）。
- `client_id` 與 `client_secret` 在隨程式打包的 `client_secret.json`（installed app 格式，鍵在 `installed` 底下）裡，不用另外存。只有 `refresh_token` 是每個使用者各自不同的祕密。

### 兩種重建方式

1. **直接用建構子**：

   ```python
   Credentials(
       token=None,
       refresh_token=rt,
       token_uri="https://oauth2.googleapis.com/token",
       client_id=cid,
       client_secret=csecret,
       scopes=GOOGLE_SCOPES,
   )
   ```

   - 實測（實驗 E，A 段）：`valid=False`、`expired=False`、`token_state=INVALID`、`expiry=None`。
   - `expired` 的定義是「沒有 `expiry` 就視為永不過期」（`google/auth/credentials.py:88-103`）。
2. **`Credentials.from_authorized_user_info({"refresh_token", "client_id", "client_secret"}, scopes)`**：
   - 只要求這三個鍵，缺了丟 `ValueError`（`credentials.py:488-495`）。
   - 沒給 `expiry` 時會設成「現在減 `REFRESH_THRESHOLD`」，也就是一建好就是過期狀態（`credentials.py:497-504`）。
   - `token_uri` 一律覆寫成 `https://oauth2.googleapis.com/token`（`credentials.py:515`）。
   - 實測：`valid=False`、`expired=True`。

### 換發 access token

- 呼叫 `creds.refresh(google.auth.transport.requests.Request())`，送出的表單欄位是 `client_id`、`client_secret`、`grant_type`、`refresh_token`、`scope`（實驗 E，C 段）。
- 不手動 refresh 也行：把 `token=None` 的憑證交給 API client，第一次請求前 `before_request` 發現 `not self.valid` 就會自動 refresh（`google/auth/credentials.py:203-205, 222-247`；實驗 E，E 段）。

### 要注意的地方

- **現行判斷式會漏掉**：`GoogleAuthService.get_credentials` 用 `if self._credentials.expired and self._credentials.refresh_token` 決定要不要 refresh（`src/services/google_auth_service.py:82-91`）。改用方式 1 重建時 `expired` 是 `False`，不會 refresh，接著因為 `valid` 是 `False` 而回傳 `None`，表現成「沒登入」。改用方式 2，或把條件改成 `not creds.valid`，就不會有這個問題。
- `expired` 與 `valid` 自 google-auth 2.24.0 起標為 deprecated，文件建議改看 `token_state`（`google/auth/credentials.py:95, 112, 118-137`）。
- **refresh token 可能被換掉**：google-auth 會採用回應裡新的 `refresh_token`，沒有才沿用舊的（`google/oauth2/_client.py:459`、`credentials.py:450`；實驗 E，D 段）。RFC 6749 §6 規定伺服器「MAY issue a new refresh token, in which case the client MUST discard the old refresh token」（[RFC 6749 §6](https://www.rfc-editor.org/rfc/rfc6749#section-6)）。所以 refresh 後若 `creds.refresh_token` 變了，要寫回安全儲存。Google 實務上是否會輪替 installed app 的 refresh token：**查無**官方說明。
- **scopes 不能比當初授權的多**：refresh 時 google-auth 會把 `scopes` 放進 `scope` 欄位（`_client.py:513`）。RFC 6749 §6 規定「The requested scope MUST NOT include any scope not originally granted」。日後 `GOOGLE_SCOPES` 若新增 scope，舊的 refresh token 換發會失敗，必須重新登入。
- **refresh token 會失效**：Google 列出的情況包括：
  - 使用者撤銷授權。
  - 六個月沒用。
  - 同一帳號、同一 client ID 超過 100 個 refresh token 時，最舊的會無預警失效。
  - OAuth 同意畫面是 external 類型、發布狀態為「Testing」時，refresh token 7 天後到期。

  （[Google OAuth 2.0：Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)）
  
  第一次 refresh 失敗時，應該清掉已存的 refresh token 並要求重新登入，而不是一直用失效的 token 重試。
- `google-auth-oauthlib` 的 `Flow.authorization_url` 預設帶 `access_type=offline`（`google_auth_oauthlib/flow.py:243`），所以 `run_local_server` 登入後通常會拿到 refresh token。

## 對 #46 決定的補充

以下是查證結果對既有決定的影響，供寫規格時參考，不是新的決定：

- 「Windows 認證管理員單筆 2560 bytes」正確，但對 keyring 來說等於 **1280 個字元**。Google refresh token 最多 512 bytes，放得下。日後若要存其他服務（例如 HuaXia 的 Discord 登入）的權杖，同樣受 1280 字元限制；那些權杖的長度本 ticket **未查**。
- 「無安全儲存就不保存」的判斷要用明確指定的 backend 加 `viable`，再加上每次呼叫的 `except Exception`（第 2 節），不能只看 `get_keyring()` 是不是 `fail.Keyring`。
- 開發環境從原始碼執行時，如果裝了 `keyrings.alt`（加上 `requirements.txt` 裡的 `pycryptodome`），自動選擇會落到加密檔或明文檔。明確指定 backend 可以同時避開這個問題。

## 未能查證

- Windows 上的實際行為（1280 字元上限、錯誤碼 1783）沒有在本機重現，來源是 keyring issue #355 留言者的實測（其中一位註明 Windows 10 Pro 21H1）。Microsoft 文件**查無** blob 過大時回傳哪個錯誤碼。
- Windows 版 PyInstaller 打包後的 keyring 行為未實測。
- Hyprland 實機未測試，是用 `dbus-run-session` 模擬「有 session bus、沒有提供者」。`gnome-keyring` 經 D-Bus 自動啟動後的解鎖或建立提示在 Hyprland 上的行為未實測。
- KWallet 6 沒在執行時是否會被判定為不可用，只讀了原始碼，未實測。
- WSL 裡自行啟動 `gnome-keyring` 的情況未實測。
- Google 是否會輪替 installed app 的 refresh token：查無官方說明。

## 附錄：實驗

腳本放在調查時的 scratchpad，沒有收進 repo。重點輸出如下：

- **A**：WSL，只裝 `keyring`，未打包。
  - `SecretService.Keyring: NOT viable: FileNotFoundError`
  - `selected: keyring.backends.fail.Keyring (priority: 0)`
  - `set_password raised keyring.errors.NoKeyringError`
- **A2**：同 A，但在 `dbus-run-session` 裡執行（模擬 Hyprland 沒有提供者）。
  - `SecretService.Keyring: NOT viable: RuntimeError: The Secret Service daemon is neither running nor activatable through D-Bus`
  - 選到 `fail.Keyring`。
- **A3**：拿掉 `DBUS_SESSION_BUS_ADDRESS`。
  - `RuntimeError: Unable to initialize SecretService: Environment variable DBUS_SESSION_BUS_ADDRESS is unset`
  - 選到 `fail.Keyring`。
- **B**：在 `dbus-run-session` 裡、另裝 `keyrings.alt`，HOME 與 XDG 目錄導到 scratchpad。
  - `selected: keyrings.alt.file.PlaintextKeyring (priority: 0.5)`
  - 寫入後檔案內容是 base64 明文。
- **C**：同 B，再裝 `pycryptodome`。
  - `selected: keyring.backends.chainer.ChainerBackend (priority: 10)`
  - `chained: [EncryptedKeyring (0.6), PlaintextKeyring (0.5)]`
- **D**：比較兩種偵測方式。
  - 沒有覆寫時：`init_backend(limit=安全 backend)` 得到 `fail.Keyring`；明確指定得到 `None`。
  - 設 `PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring` 時：前者得到 `PlaintextKeyring`；後者仍是 `None`。
- **E**：離線重建 `Credentials`，用假的 transport 攔截 token endpoint 的請求。
  - 建構子：`valid False, expired False, token_state INVALID`。
  - `from_authorized_user_info`：`expired True`。
  - refresh 送出 `client_id, client_secret, grant_type, refresh_token, scope`。
  - 回應帶新的 `refresh_token` 時會被採用。
  - `before_request` 會自動 refresh。
  - `to_json()`：UTF-16 長度在一般情況是 1444 bytes，token 取文件上限時是 5848 bytes；只存 refresh token 最多 1024 bytes。
- **F**：PyInstaller 6.22.3 在 Linux 用 `--onedir` 打包探測程式。
  - 正常打包：`entry points: ['KWallet', 'SecretService', 'Windows', 'chainer', 'libsecret', 'macOS']`。
  - 刪掉 `keyring-25.7.0.dist-info` 後：`entry points: []`，選到 `fail.Keyring`。
  - 在裝了 `keyrings.alt` 的環境打包：產物裡沒有 `keyrings`，選到 `fail.Keyring`。

## 來源

- Microsoft Learn：[CREDENTIALW structure](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)、[CredWriteW function](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)
- jaraco/keyring v25.7.0：[原始碼](https://github.com/jaraco/keyring/tree/v25.7.0/keyring)（`core.py`、`backend.py`、`backends/Windows.py`、`backends/SecretService.py`、`backends/chainer.py`、`backends/fail.py`、`backends/kwallet.py`、`backends/libsecret.py`、`util/platform_.py`、`errors.py`）、[README.rst](https://github.com/jaraco/keyring/blob/v25.7.0/README.rst)、[NEWS.rst](https://github.com/jaraco/keyring/blob/v25.7.0/NEWS.rst)、[issue #355](https://github.com/jaraco/keyring/issues/355)
- enthought/pywin32-ctypes v0.2.3：[`win32ctypes/core/ctypes/_authentication.py`](https://github.com/enthought/pywin32-ctypes/blob/v0.2.3/win32ctypes/core/ctypes/_authentication.py)、[`win32ctypes/pywin32/pywintypes.py`](https://github.com/enthought/pywin32-ctypes/blob/v0.2.3/win32ctypes/pywin32/pywintypes.py)
- mitya57/secretstorage 3.5.0：[`secretstorage/__init__.py`](https://github.com/mitya57/secretstorage/blob/3.5.0/secretstorage/__init__.py)
- jaraco/keyrings.alt v5.0.2：[`keyrings/alt/file.py`](https://github.com/jaraco/keyrings.alt/blob/v5.0.2/keyrings/alt/file.py)、[`keyrings/alt/multi.py`](https://github.com/jaraco/keyrings.alt/blob/v5.0.2/keyrings/alt/multi.py)
- freedesktop.org Secret Service API（0.2 DRAFT）：[Introduction](https://specifications.freedesktop.org/secret-service/latest/ch01.html)、[Aliases](https://specifications.freedesktop.org/secret-service/latest/aliases.html)、[Locking and Unlocking](https://specifications.freedesktop.org/secret-service/latest/unlocking.html)、[Prompts and Prompting](https://specifications.freedesktop.org/secret-service/latest/prompts.html)、[What's not included in the API](https://specifications.freedesktop.org/secret-service/latest/ch10.html)
- PyInstaller v6.22.3：[`hook-keyring.py`](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/PyInstaller/hooks/hook-keyring.py)、[`hook-win32ctypes.core.py`](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/PyInstaller/hooks/hook-win32ctypes.core.py)、[Understanding PyInstaller Hooks](https://pyinstaller.org/en/stable/hooks.html)
- google-auth 2.61.0：[`google/oauth2/credentials.py`、`google/auth/credentials.py`、`google/oauth2/_client.py`](https://github.com/googleapis/google-cloud-python/tree/main/packages/google-auth)（行號以 PyPI 2.61.0 wheel 為準）
- google-auth-oauthlib 1.5.0：`google_auth_oauthlib/flow.py`
- Google for Developers：[Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2)（Token size、Refresh token expiration）
- IETF：[RFC 6749 §6 Refreshing an Access Token](https://www.rfc-editor.org/rfc/rfc6749#section-6)
- Hyprland wiki：[must-have.md](https://github.com/hyprwm/hyprland-wiki/blob/main/content/useful-utilities/must-have.md)、[uwsm.md](https://github.com/hyprwm/hyprland-wiki/blob/main/content/useful-utilities/uwsm.md)
- KDE kwallet：[`org.kde.secretservicecompat.service.in`](https://invent.kde.org/frameworks/kwallet/-/blob/master/src/runtime/ksecretd/org.kde.secretservicecompat.service.in)、[`kwalletfreedesktopservice.cpp`](https://invent.kde.org/frameworks/kwallet/-/blob/master/src/runtime/ksecretd/kwalletfreedesktopservice.cpp)
- KeePassXC：[`docs/topics/SecretService.adoc`](https://github.com/keepassxreboot/keepassxc/blob/develop/docs/topics/SecretService.adoc)
- Arch Linux 套件檔案清單：[gnome-keyring](https://archlinux.org/packages/extra/x86_64/gnome-keyring/)、[gcr](https://archlinux.org/packages/extra/x86_64/gcr/)、[kwallet](https://archlinux.org/packages/extra/x86_64/kwallet/)、[keepassxc](https://archlinux.org/packages/extra/x86_64/keepassxc/)、[python-keyring](https://archlinux.org/packages/extra/any/python-keyring/)
