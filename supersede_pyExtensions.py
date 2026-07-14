from __future__ import annotations

import configparser
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "supersede_pyExtensions.ini"

DEFAULT_CONFIG = """\
[extensions]
# Only used when auto = false (see [options]).
# One line per extension: <name> = <source folder>
# <source folder> is what gets superseded, and the deployed zip is expected to
# be named "<source folder>.zip" in the copy_from folder below.
# <name> is only used to label the per-extension subfolder (see [options]).
pyMEP = pyMEP.extension
pyGherkin = pyGherkin.extension

[paths]
# Where superseded copies go (relative to this script, or absolute)
superseded = 00 - Superseded
# Downloads folder to pull the fresh <source>.zip files from.
copy_from = {downloads}

[options]
# If true, ignore the [extensions] list above and auto-discover: scan copy_from
# for "*.extension.zip" files and deploy each one whose matching source folder
# already exists next to this script. If false, only the [extensions] you list
# above are considered.
auto = true
# If true, each extension's superseded folder + archived zip go into their own
# subfolder inside 'superseded', named after the extension's <name>
# (e.g. superseded/pyMEP/pyMEP.extension_<timestamp>). If false, everything
# lands directly in 'superseded'.
subfolder_per_extension = true
"""


def load_or_create_config() -> configparser.ConfigParser:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(DEFAULT_CONFIG.replace(
            "{downloads}", str(Path.home() / "Downloads")))
        print(f"Created default config at {CONFIG_PATH}")
        print("Edit it and re-run.")
        sys.exit(0)
    cfg = configparser.ConfigParser()
    # Preserve the case of keys (extension names) instead of lowercasing them.
    cfg.optionxform = str
    cfg.read(CONFIG_PATH)
    return cfg


def resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else BASE / path


def timestamp_now() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def short_name(source_name: str) -> str:
    """'pyMEP.extension' -> 'pyMEP'. Anything without the suffix is unchanged."""
    suffix = ".extension"
    if source_name.endswith(suffix):
        return source_name[: -len(suffix)]
    return source_name


def discover_extensions(copy_from: Path):
    """Auto mode: scan copy_from for '*.extension.zip' files and return a list of
    (label, source_folder) pairs for those whose source folder already exists
    next to this script. Brand-new extensions (no existing folder) are skipped
    so this only ever updates things already deployed.

    Returns (pairs, skipped_new) where skipped_new lists source names found in
    Downloads but not present locally (reported, not processed).
    """
    pairs = []
    skipped_new = []
    seen = set()
    for zip_path in sorted(copy_from.glob("*.extension.zip")):
        # 'pyMEP.extension.zip' -> source folder 'pyMEP.extension'
        source_name = zip_path.name[: -len(".zip")]
        if source_name in seen:
            continue
        seen.add(source_name)
        if not (BASE / source_name).exists():
            skipped_new.append(source_name)
            continue
        label = short_name(source_name)
        pairs.append((label, source_name))
    return pairs, skipped_new


def unique_destination(parent: Path, stem: str, suffix: str) -> Path:
    """Return a non-clashing path at parent / <stem><suffix>, appending _N if taken."""
    dest = parent / f"{stem}{suffix}"
    counter = 1
    while dest.exists():
        dest = parent / f"{stem}_{counter}{suffix}"
        counter += 1
    return dest


def supersede(source: Path, superseded_dir: Path, ts: str) -> Path | None:
    if not source.exists():
        print(f"  No existing '{source.name}' to supersede, skipping move.")
        return None
    superseded_dir.mkdir(parents=True, exist_ok=True)

    destination = unique_destination(superseded_dir, f"{source.name}_{ts}", "")
    shutil.move(str(source), str(destination))
    print(f"  Superseded: {source.name} -> {destination.relative_to(BASE)}")
    return destination


def archive_zip(zip_path: Path, superseded_dir: Path, ts: str) -> Path:
    """Move the source zip into the superseded folder with a timestamp."""
    superseded_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_destination(superseded_dir, f"{zip_path.stem}_{ts}", zip_path.suffix)
    shutil.move(str(zip_path), str(destination))
    print(f"  Archived zip: {zip_path.name} -> {destination.relative_to(BASE)}")
    return destination


