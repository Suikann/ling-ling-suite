# CLAUDE.md

## Role

批判性地解讀用戶的發言，找出問題的核心；用戶的說法與程式碼或事實不符時，直接指出並附上根據。

## Language

- 一律使用中華民國慣用的繁體中文
- 禁止使用 emoji
- 中文內容使用全型標點符號

## Workflow

- 改完程式碼就 commit，不必等用戶開口：以路徑逐一 `git add` 這次改過的檔案；訊息格式由 `.githooks/commit-msg` 定義並檢查，裝有 lingling plugin 時 `/lingling:git-commit` 說明寫法

## Agent skills

### Issue tracker

issue 與 spec 都在 GitHub Issues（`Suikann/ling-ling-suite`），一律用 `gh` CLI 操作。見 `docs/agents/issue-tracker.md`。

### Triage labels

五個標準 triage 角色對映到中文標籤（`待分類`、`待補資訊`、`待派工`、`待人工`、`不處理`）。見 `docs/agents/triage-labels.md`。

### Domain docs

單一 context：根目錄 `GLOSSARY.md` 加 `docs/adr/`。見 `docs/agents/domain.md`。

---

## Project Overview

泠靈小工具（Ling Ling Suite）是 Python + PySide6 的桌面程式，給樂團譜務分割合併譜、依樂器表排序分譜，再套用命名格式批次重新命名；功能介紹見 `README.zh-TW.md`。領域名詞以 `GLOSSARY.md` 為準：文件與程式碼沿用它的用詞，不用它列為 _Avoid_ 的同義詞。

## Commands

```bash
python src/main.py              # 啟動程式
python -m pytest tests/ -v      # 執行測試
```

## Reference docs

`docs/architecture/` 下每份文件是該區行為的唯一來源（何時跟著程式碼更新，見 `CODING_STANDARDS.md` 的完成條件）。工作涉及下列程式碼或主題時，先讀對應文件（列出的檔名在 `src/` 下都只有一個）：

- 寫或改 `src/`、`tests/` 的程式碼：`CODING_STANDARDS.md`
- 某件事全程式該經過哪個模組、寫測試：`docs/architecture/modules.md`
- `naming.py`、`template_engine.py`、`TEMPLATE_VARIABLES`、`rename_service.py`、`drive_rename_service.py`，或命名格式、模板變數、子資料夾輸出：`docs/architecture/naming.md`
- `models.py`、`project_access.py`、`project_service.py`、`import_service.py`、`preferences_service.py`，或專案編輯、開啟與存檔、匯入：`docs/architecture/project.md`
- `move_history.py`、`move_service.py`、`preview_dialog.py`、`MainWindow.prompt_pending_recovery`，或預檢、復原／重做、中斷還原：`docs/architecture/move-history.md`
- `split_service.py`、`workspace_service.py`、`split_dialog.py`、`workspace_dialog.py`，或分割、工作區：`docs/architecture/workspace.md`
- `publish_service.py`、`publish_runner.py`、`orphan_cleanup.py`、`retract_service.py`、`publish_run.py`、`publish_retract_dialog.py`，或發譜、撤回、收回：`docs/architecture/publish.md`
- 讀寫使用者資料目錄（`APPDATA_DIR`）的程式碼：`docs/architecture/data-storage.md`

<!-- lingling:git-workflow trunk=develop guarded=main -->

<!-- Git 流程的其餘規則在動作當下提示，不常駐在這裡（ADR-0043）：在 Guarded branch 上提交或推送由 git 機關擋下並印下一步；合併一律經 `lingling-merge <n>`（`gh pr merge` 被 deny-dangerous-bash 擋下時指過去），它驗跟上 develop、沒有 merge commit、CI 全綠與會意關卡，拒絕時第二行是下一步，合併後印刪分支的指令。起端標記給 git 機關讀 Trunk 與 Guarded branch。重跑 /lingling:setup-lingling 第 2 步會原地重寫這一段。 -->

## Git workflow

Name branches without digits: the pre-commit hook refuses commits on a branch whose name has one.

A ticket cut from a spec (a sub-issue) is built on the spec's branch and gets no PR of its own. Before the first commit, read the spec's comments for a line `Spec branch: <name>`: switch to that branch, or when none exists, create it with `git fetch origin develop && git switch -c <name> origin/develop` and post that line as a comment on the spec. Once the ticket's work is committed and pushed, comment on the ticket naming the branch and its commits, then close the ticket. When every ticket under the spec is closed, open one PR for the whole spec with `Closes #<spec>` in its body.

<!-- /lingling:git-workflow -->
