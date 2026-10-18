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
4. **Set up the instrument list** of a group in the left panel while its tab is open (manual entry or load preset)
5. **Configure naming format** at the bottom (e.g., `{Number}-{PieceName}-{Instrument}.pdf`)
6. **Preview & Rename** to execute

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
