#!/usr/bin/env python3
"""Import an FEC Schedule A CSV export into the ForeignInfluence mirror."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import shutil
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
PAC_ALIASES = {
    "AMERICAN ISRAEL PUBLIC AFFAIRS COMMITTEE POLITICAL ACTION COMMITTEE": "AIPAC",
}


def money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}${value:,.2f}"


def parse_amount(value: str) -> Decimal:
    if not value:
        return Decimal("0")
    return Decimal(value.replace(",", ""))


def parse_date(value: str) -> dt.date | None:
    if not value:
        return None
    return dt.datetime.strptime(value[:10], "%Y-%m-%d").date()


def ascii_slug(value: str, fallback: str = "Unknown") -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9]+", "", normalized)
    return normalized or fallback


def pac_name(committee_name: str) -> str:
    return PAC_ALIASES.get(committee_name.upper(), ascii_slug(committee_name, "PAC"))


def clean_name_part(value: str) -> str:
    value = re.sub(r"\b(REP|SEN|JR|SR|DR|MR|MRS|MS)\.?\b", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip(" ,.")


def title_name(value: str) -> str:
    value = clean_name_part(value).title()
    for roman in ("Ii", "Iii", "Iv", "Vi", "Vii", "Viii", "Ix", "Xi"):
        value = re.sub(rf"\b{roman}\b", roman.upper(), value)
    return value


def display_from_candidate_name(value: str) -> str:
    value = clean_name_part(value)
    if "," in value:
        last, rest = value.split(",", 1)
        parts = [title_name(part) for part in (rest, last)]
        return " ".join(part for part in parts if part)
    return title_name(value)


def candidate_display(row: dict[str, str]) -> str:
    first = title_name(row.get("candidate_first_name", ""))
    middle = title_name(row.get("candidate_middle_name", ""))
    last = title_name(row.get("candidate_last_name", ""))
    suffix = title_name(row.get("candidate_suffix", ""))
    name = " ".join(part for part in (first, middle, last, suffix) if part)
    if name:
        return name
    if row.get("candidate_name"):
        return display_from_candidate_name(row["candidate_name"])
    if row.get("candidate_id"):
        return f"Candidate {row['candidate_id']}"
    return ""


def candidate_office(row: dict[str, str]) -> str:
    office = row.get("candidate_office_full") or row.get("candidate_office") or ""
    state = row.get("candidate_office_state") or ""
    district = row.get("candidate_office_district") or ""
    if office.upper() == "HOUSE" and state and district:
        return f"House {state}-{district}"
    if office.upper() == "SENATE" and state:
        return f"Senate {state}"
    return " ".join(part for part in (office.title(), state, district) if part)


@dataclass
class CandidateProfile:
    candidate_id: str
    display_name: str
    file_stem: str
    office: str = ""


@dataclass
class Bucket:
    pac: str
    year: str
    month: str
    candidate: CandidateProfile
    rows: list[dict[str, str]] = field(default_factory=list)

    def amounts(self) -> Iterable[Decimal]:
        for row in self.rows:
            yield parse_amount(row.get("contribution_receipt_amount", ""))

    @property
    def net(self) -> Decimal:
        return sum(self.amounts(), Decimal("0"))

    @property
    def positive(self) -> Decimal:
        return sum((amount for amount in self.amounts() if amount > 0), Decimal("0"))

    @property
    def negative(self) -> Decimal:
        return sum((amount for amount in self.amounts() if amount < 0), Decimal("0"))


def build_profiles(rows: list[dict[str, str]]) -> dict[str, CandidateProfile]:
    profiles: dict[str, CandidateProfile] = {}
    used_stems: dict[str, str] = {}
    for row in rows:
        candidate_id = row.get("candidate_id", "")
        display_name = candidate_display(row)
        if not candidate_id or not display_name or display_name.startswith("Candidate ") or candidate_id in profiles:
            continue
        stem = ascii_slug(display_name, candidate_id)
        if stem in used_stems and used_stems[stem] != candidate_id:
            stem = f"{stem}_{candidate_id}"
        used_stems[stem] = candidate_id
        profiles[candidate_id] = CandidateProfile(
            candidate_id=candidate_id,
            display_name=display_name,
            file_stem=stem,
            office=candidate_office(row),
        )
    for row in rows:
        candidate_id = row.get("candidate_id", "")
        if not candidate_id or candidate_id in profiles:
            continue
        display_name = candidate_display(row) or f"Candidate {candidate_id}"
        stem = ascii_slug(display_name, candidate_id)
        if stem in used_stems and used_stems[stem] != candidate_id:
            stem = f"{stem}_{candidate_id}"
        used_stems[stem] = candidate_id
        profiles[candidate_id] = CandidateProfile(
            candidate_id=candidate_id,
            display_name=display_name,
            file_stem=stem,
            office=candidate_office(row),
        )
    return profiles


def transaction_table(rows: list[dict[str, str]]) -> list[str]:
    lines = [
        "| Date | Amount | Type | Contributor / Committee | Election | FEC filing |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda item: (item.get("contribution_receipt_date", ""), item.get("sub_id", ""))):
        date = (row.get("contribution_receipt_date") or "")[:10]
        amount = money(parse_amount(row.get("contribution_receipt_amount", "")))
        type_label = row.get("receipt_type_desc") or row.get("line_number_label") or row.get("receipt_type") or ""
        contributor = row.get("donor_committee_name") or row.get("contributor_name") or ""
        election = " ".join(part for part in (row.get("fec_election_type_desc", ""), row.get("fec_election_year", "")) if part)
        filing = row.get("pdf_url") or ""
        filing_md = f"[image]({filing})" if filing else ""
        lines.append(f"| {date} | {amount} | {type_label} | {contributor} | {election} | {filing_md} |")
    return lines


def write_candidate_file(out_dir: Path, bucket: Bucket, accessed: str) -> None:
    candidate = bucket.candidate
    lines = [
        f"# {candidate.display_name}",
        "",
        f"- PAC: {bucket.pac}",
        f"- Month: {bucket.year}-{bucket.month}",
        f"- Candidate ID: {candidate.candidate_id}",
        f"- Office: {candidate.office}",
        f"- Net reported amount: {money(bucket.net)}",
        f"- Positive reported amount: {money(bucket.positive)}",
        f"- Negative reported amount: {money(bucket.negative)}",
        f"- Transactions: {len(bucket.rows)}",
        "- Data source: FEC Schedule A public records",
        f"- Date accessed: {accessed}",
        "",
        "## Transactions",
        "",
    ]
    lines.extend(transaction_table(bucket.rows))
    lines.append("")
    (out_dir / f"{candidate.file_stem}.md").write_text("\n".join(lines), encoding="utf-8")


def write_month_readme(out_dir: Path, pac: str, year: str, month: str, buckets: list[Bucket], accessed: str) -> None:
    all_rows = [row for bucket in buckets for row in bucket.rows]
    amounts = [parse_amount(row.get("contribution_receipt_amount", "")) for row in all_rows]
    net = sum(amounts, Decimal("0"))
    positive = sum((amount for amount in amounts if amount > 0), Decimal("0"))
    negative = sum((amount for amount in amounts if amount < 0), Decimal("0"))
    lines = [
        f"# {pac} {year}-{month} Metadata",
        "",
        f"- PAC: {pac}",
        f"- Month: {year}-{month}",
        "- Data source: FEC Schedule A public records",
        f"- Total net reported amount: {money(net)}",
        f"- Total positive reported amount: {money(positive)}",
        f"- Total negative reported amount: {money(negative)}",
        f"- Politicians listed: {len(buckets)}",
        f"- Transactions: {len(all_rows)}",
        f"- Date accessed: {accessed}",
        "",
        "## Politicians",
        "",
        "| Politician | Candidate ID | Office | Net amount | Positive amount | Negative amount | Transactions | File |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for bucket in sorted(buckets, key=lambda item: (item.net, item.candidate.display_name), reverse=True):
        file_name = f"{bucket.candidate.file_stem}.md"
        lines.append(
            "| "
            f"{bucket.candidate.display_name} | "
            f"{bucket.candidate.candidate_id} | "
            f"{bucket.candidate.office} | "
            f"{money(bucket.net)} | "
            f"{money(bucket.positive)} | "
            f"{money(bucket.negative)} | "
            f"{len(bucket.rows)} | "
            f"[{file_name}]({file_name}) |"
        )
    lines.append("")
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def import_csv(csv_path: Path, clean: bool = True) -> dict[str, int]:
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    profiles = build_profiles(rows)
    buckets: dict[tuple[str, str, str, str], Bucket] = {}
    skipped = 0
    for row in rows:
        date = parse_date(row.get("contribution_receipt_date", ""))
        candidate_id = row.get("candidate_id", "")
        if not date or not candidate_id or candidate_id not in profiles:
            skipped += 1
            continue
        candidate = profiles[candidate_id]
        pac = pac_name(row.get("committee_name", ""))
        key = (pac, f"{date.year:04d}", f"{date.month:02d}", candidate_id)
        buckets.setdefault(key, Bucket(pac=key[0], year=key[1], month=key[2], candidate=candidate)).rows.append(row)

    for pac in sorted({key[0] for key in buckets}):
        pac_dir = ROOT_DIR / pac
        if clean and pac_dir.exists():
            shutil.rmtree(pac_dir)

    accessed = dt.datetime.now(dt.timezone.utc).isoformat()
    monthly: dict[tuple[str, str, str], list[Bucket]] = defaultdict(list)
    for (pac, year, month, _candidate_id), bucket in buckets.items():
        out_dir = ROOT_DIR / pac / year / month
        out_dir.mkdir(parents=True, exist_ok=True)
        write_candidate_file(out_dir, bucket, accessed)
        monthly[(pac, year, month)].append(bucket)

    for (pac, year, month), month_buckets in monthly.items():
        write_month_readme(ROOT_DIR / pac / year / month, pac, year, month, month_buckets, accessed)

    return {
        "rows": len(rows),
        "importedTransactions": sum(len(bucket.rows) for bucket in buckets.values()),
        "skippedRows": skipped,
        "politicianMonths": len(buckets),
        "months": len(monthly),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import FEC Schedule A CSV data into monthly markdown files.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--no-clean", action="store_true", help="Do not remove existing PAC output before writing.")
    args = parser.parse_args()
    stats = import_csv(args.csv_path, clean=not args.no_clean)
    for key, value in stats.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
