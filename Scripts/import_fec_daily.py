#!/usr/bin/env python3
"""Import daily FEC electronic and paper filings for tracked foreign-influence PACs."""

from __future__ import annotations

import argparse
import datetime as dt
import io
import re
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
FEC_DAILY_BASE = "https://cg-519a459a-0ea3-42c2-b7bc-fa1143481f74.s3-us-gov-west-1.amazonaws.com/bulk-downloads"

TRACKED_COMMITTEES = {
    "C00797670": {
        "folder": "AIPAC",
        "name": "American Israel Public Affairs Committee Political Action Committee",
        "country": "Israel",
    },
    "C00799031": {
        "folder": "UnitedDemocracyProject",
        "name": "United Democracy Project",
        "country": "Israel",
    },
    "C00441949": {
        "folder": "JStreetPAC",
        "name": "JStreetPAC",
        "country": "Israel (Two-State Solution)",
    },
    "C00247403": {
        "folder": "NORPAC",
        "name": "NORPAC",
        "country": "Israel",
    },
    "C00711341": {
        "folder": "DMFIPAC",
        "name": "Democratic Majority for Israel PAC",
        "country": "Israel",
    },
    "C00381699": {
        "folder": "USINPAC",
        "name": "United States India Political Action Committee",
        "country": "India",
    },
    "C00434316": {
        "folder": "TurkishCoalition",
        "name": "TC-USA PAC",
        "country": "Turkey",
    },
    "C00465591": {
        "folder": "ArmenianNational",
        "name": "Armenian National Committee PAC",
        "country": "Armenia",
    },
    "C00155556": {
        "folder": "CubanAmerican",
        "name": "Cuban American National Foundation PAC",
        "country": "Cuba",
    },
    "C00386763": {
        "folder": "IranianAmerican",
        "name": "Iranian American Political Action Committee",
        "country": "Iran (Diaspora interests)",
    },
}


@dataclass
class FilingSummary:
    folder: str
    committee_id: str
    committee_name: str
    country: str
    filer_committee_id: str
    filer_committee_name: str
    filing_id: str
    source_kind: str
    source_url: str
    source_file: str
    filing_date: dt.date
    forms: set[str] = field(default_factory=set)
    candidate_ids: set[str] = field(default_factory=set)
    transaction_count: int = 0
    record_count: int = 0

    @property
    def relation(self) -> str:
        return "filer" if self.filer_committee_id == self.committee_id else "mentioned"


def source_url(kind: str, file_name: str) -> str:
    return f"{FEC_DAILY_BASE}/{kind}/{file_name}"


