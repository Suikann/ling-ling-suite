# Ling Ling Suite

**A template-driven PDF management tool designed for music librarians.**

[English](README.md) | [繁體中文](README.zh-TW.md)

## Overview

**Ling Ling Suite** is a desktop application built to streamline the post-processing of orchestral score parts. It handles batch renaming, PDF splitting, PDF rotation, and file organization --- all within a visual, project-based workflow.

## Core Features

### Template-Driven Renaming
Define filename structures using dynamic variables:
- **Example:** `{Number}-{PieceName}-{Instrument}.pdf` produces `01-Beethoven Sym.5-Flute.pdf`
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

Download the latest portable EXE from the **[Releases](../../releases)** page. No installation required --- extract and run.

After upgrading, do not open project files saved by the new version with an older version: the older version does not understand the new fields and drops them when it saves. A newer version opens project files saved by an even newer one read-only, and cannot save them.

## Quick Start

1. Launch `LingLingSuite.exe`
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
- **After upgrading, do not open the catalog with an older version of Ling Ling**: its part status menu creates fake voices, its deletions shift row numbers, and its updates overwrite the first columns with old data

### If the spreadsheet gets corrupted

When rows were written to the wrong place or a sheet was deleted, restore it from Google Sheets' version history (neither Ling Ling nor the API can restore versions):

1. Open the catalog spreadsheet in a browser with the catalog account
2. Choose File → Version history → See version history
3. Pick a point in time before the problem on the right and check its content
4. Click "Restore this version"

Every change anyone made to the spreadsheet after that point is rolled back too, so check with the other score librarians first and redo the changes worth keeping afterwards.

## Known Limitations

- **Paths are compared case-insensitively**: on every platform, paths that differ only in letter case count as the same file. On Linux, when one folder holds both `Flute.pdf` and `flute.pdf`, Ling Ling Suite treats them as one file; a move first checks whether the destination is a different file and refuses to overwrite it, so no file is damaged.
- **File names are cleaned by Windows rules**: Linux also replaces characters Windows does not allow (`< > : " / \ | ? *`) with underscores, because the files are uploaded to Drive and downloaded by Windows users.

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

`LingLingSuite.spec` is the only set of build options; `build.bat`, `build.sh`, and CD all run it.

## CI/CD

- **CI:** Automated testing on push/PR (Python 3.12, Windows and Linux)
- **CD:** Push a `v*` tag to trigger portable EXE build and GitHub Release

## License

See [LICENSE](LICENSE) for details.
