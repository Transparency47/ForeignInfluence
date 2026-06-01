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

from import_fec_bulk import (
    Bucket,
    CN_COLUMNS,
    COMMITTEE_NAMES,
    CandidateProfile,
    DEFAULT_CYCLES,
    fallback_profile,
    load_candidates,
    load_webk,
    rows_from_zip,
    write_candidate_file,
    write_month_readme,
    zip_url,
    download as download_bulk_file,
)


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


@dataclass
class DailyTransaction:
    folder: str
    committee_id: str
    candidate_id: str
    row: dict[str, str]


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


def load_principal_committees(cycles: Iterable[int], cache_dir: Path) -> dict[str, str]:
    principals: dict[str, str] = {}
    for cycle in cycles:
        path = download_bulk_file(zip_url("cn", cycle), cache_dir)
        for row in rows_from_zip(path, CN_COLUMNS):
            committee_id = row.get("principal_committee_id", "")
            candidate_id = row.get("candidate_id", "")
            if committee_id and candidate_id:
                principals.setdefault(committee_id, candidate_id)
    return principals


def field(record: list[str], index: int) -> str:
    return record[index] if index < len(record) else ""


def record_name(record: list[str]) -> str:
    organization = field(record, 6)
    if organization:
        return organization
    parts = [field(record, 8), field(record, 9), field(record, 7), field(record, 10), field(record, 11)]
    return " ".join(part for part in parts if part).strip()


def record_committee_ids(record: list[str]) -> set[str]:
    return {value for value in record if value in TRACKED_COMMITTEES}


def record_candidate_id(record: list[str], filing_candidate_ids: set[str], filer_committee_id: str, principal_committees: dict[str, str]) -> str:
    for value in record:
        if re.fullmatch(r"[HSP]\d[A-Z]{2}\d{5}", value):
            return value
    if filer_committee_id in principal_committees:
        return principal_committees[filer_committee_id]
    if len(filing_candidate_ids) == 1:
        return next(iter(filing_candidate_ids))
    return ""


def daily_image_number(day: dt.date, filing_id: str) -> str:
    return ""


def looks_like_date(value: str) -> bool:
    if not re.fullmatch(r"\d{8}", value or ""):
        return False
    try:
        dt.datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True


