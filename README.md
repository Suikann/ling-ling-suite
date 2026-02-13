# Ling Ling Suite

**A template-driven file organization tool designed for music librarians.**

[English](README.md) | [繁體中文](README.zh-TW.md)

## Overview

**Ling Ling Suite** is a desktop application built to streamline the post-processing of split score parts.

When exporting parts from notation software (e.g., Sibelius, Finale, Dorico), the resulting files often have cluttered, inconsistent filenames (e.g., `Symphony_No5_vFinal_Part_01_Flute.pdf`).

This tool moves beyond simple batch renaming by introducing a **Logic-Based Template System**. It allows users to define specific naming structures for different pieces within the same project, ensuring every file in your library adheres to a strict standard.

## Core Features

### 1. Template-Driven Renaming
Define your own filename structures using dynamic variables.
* **Example Template:** `{Number}. {Instrument} ({Title}).pdf`
* **Result:** `01. Flute (Beethoven Sym.5).pdf`

### 2. Dual-Layer Logic (Master vs. Specific)
The application handles complex concert programs where different pieces require different naming conventions.

* **Master Template (Global):**
    Sets a default rule for all files in the working directory.
    * *Use case:* General rehearsal parts.
    
* **Track-Specific Templates (Override):**
    You can select specific groups of files and apply a unique "Small Template" to them.
    * **Independent Override:** When a track-specific template is applied, it completely ignores the Master Template for those files.
    * *Use case:* A concert program containing a Symphony (Template A) and a Concerto (Template B).

## Download & Usage

1.  Download the latest `LingLingSuite.exe` from the **[Releases](../../releases)** page.
2.  Launch the application.
3.  **Import:** Select the folder containing your raw split parts.
4.  **Configure Master Template:** Set the default naming rule (e.g., `[Orch] {Instrument}`).
5.  **Apply Specific Templates:** (Optional) Select a subset of files (e.g., for a specific piece) and apply a different template.
6.  **Run:** Click "Process Files" to rename everything in one go.

## Development

**Requirements:**
* Python 3.8+
* tkinter

**Build from Source:**
```bash
git clone [https://github.com/yourusername/ling-ling-suite.git](https://github.com/yourusername/ling-ling-suite.git)
cd ling-ling-suite
python src/main.py
