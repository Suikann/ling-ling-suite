# CLAUDE.md

## Role

批判性地解讀用戶的發言，確保能找到問題的核心，而非單純支持用戶的心情。

## Language

- 一律使用中華民國慣用的繁體中文
- 禁止使用 emoji
- 中文內容使用全型標點符號

## Workflow

- After code changes, automatically run `git add` and use `/lingling:git-commit` to do git commit in 繁體中文

## Agent skills

### Issue tracker

issue 與 spec 都在 GitHub Issues（`Suikann/ling-ling-suite`），一律用 `gh` CLI 操作。見 `docs/agents/issue-tracker.md`。

### Triage labels

五個標準 triage 角色對映到中文標籤（`待分類`、`待補資訊`、`待派工`、`待人工`、`不處理`）。見 `docs/agents/triage-labels.md`。

### Domain docs

單一 context：根目錄 `GLOSSARY.md` 加 `docs/adr/`。見 `docs/agents/domain.md`。

---

## Project Overview

Ling Ling Suite（泠靈小工具）是一套專為樂團譜務設計的 Python + PySide6 桌面應用程式，用於解決分譜 PDF 檔案的批次重新命名問題。

核心功能：
- **雙層級模板系統**：通用大模板（Master Template）設定全域命名規則，曲目小模板（Track-Specific Template）基於大模板複製後編輯、可針對個別群組覆寫
- **群組管理**：一個群組 = 一首曲目的一個樂章，專案內可包含任意數量的群組
- **樂器表與排序對應**：使用者輸入樂器表（順序 = 總譜順序），群組內的檔案透過排序與樂器一一對應，位置決定流水號
- **曲名與總譜自動偵測**：檔案進入群組時，從分譜檔名的共同部分提取曲名、依檔名關鍵字找出總譜，各猜一次
- **子資料夾輸出**：可選擇將各群組的檔案分別放入以模板變數命名的子資料夾
- **視覺化編輯介面**：提供即時預覽（含衝突警告），確認後一鍵批次重新命名
- **復原機制**：每次操作自動記錄原始與新檔名的對照，支援復原上次操作
- **專案持久化**：可儲存/載入專案檔，保留模板設定、群組配置與檔案對應

## Commands

```bash
python src/main.py              # 啟動程式
python -m pytest tests/ -v      # 執行測試
```

## Reference docs

- 撰寫或修改 `src/`、`tests/` 下的程式碼前，先讀 `CODING_STANDARDS.md`：程式碼風格、docstring 格式、分層職責與資料流、SOLID／DRY 原則與實作前後的檢查清單
- 找某個職責屬於哪個模組、或新增模組時，先讀 `docs/architecture/modules.md`：`src/` 各檔的職責
- 修改 `src/core/naming.py`、`src/core/template_engine.py`、`TEMPLATE_VARIABLES`、`src/services/rename_service.py` 或 `src/services/drive_rename_service.py` 前，先讀 `docs/architecture/naming.md`：模板變數、命名格式套用、子資料夾輸出
- 修改 `src/core/models.py`、`src/services/project_access.py`、`src/services/project_service.py`、`src/services/import_service.py` 或 `src/services/preferences_service.py` 前，先讀 `docs/architecture/project.md`：專案編輯、開啟與存檔、檔案匯入
- 修改 `src/services/move_history.py`、`src/services/move_service.py`、`src/ui/preview_dialog.py` 或 `src/ui/main_window.py` 的啟動還原（`prompt_pending_recovery`）前，先讀 `docs/architecture/move-history.md`：重新命名預檢、搬移引擎、中斷還原、復原／重做堆疊
- 修改 `src/services/split_service.py`、`src/services/workspace_service.py`、`src/ui/split_dialog.py` 或 `src/ui/workspace_dialog.py` 前，先讀 `docs/architecture/workspace.md`：分割與工作區的規則
- 修改讀寫使用者資料目錄（`APPDATA_DIR`）的程式碼前，先讀 `docs/architecture/data-storage.md`：各檔位置與對應常數

---

## User Workflow

1. 開啟程式，透過檔案總管匯入 PDF 檔案或資料夾
2. 建立或確認群組，將檔案分配至各群組
3. 切到群組分頁，輸入該群組的樂器表（打字，順序 = 總譜順序；目前分頁不是群組時樂器表停用）
4. 每個群組內：排列檔案順序使其與樂器一一對應
5. 填寫各群組的 `{曲名}`、`{樂章編號}`、`{樂章名稱}`（檔案進入群組時已自動偵測一次曲名與總譜；想重猜曲名時按「自動偵測」）
6. 設定大模板；需要時為特定群組建立小模板
7. （選用）啟用子資料夾輸出，設定資料夾名稱模板
8. 預覽結果，確認無衝突
9. 執行重新命名

<!-- lingling:git-workflow trunk=develop guarded=main -->

## Git workflow

Merge a PR only when the user says to, and only with `lingling-merge <n>`, which ships in the `lingling` plugin's `bin/` and is on `PATH` while the plugin is enabled. When it refuses, its second line is the next step: do that, then run it again. When that line hands the merge to the user, stop and tell them; don't merge any other way.

Before running it, if the user has not yet explained the PR in their own words in this session (what changed, why, and how it was verified), ask them to, once. If they say to skip it, merge. When they explain, check what they said against the diff and name anything wrong or missing, then let them choose: explain again, ask for a re-explanation (`/wait-what`, `/teach`, `/show-me`), or merge. Never refuse to merge over it.

After merging a feature branch into `develop`, delete that branch as part of the same workflow — both local (`git branch -d`) and remote. GitHub deletes the remote branch on merge where `delete_branch_on_merge` is on, so run `git push origin --delete <branch>` only when `git ls-remote --exit-code --heads origin <branch>` still finds it, then `git fetch --prune origin` to drop the stale `origin/<branch>`. Don't leave merged branches around and don't ask first; cleanup is the final step of any commit → push → merge request.

After merging a ticket's PR, in the same workflow as the branch cleanup, run `/lingling:spec-closeout`: it finds the ticket's spec and tells the user when every ticket under that spec is closed. It never closes the spec; close it only when the user says so. A PR that delivers a whole spec, opened by `/implement-spec` or at the end of a spec branch, is the exception: it closes the spec when it merges, and merging it is the user's call. A ticket with no parent needs nothing here.

Before any commit — including at the start of `/implement` — if the current branch is `develop`, first branch off the latest `develop` (`git fetch origin develop && git switch -c <name> origin/develop`, so the branch starts from `origin/develop`, not from a possibly stale local `develop`) and work there; never commit on `develop` directly. The agent picks the branch name, and the name must not contain digits. Upstream `implement` only says "commit to the current branch" and never opens a branch itself; this rule fills that gap.

A ticket cut from a spec (a sub-issue) is built on the spec's branch and gets no PR of its own. Before the first commit, read the spec's comments for a line `Spec branch: <name>`: if one exists, switch to that branch; if none does, branch off as above and post that line as a comment on the spec. Once the ticket's work is committed and pushed, comment on the ticket naming the branch and its commits, then close the ticket. Open the PR only when every ticket under the spec is closed: one PR for the whole spec, with `Closes #<spec>` in its body.

No merge commit may land inside a branch, and `lingling-merge` refuses a branch that holds one. A branch catches up with `develop` only by rebasing onto it (`git fetch origin && git rebase origin/develop`, then `git push --force-with-lease`), resolving any conflict during the rebase; one branch takes another's commits only by fast-forward (`git merge --ff-only`).

<!-- /lingling:git-workflow -->