def looks_like_amount(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(?:\.\d{1,2})?", value or ""))


def transaction_date_amount(record: list[str]) -> tuple[str, str]:
    preferred_date = field(record, 20)
    preferred_amount = field(record, 21)
    if looks_like_date(preferred_date) and looks_like_amount(preferred_amount):
        return preferred_date, preferred_amount
    for index, value in enumerate(record):
        if not looks_like_date(value):
            continue
        for amount in record[index + 1 : index + 5]:
            if looks_like_amount(amount):
                return value, amount
    return "", ""


def metadata_value(markdown: str, label: str) -> str:
    match = re.search(rf"^-\s+{re.escape(label)}:\s*(.*?)\s*$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else ""


def parse_table_row(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def parse_money(value: str) -> str:
    cleaned = value.replace("$", "").replace(",", "").strip()
    if cleaned.startswith("-"):
        return cleaned
    return cleaned or "0"


def committee_id_for_name(name: str) -> str:
    for committee_id, committee_name in COMMITTEE_NAMES.items():
        if committee_name == name:
            return committee_id
    return ""


def existing_rows(path: Path, default_committee_id: str) -> list[dict[str, str]]:
    if not path.exists():
        return []
    markdown = path.read_text(encoding="utf-8", errors="replace")
    candidate_id = metadata_value(markdown, "Candidate ID")
    rows: list[dict[str, str]] = []
    in_table = False
    for line in markdown.splitlines():
        if line.startswith("| Date | Date basis | Source committee |"):
            in_table = True
            continue
        if not in_table or not line.startswith("|"):
            continue
        if line.startswith("| ---"):
            continue
        parts = parse_table_row(line)
        if len(parts) < 8:
            continue
        date_text, _date_basis, source_committee, amount, transaction_type, recipient, election, filing = parts[:8]
        try:
            date_value = dt.date.fromisoformat(date_text).strftime("%m%d%Y")
        except ValueError:
            date_value = dt.datetime.strptime(date_text, "%Y%m%d").strftime("%m%d%Y") if looks_like_date(date_text) else date_text
        image_match = re.search(r"\?\s*([0-9]+)", filing)
        committee_id = committee_id_for_name(source_committee) or default_committee_id
        rows.append(
            {
                "committee_id": committee_id,
                "transaction_pgi": election,
                "image_number": image_match.group(1) if image_match else "",
                "transaction_type": transaction_type,
                "entity_type": "",
                "name": recipient,
                "transaction_date": date_value,
                "transaction_amount": parse_money(amount),
                "other_id": "",
                "candidate_id": candidate_id,
                "transaction_id": "",
                "file_number": "",
                "memo_text": "",
                "sub_id": f"existing-{date_text}-{amount}-{transaction_type}-{recipient}-{election}",
            }
        )
    return rows


def first_heading(markdown: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else ""


def bucket_from_candidate_file(path: Path, folder: str, year: str, month: str, candidates: dict[str, CandidateProfile]) -> Bucket | None:
    markdown = path.read_text(encoding="utf-8", errors="replace")
    candidate_id = metadata_value(markdown, "Candidate ID")
    if not candidate_id:
        return None
    candidate = candidates.get(candidate_id)
    if not candidate:
        candidate = CandidateProfile(
            candidate_id=candidate_id,
            display_name=first_heading(markdown) or path.stem,
            file_stem=path.stem,
            office=metadata_value(markdown, "Office"),
            party=metadata_value(markdown, "Party"),
        )
        candidates[candidate_id] = candidate
    rows = existing_rows(path, "")
    if not rows:
        return None
    return Bucket(pac=folder, year=year, month=month, candidate=candidate, rows=rows)


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str, str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        key = (
            row.get("committee_id", ""),
            row.get("candidate_id", ""),
            row.get("transaction_date", ""),
            row.get("transaction_amount", ""),
            row.get("transaction_type", ""),
            row.get("name", ""),
            row.get("transaction_id", "") or row.get("sub_id", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def transaction_rows_from_file(
    kind: str,
    url: str,
    day: dt.date,
    name: str,
    records: list[list[str]],
    principal_committees: dict[str, str],
) -> list[DailyTransaction]:
    filing_id = filing_id_from_name(name)
    filer_committee_id, filer_committee_name = filer_info(records)
    filing_candidate_ids = candidate_ids(records)
    transactions: list[DailyTransaction] = []
    for record in records:
        if not record or not record[0].startswith(("SA", "SB", "SE", "F65")):
            continue
        committee_ids = record_committee_ids(record)
        if filer_committee_id in TRACKED_COMMITTEES:
            committee_ids.add(filer_committee_id)
        if not committee_ids:
            continue
        candidate_id = record_candidate_id(record, filing_candidate_ids, filer_committee_id, principal_committees)
        if not candidate_id:
            continue
        date_value, amount_value = transaction_date_amount(record)
        if not date_value or not amount_value:
            continue
        for committee_id in sorted(committee_ids):
            committee = TRACKED_COMMITTEES[committee_id]
            transactions.append(
                DailyTransaction(
                    folder=committee["folder"],
                    committee_id=committee_id,
                    candidate_id=candidate_id,
                    row={
                        "committee_id": committee_id,
                        "transaction_pgi": field(record, 18),
                        "image_number": daily_image_number(day, filing_id),
                        "transaction_type": record[0],
                        "entity_type": field(record, 5),
                        "name": filer_committee_name or record_name(record),
                        "transaction_date": dt.datetime.strptime(date_value, "%Y%m%d").strftime("%m%d%Y"),
                        "transaction_amount": amount_value,
                        "other_id": filer_committee_id,
                        "candidate_id": candidate_id,
                        "transaction_id": field(record, 2),
                        "file_number": filing_id,
                        "memo_text": f"FEC daily {kind} filing: {url}",
                        "sub_id": f"daily-{kind}-{filing_id}-{field(record, 2)}-{committee_id}",
                    },
                )
            )
    return transactions


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


def import_daily(start: dt.date, end: dt.date, cache_dir: Path) -> dict[str, int]:
    accessed = dt.datetime.now(dt.timezone.utc).isoformat()
    summaries: list[FilingSummary] = []
    transactions: list[DailyTransaction] = []
    downloaded = 0
    missing = 0
    nofiles = 0
    candidates = load_candidates(DEFAULT_CYCLES, cache_dir / "bulk")
    principal_committees = load_principal_committees(DEFAULT_CYCLES, cache_dir / "bulk")
    webk = load_webk(DEFAULT_CYCLES, cache_dir / "bulk")
    used_stems = {profile.file_stem: candidate_id for candidate_id, profile in candidates.items()}
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
                    summaries.extend(summarize_file(kind, url, day, member_name, records))
                    transactions.extend(transaction_rows_from_file(kind, url, day, member_name, records, principal_committees))
                break
    buckets: dict[tuple[str, str, str, str], Bucket] = {}
    for transaction in transactions:
        candidate = candidates.get(transaction.candidate_id)
        if not candidate:
            candidate = fallback_profile(transaction.candidate_id, transaction.row, used_stems)
            candidates[transaction.candidate_id] = candidate
        row_date = dt.datetime.strptime(transaction.row["transaction_date"], "%m%d%Y").date()
        key = (transaction.folder, f"{row_date.year:04d}", f"{row_date.month:02d}", transaction.candidate_id)
        buckets.setdefault(key, Bucket(pac=key[0], year=key[1], month=key[2], candidate=candidate)).rows.append(transaction.row)

    touched_months: set[tuple[str, str, str]] = set()
    for (folder, year, month, _candidate_id), bucket in buckets.items():
        out_dir = ROOT_DIR / folder / year / month
        out_dir.mkdir(parents=True, exist_ok=True)
        existing_path = out_dir / f"{bucket.candidate.file_stem}.md"
        bucket.rows = dedupe_rows(existing_rows(existing_path, bucket.rows[0].get("committee_id", "")) + bucket.rows)
        write_candidate_file(out_dir, bucket, accessed)
        touched_months.add((folder, year, month))

    for folder, year, month in touched_months:
        out_dir = ROOT_DIR / folder / year / month
        month_buckets = []
        for path in sorted(out_dir.glob("*.md")):
            if path.name == "README.md" or path.name.startswith("FEC_Filing_") or path.name == "Daily_Filings.md":
                continue
            bucket = bucket_from_candidate_file(path, folder, year, month, candidates)
            if bucket:
                month_buckets.append(bucket)
        write_month_readme(out_dir, folder, year, month, month_buckets, accessed, webk.get((folder, year, month)))

    return {
        "downloadedDailyFiles": downloaded,
        "missingDailyFiles": missing,
        "noFileMarkers": nofiles,
        "trackedFilings": len(summaries),
        "candidateTransactions": len(transactions),
        "politicianMonths": len(buckets),
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
