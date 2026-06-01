#!/usr/bin/env python3
"""Generate listing.json for the Transparency47 Foreign Influence archive."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
LISTING_PATH = ROOT_DIR / "listing.json"


def stable_id(source: str, path: str) -> str:
    digest = hashlib.sha1(f"{source}:{path}".encode("utf-8")).hexdigest()[:16]
    return f"{source}:{digest}"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def first_heading(markdown: str) -> str | None:
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else None


def metadata_line(markdown: str, label: str) -> str | None:
    pattern = re.compile(rf"^-\s+{re.escape(label)}:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
    match = pattern.search(markdown)
    return match.group(1).strip() if match else None


def summary_from(markdown: str) -> str | None:
    lines = []
    for line in markdown.splitlines():
        if not line or line.startswith("#") or line.startswith("|") or line.startswith("- "):
            continue
        lines.append(line)
    text = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return text[:280] if text else None


def month_from_path(relative_path: str) -> str | None:
    parts = relative_path.split("/")
    if len(parts) >= 3 and parts[1].isdigit():
        return f"{parts[1]}-{parts[2]}"
    return None


def build_record(path: Path) -> dict:
    relative_path = path.relative_to(ROOT_DIR).as_posix()
    markdown = read_text(path)
    is_month = path.name == "README.md"
    pac = metadata_line(markdown, "PAC") or relative_path.split("/", 1)[0]
    month = metadata_line(markdown, "Month") or month_from_path(relative_path)
    title = first_heading(markdown) or path.stem.replace("_", " ")
    date = f"{month}-01" if month else None
    return {
        "id": stable_id("foreign_influence", relative_path),
        "title": title,
        "path": relative_path,
        "category": pac,
        "kind": "disclosure_month" if is_month else "disclosure",
        "date": date,
        "sourceUrl": "https://www.fec.gov/",
        "summary": summary_from(markdown),
        "metadata": {
            "pac": pac,
            "month": month,
            "candidateId": metadata_line(markdown, "Candidate ID"),
            "office": metadata_line(markdown, "Office"),
            "netAmount": metadata_line(markdown, "Net reported amount") or metadata_line(markdown, "Total net reported amount"),
            "transactionCount": metadata_line(markdown, "Transactions"),
            "dateAccessed": metadata_line(markdown, "Date accessed"),
        },
    }


def discover_records() -> list[Path]:
    records = []
    for path in ROOT_DIR.rglob("*.md"):
        relative = path.relative_to(ROOT_DIR).as_posix()
        if relative == "README.md" or relative.startswith("Scripts/"):
            continue
        records.append(path)
    return sorted(records, key=lambda p: p.relative_to(ROOT_DIR).as_posix())


def build_listing() -> dict:
    records = [build_record(path) for path in discover_records()]
    records.sort(key=lambda row: (row.get("date") or "", row.get("title") or ""), reverse=True)
    return {
        "version": 1,
        "source": "foreign_influence",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "records": records,
    }


def write_listing(path: Path = LISTING_PATH) -> None:
    listing = build_listing()
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(listing, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)
    print(f"Wrote {path.relative_to(ROOT_DIR)} with {len(listing['records'])} records.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Foreign Influence listing.json.")
    parser.parse_args()
    write_listing()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
