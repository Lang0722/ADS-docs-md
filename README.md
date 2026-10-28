# ADS 2025 documentation to Markdown

This repository holds converters that make Markdown files for RAG from the Keysight ADS 2025 documentation:

| Source in the ADS install | Format | Converter |
|---|---|---|
| `C:\Program Files\Keysight\ADS2025\doc\ads` | MadCap Flare WebHelp2 help | `flare2md/flare2md.py` |
| `C:\Program Files\Keysight\ADS2025\<module>\python\docs\html` for `ael`, `ann`, `dataset`, `dds` and `de` | Sphinx Python API docs | `flare2md/sphinx2md.py` |

The repository does not hold the converted documentation. The ADS documentation is copyrighted by Keysight. Convert the files of your own ADS install (see [Run](#run)).

`METHODOLOGY.md` explains the method and the checks. `flare2md/README.md` gives the conversion rules and the options.

## Requirements

Python 3.10 or later. The converters need no install: their packages are pure-Python copies in `flare2md/_vendor/`. The check tools (`verify.py`, `images_csv.py`) also need `markdown-it-py`:

```sh
python3 -m pip install markdown-it-py==4.2.0
```

## Run

Copy the two source folders from the ADS install, for example to `~/ADS-docs-src` (the Flare help) and `~/ADS-docs-src-py` (the Sphinx builds, in the install layout). Then:

```sh
cd flare2md
python3 -m unittest discover -s tests
python3 flare2md.py ~/ADS-docs-src/Content/ads2025 ../ads2025 \
    --product "Keysight ADS" --version 2025 --source-prefix doc/ads
python3 verify.py ~/ADS-docs-src/Content/ads2025 ../ads2025

python3 sphinx2md.py ~/ADS-docs-src-py ../ads2025-python \
    --product "Keysight ADS" --version 2025
python3 verify.py ~/ADS-docs-src-py ../ads2025-python
```

Without `markdown-it-py`, the unit tests of the check tools are skipped. On ADS 2025, `verify.py` exits 0 on both outputs (no hard failures).

## Output layout

- `ads2025/<section>/<page>.md`: one file for each source `.html` page (7,401 files).
- `ads2025/docimages/`: the images that the pages refer to (12,433 files). Image links in the pages are relative, for example `../docimages/x.png`.
- `ads2025/_report/pages.csv`: statistics for each page (word recall, tables, code blocks, notes, images).
- `ads2025/_report/missing_images.txt`: images that the source pages refer to but that are not in the install.
- `ads2025-python/<module>/python/docs/html/<page>.md`: one file for each Sphinx topic page (158 files: de 86, dds 38, ann 16, ael 13, dataset 5). The layout is the install layout, so links between the builds work. The index, search and module index pages are not converted.

## YAML header of each page

```yaml
title: Set the Number of Harmonics
source: doc/ads/Content/ads2025/cktsimhb/Set_the_Number_of_Harmonics.html
breadcrumb:
- Home
- Simulation
- ...
section: cktsimhb
page_id: '861433733'
product: Keysight ADS
version: '2025'
```

The breadcrumb comes from the page's `data-mc-toc-path`. If a page has none, it comes from the help system's TOC tree (`Data/Tocs`).

## Python API pages (`ads2025-python`)

- 3,052 API objects (classes, methods, properties, functions). Each one is a heading with its full name, for example `` ### `keysight.ads.dds.Point.astuple` ``, and a `python` code block with its signature.
- 131 code blocks with their language (`python`, `pycon`, `shell`, `bat`, `ini`), 23 notes as block quotes, 4 tables, 10 images.
- The YAML header has `project` (for example `DDS Python Documentation`) and `page_id` (the Sphinx document name, for example `reference/dds/point`). `version` is `2025`, given on the command line.

## Contents of `ads2025`

- 3,997 simple tables are Markdown tables. 280 tables with merged cells, nested tables, code or headings in cells are clean HTML `<table>` blocks.
- 3,575 Note, Caution and Warning boxes are `> **Note:**` block quotes.
- 3,786 code panels are fenced code blocks. Code text is kept exactly.
- 293 equations are images with the alt text `equation`. The source has no LaTeX.

## Known source defects (not converter errors)

- 38 image references point to files that are not in the install (30 on the DDR4 compliance page).
- `cktsimddr/Setting_up_Basic_DDR4_Signal_Simulation_for_Compliance_Tests` is UTF-16 text behind a UTF-8 BOM, and 8 of its characters were already lost in the source. They show as `�`.
- Some examples are truncated in the source, for example `Is=1.5e-` on the BJT page.
- `appguide/Designing_Smart_Antenna` links to `4%2A4_Butler_Matrix.html`, but the page file is `4_4_Butler_Matrix.html`. The link stays broken, as in the source.

## Windows

An earlier version of `flare2md.py` (with the lxml parser and a `requirements.txt`) was tested on Windows 11 (ARM64) with the Python 3.12 that ADS installs (`C:\Program Files\Keysight\ADS2025\tools\python\python.exe`). All tests passed, `verify.py` exited 0, and all output files were byte-identical to the Mac output. The current version is not tested on Windows yet.
