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
- 讀寫使用者資料目錄（`APPDATA_DIR`）的程式碼：`docs/architecture/data-storage.md`

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
