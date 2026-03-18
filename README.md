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

## Quick Start

1. Launch `LingLingSuite.exe`
2. **Import** PDF files or folders via the Import menu
3. **Set up instruments** in the left panel (manual entry or load preset)
4. **Organize groups** --- each group = one movement/piece
5. **Configure naming format** at the bottom (e.g., `{Number}-{PieceName}-{Instrument}.pdf`)
6. **Preview & Rename** to execute

## Development

**Requirements:**
- Python 3.11+
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

## CI/CD

- **CI:** Automated testing on push/PR (Python 3.11 + 3.12, Windows)
- **CD:** Push a `v*` tag to trigger portable EXE build and GitHub Release

## License

See [LICENSE](LICENSE) for details.