def extract_zip(zip_path: Path, source_name: str, dest: Path) -> None:
    """Extract <source>.zip into 'dest', handling both zip layouts:
    (a) a single top-level folder, or (b) loose files."""
    temp_dir = dest.parent / f".__extract_{source_name}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)

    print(f"  Extracting {zip_path.name}...")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(temp_dir)

    extracted = list(temp_dir.iterdir())
    if len(extracted) == 1 and extracted[0].is_dir():
        # Zip had a single top-level folder - move it into place
        shutil.move(str(extracted[0]), str(dest))
        shutil.rmtree(temp_dir)
    else:
        # Zip had loose files - rename the temp dir itself
        temp_dir.rename(dest)

    print(f"  Extracted -> {dest.relative_to(BASE)}")


def process_extension(name: str, source: Path, copy_from: Path,
                      superseded_root: Path, use_subfolder: bool,
                      ts: str) -> str:
    """Handle one extension. Returns a status string for the summary."""
    print(f"[{name}] source '{source.name}'")

    zip_path = copy_from / f"{source.name}.zip"
    if not zip_path.exists():
        print(f"  No '{source.name}.zip' in {copy_from} - skipped "
              "(nothing deployed, nothing superseded).")
        return "skipped (no download)"

    # Where the archived old copy + zip go for this extension.
    if use_subfolder:
        superseded_dir = superseded_root / short_name(source.name)
    else:
        superseded_dir = superseded_root

    # A fresh artefact is waiting: supersede the old folder, then extract the
    # zip into place, then archive the zip alongside the superseded folder.
    # If the extract fails (corrupt/wrong zip), the superseded copy is moved
    # back so a bad download never leaves you without a deployed extension.
    moved = supersede(source, superseded_dir, ts)
    try:
        extract_zip(zip_path, source.name, source)
    except Exception:
        try:
            if moved is not None and not source.exists():
                shutil.move(str(moved), str(source))
                print(f"  Extract failed - previous '{source.name}' restored.")
        except Exception as restore_err:
            print(f"  Restore after failed extract ALSO failed: {restore_err}"
                  f" - recover manually from {moved}")
        raise
    archive_zip(zip_path, superseded_dir, ts)
    return "deployed"


def main() -> None:
    cfg = load_or_create_config()

    superseded_root = resolve(cfg["paths"]["superseded"])
    copy_from_str = cfg["paths"].get("copy_from", "").strip()
    auto = cfg["options"].getboolean("auto", fallback=True)
    use_subfolder = cfg["options"].getboolean(
        "subfolder_per_extension", fallback=True)

    if not copy_from_str:
        print("copy_from is empty - nothing to deploy, nothing superseded.")
        return
    copy_from = Path(copy_from_str)
    if not copy_from.exists():
        print(f"copy_from path not found: {copy_from} - "
              "nothing to deploy, nothing superseded.")
        return

    # Build the list of (label, source folder) jobs from either auto-discovery
    # or the explicit [extensions] list.
    if auto:
        jobs, skipped_new = discover_extensions(copy_from)
        print(f"Auto mode: scanning {copy_from} for *.extension.zip")
        for source_name in skipped_new:
            print(f"  Found '{source_name}.zip' but no local '{source_name}' "
                  "folder - skipped (auto only updates existing extensions).")
        if not jobs:
            print("Nothing to deploy (no matching downloads for existing "
                  "extensions).")
            return
    else:
        if "extensions" not in cfg or not cfg["extensions"]:
            print("auto = false but no [extensions] listed - nothing to do.")
            return
        jobs = []
        for name, source_str in cfg["extensions"].items():
            source_str = source_str.strip()
            if not source_str:
                print(f"[{name}] no source folder set - skipped.")
                continue
            jobs.append((name, source_str))

    ts = timestamp_now()

    summary = []
    for name, source_str in jobs:
        source = resolve(source_str)
        try:
            status = process_extension(
                name, source, copy_from, superseded_root, use_subfolder, ts)
        except Exception as e:  # one bad extension must not stop the rest
            print(f"  Error processing '{name}': {e}")
            status = f"error: {e}"
        summary.append((name, status))

    print("\nSummary:")
    for name, status in summary:
        print(f"  {name}: {status}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