def download(url: str, cache_dir: Path) -> Path | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / url.rsplit("/", 1)[-1]
    if out.exists():
        return out
    try:
        urllib.request.urlretrieve(url, out)
        return out
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def date_range(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    current = start
    while current <= end:
        yield current
        current += dt.timedelta(days=1)


def file_names_for(kind: str, day: dt.date) -> list[str]:
    date_part = day.strftime("%Y%m%d")
    if kind == "paper":
        return [f"{date_part}.zip", f"{date_part}.nofiles.zip"]
    return [f"{date_part}.zip"]


def records_from_text(text: str) -> list[list[str]]:
    records: list[list[str]] = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        delimiter = "\x1c" if "\x1c" in line else "|"
        parts = [part.strip() for part in line.split(delimiter)]
        if parts and parts[-1] == "":
            parts.pop()
        if parts:
            records.append(parts)
    return records


def read_zip_records(path: Path) -> Iterable[tuple[str, list[list[str]]]]:
    if path.stat().st_size <= 1:
        return
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            with archive.open(name) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace").read()
            yield name, records_from_text(text)


def filing_id_from_name(name: str) -> str:
    return Path(name).stem


def candidate_ids(records: list[list[str]]) -> set[str]:
    ids: set[str] = set()
    for record in records:
        for field in record:
            if re.fullmatch(r"[HSP]\d[A-Z]{2}\d{5}", field):
                ids.add(field)
    return ids


def filer_info(records: list[list[str]]) -> tuple[str, str]:
    for record in records:
        if not record or record[0] == "HDR":
            continue
        committee_id = record[1] if len(record) > 1 and re.fullmatch(r"C\d{8}", record[1]) else ""
        committee_name = record[2] if len(record) > 2 else ""
        if committee_id:
            return committee_id, committee_name
    return "", ""


def summarize_file(kind: str, url: str, day: dt.date, name: str, records: list[list[str]]) -> list[FilingSummary]:
    mentioned = {
        field
        for record in records[:25]
        for field in record
        if field in TRACKED_COMMITTEES
    }
    if not mentioned:
        mentioned = {
            field
            for record in records
            for field in record
            if field in TRACKED_COMMITTEES
        }
    summaries: list[FilingSummary] = []
    forms = {record[0] for record in records if record}
    candidates = candidate_ids(records)
    filer_committee_id, filer_committee_name = filer_info(records)
    transaction_count = sum(1 for record in records if record and record[0].startswith(("SA", "SB", "SE", "F65")))
    for committee_id in sorted(mentioned):
        committee = TRACKED_COMMITTEES[committee_id]
        summaries.append(
            FilingSummary(
                folder=committee["folder"],
                committee_id=committee_id,
                committee_name=committee["name"],
                country=committee["country"],
                filer_committee_id=filer_committee_id,
                filer_committee_name=filer_committee_name,
                filing_id=filing_id_from_name(name),
                source_kind=kind,
                source_url=url,
                source_file=name,
                filing_date=day,
                forms=forms,
                candidate_ids=candidates,
                transaction_count=transaction_count,
                record_count=len(records),
            )
        )
    return summaries


def write_filing(summary: FilingSummary, accessed: str) -> Path:
    out_dir = ROOT_DIR / summary.folder / f"{summary.filing_date.year:04d}" / f"{summary.filing_date.month:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"FEC_Filing_{summary.filing_id}.md"
    title_action = "Filed By" if summary.relation == "filer" else "Mentioning"
    lines = [
        f"# FEC Filing {summary.filing_id} {title_action} {summary.committee_name}",
        "",
        f"- PAC folder: {summary.folder}",
        f"- Committee ID: {summary.committee_id}",
        f"- Committee name: {summary.committee_name}",
        f"- Relationship to tracked committee: {summary.relation}",
        f"- Filer committee ID: {summary.filer_committee_id}",
        f"- Filer committee name: {summary.filer_committee_name}",
        f"- Foreign nation focus: {summary.country}",
        f"- Filing date: {summary.filing_date.isoformat()}",
        f"- Source type: {summary.source_kind}",
        f"- Source daily file: {summary.source_file}",
        f"- Source URL: {summary.source_url}",
        f"- FEC forms seen: {', '.join(sorted(summary.forms))}",
        f"- Candidate IDs mentioned: {', '.join(sorted(summary.candidate_ids)) if summary.candidate_ids else 'None detected'}",
        f"- Transaction-like records detected: {summary.transaction_count}",
        f"- Total records: {summary.record_count}",
        f"- Date accessed: {accessed}",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def write_daily_readmes(summaries: list[FilingSummary], accessed: str) -> None:
    grouped: dict[tuple[str, str, str], list[FilingSummary]] = defaultdict(list)
    for summary in summaries:
        grouped[(summary.folder, f"{summary.filing_date.year:04d}", f"{summary.filing_date.month:02d}")].append(summary)
    for (folder, year, month), month_summaries in grouped.items():
        out_dir = ROOT_DIR / folder / year / month
        lines = [
            f"# {folder} Daily FEC Filings {year}-{month}",
            "",
            f"- PAC folder: {folder}",
            f"- Month: {year}-{month}",
            f"- Filings: {len(month_summaries)}",
            f"- Date accessed: {accessed}",
            "",
            "| Filing date | Filing | Relationship | Committee ID | Committee name | Filer | Source type | Forms | Candidate IDs | File |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for summary in sorted(month_summaries, key=lambda item: (item.filing_date, item.committee_id, item.filing_id)):
            file_name = f"FEC_Filing_{summary.filing_id}.md"
            lines.append(
                "| "
                f"{summary.filing_date.isoformat()} | "
                f"{summary.filing_id} | "
                f"{summary.relation} | "
                f"{summary.committee_id} | "
                f"{summary.committee_name} | "
                f"{summary.filer_committee_id} {summary.filer_committee_name} | "
                f"{summary.source_kind} | "
                f"{', '.join(sorted(summary.forms))} | "
                f"{', '.join(sorted(summary.candidate_ids)) if summary.candidate_ids else ''} | "
                f"[{file_name}]({file_name}) |"
            )
        lines.append("")
        (out_dir / "Daily_Filings.md").write_text("\n".join(lines), encoding="utf-8")


def import_daily(start: dt.date, end: dt.date, cache_dir: Path) -> dict[str, int]:
    accessed = dt.datetime.now(dt.timezone.utc).isoformat()
    summaries: list[FilingSummary] = []
    downloaded = 0
    missing = 0
    nofiles = 0
    for day in date_range(start, end):
        for kind in ("electronic", "paper"):
            for file_name in file_names_for(kind, day):
                url = source_url(kind, file_name)
                path = download(url, cache_dir / kind)
                if not path:
                    missing += 1
                    continue
                downloaded += 1
                if ".nofiles." in file_name or path.stat().st_size <= 1:
                    nofiles += 1
                    continue
                for member_name, records in read_zip_records(path):
                    for summary in summarize_file(kind, url, day, member_name, records):
                        write_filing(summary, accessed)
                        summaries.append(summary)
                break
    write_daily_readmes(summaries, accessed)
    return {
        "downloadedDailyFiles": downloaded,
        "missingDailyFiles": missing,
        "noFileMarkers": nofiles,
        "trackedFilings": len(summaries),
    }


def parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import daily FEC electronic and paper filings for tracked PACs.")
    parser.add_argument("--date", type=parse_date, help="Import one filing date in YYYY-MM-DD format.")
    parser.add_argument("--start-date", type=parse_date)
    parser.add_argument("--end-date", type=parse_date)
    parser.add_argument("--lookback-days", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "foreign_influence_fec_daily")
    args = parser.parse_args()

    today = dt.datetime.now(dt.timezone.utc).date()
    if args.date:
        start = end = args.date
    elif args.start_date or args.end_date:
        end = args.end_date or today
        start = args.start_date or end
    else:
        end = today
        start = today - dt.timedelta(days=max(args.lookback_days - 1, 0))

    stats = import_daily(start, end, args.cache_dir)
    for key, value in stats.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
