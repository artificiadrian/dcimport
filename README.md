# dcimport

[![PyPI](https://img.shields.io/pypi/v/dcimport.svg)](https://pypi.org/project/dcimport/) ![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-blue) [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

`dcimport` imports photos and videos from an iPhone to a local folder over USB. It works without iCloud or iTunes, installs nothing on the device, and only ever reads from it.

Runs are incremental: a local SQLite database records what was copied, so re-runs fetch only new or changed files. Downloads run in parallel and are committed by atomic rename, so an interrupted run resumes with a plain re-run. Original mtimes are preserved.

Beyond a plain copy: filename/folder layout templates, `--since`/`--until` date filtering, HEIC-to-JPEG conversion, skipping the video half of Live Photos, and a JSON manifest per run.

![demo](.assets/demo.webp)

## Install

Requires Python 3.11+.

```sh
uv tool install dcimport            # or: pip install dcimport
uv tool install "dcimport[heic]"    # to enable --convert-heic
```

## Usage

```sh
dcimport ~/Pictures/iPhone
```

Connect the iPhone by USB, unlock it, and tap **Trust**. On Windows, [iTunes or the Apple Devices app](https://support.apple.com/en-us/HT210384) must be installed for the drivers.

```sh
dcimport ~/Pictures --layout "{mtime:%Y}/{mtime:%m}/{name}"   # sort into year/month folders
dcimport ~/Pictures --since 2024-01-01 --convert-heic         # recent photos, converted to JPEG
dcimport ~/Pictures --manifest run.json                       # also write a JSON report
```

Run `dcimport --help` for the full list of options.

## Layout

`--layout` is a template of `{name}` (original filename) and `{mtime:...}` (capture time, [strftime](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes) codes), and may contain `/` for subfolders. The default is `{mtime:%Y-%m-%d_%H-%M-%S}_{name}` → `2024-01-02_03-04-05_IMG_0001.JPG`. The layout is stored with the library on first use and reused on later runs; changing it requires `--force`.

## How it tracks files

Each copied file is recorded — by on-device path, size, and modification time — in `media.db` inside the output directory, and skipped on later runs. Editing a photo on the phone changes its mtime, so it re-imports as a new file; deleting `media.db` re-imports everything. Local files are never overwritten — name clashes get a `_1`, `_2`, … suffix.

## Development

```sh
git clone https://github.com/artificiadrian/dcimport.git
cd dcimport && uv sync --all-extras && uv run pytest
```
