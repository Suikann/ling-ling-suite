# Ling Ling Suite

**A template-driven PDF management tool designed for music librarians.**

[English](README.md) | [繁體中文](README.zh-TW.md)

[Homepage](https://suikann.github.io/ling-ling-suite/en/) | [Privacy Policy](https://suikann.github.io/ling-ling-suite/en/privacy.html)

## Overview

**Ling Ling Suite** is a desktop application built to streamline the post-processing of orchestral score parts. It handles batch renaming, PDF splitting, PDF rotation, and file organization --- all within a visual, project-based workflow.

## Core Features

### Template-Driven Renaming
Define filename structures using dynamic variables:
- **Example:** `{Number}-{PieceName}-{Instrument}.pdf` produces `01-Beethoven Sym.5-Flute.pdf`
- **Empty variables:** An empty variable is left out together with the `-`, `_` or spaces before it; without a movement name, `{PieceName}-{MovementName}-{Instrument}.pdf` produces `Beethoven Sym.5-Flute.pdf`.
- **Per-group override:** Each group can use a custom template that completely replaces the master template.

### Visual PDF Splitting
Split combined PDFs into individual instrument parts with a thumbnail-based interface:
- Click page thumbnails to mark split points
- Color-coded sections with instrument assignment
- Right-click or double-click to preview full-size pages

### PDF Rotation
Rotate pages with per-section angle control:
- Mark rotation segments (like split points)
- Assign different angles (0/90/180/270) to each segment
- Backup before overwrite with undo support

### PDF Compression
Shrink oversized scanned parts below 20MB (Tools > Compress PDF, or Compress Folder for a whole folder with its subfolders):
- Lossless first, then downsample to 300 and 200 dpi only as needed; black-and-white (1-bit) images are left alone
- Each result is checked for structure, image data and appearance; a broken result is discarded
- Before saving, compare each file: pick a spot on the page thumbnail and two magnifiers show the same patch of the original and the compressed page, or open it in your PDF reader. Keep or discard each one; nothing is saved until all are decided
- Saved beside the original folder as "<folder> (compressed)" with the same file names and subfolders; files already under 20MB are copied as is, and the originals are never modified

### Instrument Presets
Built-in instrument lists for common ensembles:
- Orchestra, Concert Band, String Orchestra
- Chinese Orchestra, Silk and Bamboo Ensemble
- Each group maintains its own independent instrument list

### Project Management
- Save/load project files (`.llproj`) with all settings preserved
- Recent projects quick access
- Score file designation per group (renamed as sequence number 00)
- Subfolder output organized by group

### Undo / Redo
- Undo rename, split, and rotate operations
- Redo support for rename operations (Ctrl+Z / Ctrl+Y)
- Automatic backup before destructive PDF rotation

### Internationalization
- Traditional Chinese (繁體中文) and English
- Live language switching without restart
- Template variables automatically converted between languages

## Download

Download the latest version from the **[Releases](../../releases)** page. No installation required:

- **Windows**: download `LingLingSuite-portable.zip`, extract and run
- **Linux**: download `LingLingSuite-x86_64.AppImage`, make it executable (`chmod +x LingLingSuite-x86_64.AppImage`) and run it; read "Linux Notes" below first

After upgrading, do not open project files saved by the new version with an older version: the older version does not understand the new fields and drops them when it saves. A newer version opens project files saved by an even newer one read-only, and cannot save them.

## Linux Notes

The AppImage supports x86_64 only and needs glibc 2.39 or later (Ubuntu 24.04 and later, Debian 13, Arch-based distributions, recent Fedora).

- **Menu shortcut and updates**: import the AppImage with [Gear Lever](https://github.com/mijorus/gearlever); it creates a menu shortcut and checks for new versions. Without Gear Lever, write `~/.local/share/applications/lingling-suite.desktop` yourself with the full path of the AppImage in `Exec=`; both the file name and `StartupWMClass` must be `lingling-suite` so the window manager matches the shortcut to the window:
  ```ini
  [Desktop Entry]
  Type=Application
  Name=Ling Ling Suite
  Name[zh_TW]=泠靈小工具
  Exec=/home/you/Applications/LingLingSuite-x86_64.AppImage
  Categories=Office;
  StartupWMClass=lingling-suite
  ```
- **Chinese fonts**: Ling Ling Suite uses the system fonts; without a Chinese font, Chinese text shows as boxes. Install one, for example `noto-fonts-cjk` (Arch-based) or `fonts-noto-cjk` (Debian, Ubuntu)
- **Remembering sign-ins**: sign-ins are stored in the system's Secret Service. GNOME and KDE usually provide one; on hand-assembled setups such as Hyprland, install and run a Secret Service yourself (for example `gnome-keyring`). Without one you can still sign in, but the sign-in status reads "This sign-in won't be remembered: no secure storage on this computer" and you sign in again the next time you open Ling Ling Suite
- **Without FUSE**: if the AppImage does not start and the error mentions FUSE, run it as `APPIMAGE_EXTRACT_AND_RUN=1 ./LingLingSuite-x86_64.AppImage`; it extracts itself to a temporary folder on every start, so it opens more slowly
- **Falling back to X11**: Ling Ling Suite uses Wayland when it is available. Running under X11 (or with `QT_QPA_PLATFORM=xcb`) needs the xcb libraries of Qt's xcb plugin, such as `libxcb-cursor` (`libxcb-cursor0` on Debian and Ubuntu); without them you get `Could not load the Qt platform plugin "xcb"`
- **System file dialogs**: to use your desktop's own open and save dialogs, install `xdg-desktop-portal-gtk` and start with `QT_QPA_PLATFORMTHEME=xdgdesktopportal` (for example `Exec=env QT_QPA_PLATFORMTHEME=xdgdesktopportal /full/path/LingLingSuite-x86_64.AppImage` in the `.desktop` file); otherwise Qt's built-in dialogs are used

## Quick Start

1. Launch `LingLingSuite.exe` (on Linux, run `LingLingSuite-x86_64.AppImage`)
2. **Import** PDF files or folders via the Import menu
3. **Organize groups** --- each group = one piece, or one of its movements
4. **Set up the voices** of a group in the instrumentation table on the left while its tab is open (manual entry or load preset); dragging a voice takes its part along, and unchecking "In Project" sets aside a part this project does not have; each voice's headcount (min, max, or "or more") and the group's duration are entered here too
5. **Configure naming format** at the bottom (e.g., `{Number}-{PieceName}-{Instrument}.pdf`)
6. **Preview & Rename** to execute

## Catalog

A group can be linked to a piece, edition and movement in the catalog (the club's Google spreadsheet plus Drive). Once linked, the catalog holds the instrumentation and duration:

- Pressing **Save** on the project also writes the group's changed voices, order, headcounts, duration and sections to the catalog; nothing is written until you save. Only changed cells are written. If someone else changed the same cell in the catalog, every change of this save is listed first, with the conflicts in red for you to choose a side
- If a group was linked to the wrong place, click "Relink…" next to the link above the instrumentation table to link it elsewhere (there is no plain unlink). If the previous place still has instrumentation this group wrote and nobody changed since, you are asked whether to revert it: choose "Revert" and the next save removes the voices it added and puts headcounts back; anything changed by someone else since is left alone, and the table shows who changed it and when
- When offline or the catalog can't be written, the project is still saved; the changes not written are marked "Unsynced" in the instrumentation table with the catalog's current value, and retried on the next save or when the project is opened
- Opening a project reads the instrumentation back from the catalog: headcount and duration changes made by others are applied. If the voice list changed, a bar at the top of the instrumentation table marks added, removed and moved voices; choose "Use Catalog's" (parts follow their voice names, unmatched parts move to Ungrouped) or "Use Project's (Write Back on Save)" (written to the catalog on save after you confirm). Until you choose, parts are paired and named by the project's list
- The section map is shared by the whole club: in the catalog window, click "Section Map" on the toolbar. Each section is a column; drag instruments to another column and save to write them to the catalog. You can switch between Chinese and Western orchestra, search instruments, and see how many pieces in the catalog use each instrument
- Deleting a voice in a linked group deletes it from the score; if this concert just lacks the part, uncheck "In Project" instead. A voice that already has a Drive file can't be deleted from the group; handle it in the catalog window
- The catalog window's "Instrumentation & Parts" page shows each voice as "Present" or "Missing" depending on whether a Drive file is registered for it. The right side lists the files in the edition folder that nothing registers: pick a file, pick a voice (or the score) on the left and press "Assign"; nothing is written to the catalog until you press "Save", and the same goes for instrumentation edits. Assigning to a voice that already has a file sends the old file to the Drive trash, where it can be restored within 30 days
- **Archive to Catalog** (Catalog menu): uploads the score and parts of renamed groups linked to the catalog into the edition's Drive folder and registers them to their voices, keeping the same subfolders as the local output location; the local copies stay. The preview lists whether each file will be uploaded, updated, moved or skipped; save the project first. Archiving a changed file again updates the same Drive file and Drive keeps the old version; if archiving stops halfway (offline or cancelled), finished files are kept and archiving again finishes the rest
- A file changed locally after archiving is shown in orange in the group panel as "Newer locally, not archived yet", and closing the project reminds you once; no connection is needed. Undoing a rename after archiving still works, but the catalog copies are not undone; archive again once they are ready
- **Clear Local Copies** (Catalog menu): once Drive has a copy with the same content (matching MD5), permanently deletes the local score and parts, along with folders left empty (up to the output location; the home folder and system folders such as Desktop, Documents and Downloads are kept). Nothing goes to the trash. The preview's group table shows how many files in each group can be cleared, differ from the catalog, are not archived or are not in the catalog, and how much space is freed; check the groups to clear. Clearing is unavailable offline, and the project must be saved first. Afterwards these files are marked "only in the catalog" in the group rather than missing, and renaming and archiving skip them; putting a file back where it was makes it normal again
- **Download from Catalog**: when a group has files only in the catalog, the group tab (and the note in the rename preview) shows "Download from Catalog", which puts them back at the path and name they had before clearing, recreating deleted folders; if the catalog copy was archived again after clearing, the newest copy comes back. A different file already at that location is never overwritten; it is listed instead. Each file is first downloaded to a temporary file and only put in place once its MD5 matches Drive, so an interrupted or mismatched download leaves no partial file; downloading can be cancelled, and pressing it again finishes the rest
- **Download to a folder of your choice**: on the catalog window's edition page, press "Download…", check some files or "Whole edition" (subfolders kept), and pick a folder; the download runs in the background, so you can print old parts on Linux without a Drive sync client. If the folder already has a file with the same name, identical content is skipped and different content gets a " (1)" suffix; nothing is overwritten, and every download is checked against its MD5
- **Revert Archive**: if you archived to the wrong edition or movement, open "Archive Log…" on the catalog window's edition page, pick that archive, press "Revert Archive…" and check the groups to revert. Files uploaded that time go to the Drive trash (recoverable for 30 days) and are unregistered; content they overwrote is restored while Drive still keeps the old version. Files archived again or changed on Drive since are left alone and listed with the reason. Groups whose local files were cleared are downloaded back and verified first; a group that cannot be downloaded back is not reverted. You can also revert the instrumentation the group wrote to that place before the archive, if nobody changed it since (added voices removed, headcounts restored); this is unchecked by default
- **After upgrading, do not open the catalog with an older version of Ling Ling**: its part status menu creates fake voices, its deletions shift row numbers, and its updates overwrite the first columns with old data

### If the spreadsheet gets corrupted

When rows were written to the wrong place or a sheet was deleted, restore it from Google Sheets' version history (neither Ling Ling nor the API can restore versions):

1. Open the catalog spreadsheet in a browser with the catalog account
2. Choose File → Version history → See version history
3. Pick a point in time before the problem on the right and check its content
4. Click "Restore this version"

Every change anyone made to the spreadsheet after that point is rolled back too, so check with the other score librarians first and redo the changes worth keeping afterwards.

## Distribute

When the club settings (the "社團設定" sheet in the catalog spreadsheet) enable HuaXia, **Distribute…** appears at the bottom and puts a group's voices, headcounts, parts and score on HuaXia, the Huaxia Chinese Orchestra Club's site. You sign in with an officer's Discord account; until HuaXia merges Ling Ling's distribution function it is unavailable, and pressing it explains why.

- Save the project first, and set the orchestra type to Chinese orchestra; groups not renamed yet or missing a minimum headcount are skipped this time, with the reason in the preview
- The preview has five steps: piece (pick an existing HuaXia piece or create one), voices (which HuaXia voice each maps to; differing headcounts and sections are overwritten by default), files (each replaces the copy Ling Ling put under the same voice last time, others' files stay; files over 20 MB are compressed first), concerts (which events to attach), and confirm
- After you press "Distribute" it runs in the background and the main window stays editable; what goes out is the files and instrumentation at that moment, so later changes need another distribution. The status bar has "Stop Distributing", the bottom of the main window logs each step, and groups that failed are listed with the reason at the end, with "Distribute Failed Groups Again" to redo only those
- If the connection drops or you stop halfway, members never see half a set of parts; distributing again picks up where it stopped, without uploading files again or creating the new piece twice; unregistered files an interruption leaves on HuaXia are removed the next time that piece is distributed
- Every distribution is recorded on the catalog's "發譜紀錄" sheet; if that can't be written, nothing is distributed
- **Retract**: if a group went to the wrong piece, open **Distribution Log** in the catalog window, select that distribution, press **Retract…** and check the groups. It removes the parts and full score that distribution put up, and the parts it created that nobody is assigned to; replaced files do not come back, and headcounts, sections and events are not changed back. Before retracting you see each part's result and which parts will be left without a score; a piece that distribution created is left empty for an officer to delete on the website

## Known Limitations

- **Paths are compared case-insensitively**: on every platform, paths that differ only in letter case count as the same file. On Linux, when one folder holds both `Flute.pdf` and `flute.pdf`, Ling Ling Suite treats them as one file; a move first checks whether the destination is a different file and refuses to overwrite it, so no file is damaged.
- **File names are cleaned by Windows rules**: Linux also replaces characters Windows does not allow (`< > : " / \ | ? *`) with underscores, because the files are uploaded to Drive and downloaded by Windows users.
- **Google sign-in slots**: the Google client bundled with Ling Ling Suite is an unverified production app, which allows at most 100 Google accounts to sign in over its lifetime, shared by all clubs. A club that signs in only with its catalog account takes one slot, so please do not try it out with a personal account. When the slots run out, accounts that have signed in before keep working, but new accounts cannot sign in until the maintainer ships a new version with a new client and everyone signs in again.

## Development

**Requirements:**
- Python 3.12+
- PySide6, PyPDF2, PyMuPDF, send2trash

**Run from source:**
```bash
git clone https://github.com/Suikann/ling-ling-suite.git
cd ling-ling-suite
pip install -r requirements.txt
python src/main.py
```

**Run tests:**
```bash
pip install pytest pytest-cov
python -m pytest tests/ -v
```

**Build portable EXE:**
```bash
build.bat
```

### Run from source on Linux

Install the packages into a `.venv` at the repo root (on Debian and Ubuntu, install `python3-venv` first):
```bash
git clone https://github.com/Suikann/ling-ling-suite.git
cd ling-ling-suite
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh
```

`run.sh` uses `.venv/bin/python` when `.venv` exists and `python3` otherwise; if packages are missing, it lists them and stops.

**Run tests:**
```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -v
```

**Build the folder version:**
```bash
./build.sh
```

`build.sh` picks Python the same way as `run.sh`, installs PyInstaller, runs the tests, and builds `dist/LingLingSuite/`.

**Build the AppImage:** after building the folder version, run `scripts/build_appimage.sh`. It downloads pinned versions of appimagetool and the runtime, verifies their checksums, and produces `dist/LingLingSuite-x86_64.AppImage` and its `.zsync`. An AppImage built locally requires the local glibc version; releases are always built by CD on `ubuntu-24.04`.

`LingLingSuite.spec` is the only set of build options; `build.bat`, `build.sh`, and CD all run it.

## CI/CD

- **CI:** Automated testing on push/PR (Python 3.12, Windows and Linux)
- **CD:** Pushing a `v*` tag builds the Windows `LingLingSuite-portable.zip` and the Linux `LingLingSuite-x86_64.AppImage` (with its `.zsync`), and puts them into one GitHub release once both are built; a manual run (workflow_dispatch) only uploads them as workflow artifacts and creates no release
  - Do not tick "Set as a pre-release": the update information embedded in the AppImage only follows the latest full release (`latest`), so Gear Lever would not see a pre-release
- **Pages:** `website/` holds the homepage and privacy policy (Traditional Chinese at the root, English under `en/`). When it changes on `develop`, `.github/workflows/pages.yml` publishes it to GitHub Pages at `https://suikann.github.io/ling-ling-suite/`. The repository's Pages source must be set to "GitHub Actions"
  - Switching to a subdomain: enter it under Settings > Pages > Custom domain in the repository and add a DNS CNAME record pointing to `suikann.github.io`; no file in the repository changes (Pages ignores CNAME files when publishing from GitHub Actions). Afterwards the `suikann.github.io/ling-ling-suite` URLs redirect to the subdomain, so the links above keep working; if the domain lapses, clearing Custom domain brings back the original URL. Either way, update the homepage and privacy policy URLs in the Google Console to the actual URLs
  - When the privacy policy changes, update both languages and the effective date on both pages

## License

See [LICENSE](LICENSE) for details.
