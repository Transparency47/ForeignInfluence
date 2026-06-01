#!/usr/bin/env python3
"""Import FEC bulk PAC records into the ForeignInfluence mirror."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import re
import shutil
import tempfile
import unicodedata
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CYCLES = (2016, 2018, 2020, 2022, 2024, 2026)
PAC_IDS = {
    "C00797670": "AIPAC",
}
FEC_BASE = "https://www.fec.gov/files/bulk-downloads"

PAS2_COLUMNS = [
    "committee_id",
    "amendment_indicator",
    "report_type",
    "transaction_pgi",
    "image_number",
    "transaction_type",
    "entity_type",
    "name",
    "city",
    "state",
    "zip_code",
    "employer",
    "occupation",
    "transaction_date",
    "transaction_amount",
    "other_id",
    "candidate_id",
    "transaction_id",
    "file_number",
    "memo_code",
    "memo_text",
    "sub_id",
]

CN_COLUMNS = [
    "candidate_id",
    "candidate_name",
    "party",
    "election_year",
    "state",
    "office",
    "district",
    "incumbent_challenge_status",
    "candidate_status",
    "principal_committee_id",
    "street_1",
    "street_2",
    "city",
    "state_address",
    "zip_code",
]

WEBK_COLUMNS = [
    "committee_id",
    "committee_name",
    "committee_type",
    "committee_designation",
    "filing_frequency",
    "total_receipts",
    "transfers_from_affiliates",
    "individual_contributions",
    "other_political_committee_contributions",
    "candidate_contributions",
    "candidate_loans",
    "total_loans_received",
    "total_disbursements",
    "transfers_to_affiliates",
    "individual_refunds",
    "political_committee_refunds",
    "candidate_loan_repayments",
    "loan_repayments",
    "cash_on_hand_beginning",
    "cash_on_hand_end",
    "debts_owed_by_committee",
    "debts_owed_to_committee",
    "contributions_to_candidates",
    "independent_expenditures",
    "party_coordinated_expenditures",
    "nonfed_shared_expenditures",
    "coverage_end_date",
]


@dataclass
class CandidateProfile:
    candidate_id: str
    display_name: str
    file_stem: str
    office: str = ""
    party: str = ""


@dataclass
class Bucket:
    pac: str
    year: str
    month: str
    candidate: CandidateProfile
    rows: list[dict[str, str]] = field(default_factory=list)

    def amounts(self) -> Iterable[Decimal]:
        for row in self.rows:
            yield parse_amount(row.get("transaction_amount", ""))

    @property
    def net(self) -> Decimal:
        return sum(self.amounts(), Decimal("0"))

    @property
    def positive(self) -> Decimal:
        return sum((amount for amount in self.amounts() if amount > 0), Decimal("0"))

    @property
    def negative(self) -> Decimal:
        return sum((amount for amount in self.amounts() if amount < 0), Decimal("0"))


def money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def parse_amount(value: str) -> Decimal:
    if not value:
        return Decimal("0")
    return Decimal(value.replace(",", ""))


def parse_fec_date(value: str) -> dt.date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%m%d%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def ascii_slug(value: str, fallback: str = "Unknown") -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9]+", "", normalized)
    return normalized or fallback


def clean_name_part(value: str) -> str:
    value = re.sub(r"\b(REP|SEN|JR|SR|DR|MR|MRS|MS)\.?\b", "", value or "", flags=re.I)
    return re.sub(r"\s+", " ", value).strip(" ,.")


def title_name(value: str) -> str:
    value = clean_name_part(value).title()
    for roman in ("Ii", "Iii", "Iv", "Vi", "Vii", "Viii", "Ix", "Xi"):
        value = re.sub(rf"\b{roman}\b", roman.upper(), value)
    return value


def display_from_fec_name(value: str) -> str:
    value = clean_name_part(value)
    if "," in value:
        last, rest = value.split(",", 1)
        return " ".join(part for part in (title_name(rest), title_name(last)) if part)
    return title_name(value)


def office_label(row: dict[str, str]) -> str:
    office = row.get("office", "")
    state = row.get("state", "")
    district = row.get("district", "")
    if office == "H":
        return f"House {state}-{district}" if state and district else "House"
    if office == "S":
        return f"Senate {state}" if state else "Senate"
    if office == "P":
        return "President"
    return " ".join(part for part in (office, state, district) if part)


def zip_url(kind: str, cycle: int) -> str:
    suffix = str(cycle)[-2:]
    if kind == "webk":
        return f"{FEC_BASE}/{cycle}/webk{suffix}.zip"
    if kind == "pas2":
        return f"{FEC_BASE}/{cycle}/pas2{suffix}.zip"
    if kind == "cn":
        return f"{FEC_BASE}/{cycle}/cn{suffix}.zip"
    raise ValueError(kind)


def download(url: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / url.rsplit("/", 1)[-1]
    if not out.exists():
        print(f"Downloading {url}", flush=True)
        urllib.request.urlretrieve(url, out)
    return out


def rows_from_zip(path: Path, columns: list[str]) -> Iterable[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if not names:
            return
        with archive.open(names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            reader = csv.reader(text, delimiter="|")
            for parts in reader:
                row = {column: parts[index] if index < len(parts) else "" for index, column in enumerate(columns)}
                yield row


def load_candidates(cycles: Iterable[int], cache_dir: Path) -> dict[str, CandidateProfile]:
    profiles: dict[str, CandidateProfile] = {}
    used: dict[str, str] = {}
    for cycle in cycles:
        path = download(zip_url("cn", cycle), cache_dir)
        for row in rows_from_zip(path, CN_COLUMNS):
            candidate_id = row.get("candidate_id", "")
            if not candidate_id or candidate_id in profiles:
                continue
            display = display_from_fec_name(row.get("candidate_name", "")) or f"Candidate {candidate_id}"
            stem = ascii_slug(display, candidate_id)
            if stem in used and used[stem] != candidate_id:
                stem = f"{stem}_{candidate_id}"
            used[stem] = candidate_id
            profiles[candidate_id] = CandidateProfile(
                candidate_id=candidate_id,
                display_name=display,
                file_stem=stem,
                office=office_label(row),
                party=row.get("party", ""),
            )
    return profiles


def fallback_profile(candidate_id: str, row: dict[str, str], used: dict[str, str]) -> CandidateProfile:
    display = display_from_fec_name(row.get("name", "")) or f"Candidate {candidate_id}"
    stem = ascii_slug(display, candidate_id)
    if stem in used and used[stem] != candidate_id:
        stem = f"{stem}_{candidate_id}"
    used[stem] = candidate_id
    return CandidateProfile(candidate_id=candidate_id, display_name=display, file_stem=stem)


def fec_image_url(image_number: str) -> str:
    return f"https://docquery.fec.gov/cgi-bin/fecimg/?{image_number}" if image_number else ""


def transaction_table(rows: list[dict[str, str]]) -> list[str]:
    lines = [
        "| Date | Amount | Transaction type | Recipient committee / payee | Election | FEC filing |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda item: (item.get("transaction_date", ""), item.get("sub_id", ""))):
        date = parse_fec_date(row.get("transaction_date", ""))
        date_text = date.isoformat() if date else row.get("transaction_date", "")
        amount = money(parse_amount(row.get("transaction_amount", "")))
        filing = fec_image_url(row.get("image_number", ""))
        filing_md = f"[image]({filing})" if filing else ""
        lines.append(
            "| "
            f"{date_text} | "
            f"{amount} | "
            f"{row.get('transaction_type', '')} | "
            f"{row.get('name', '')} | "
            f"{row.get('transaction_pgi', '')} | "
            f"{filing_md} |"
        )
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
        f"- Party: {candidate.party}",
        f"- Net reported amount: {money(bucket.net)}",
        f"- Positive reported amount: {money(bucket.positive)}",
        f"- Negative reported amount: {money(bucket.negative)}",
        f"- Transactions: {len(bucket.rows)}",
        "- Data source: FEC bulk PAS2 public records",
        f"- Date accessed: {accessed}",
        "",
        "## Transactions",
        "",
    ]
    lines.extend(transaction_table(bucket.rows))
    lines.append("")
    (out_dir / f"{candidate.file_stem}.md").write_text("\n".join(lines), encoding="utf-8")


def webk_summary_lines(summary: dict[str, str] | None) -> list[str]:
    if not summary:
        return []
    labels = [
        ("committee_name", "Committee name"),
        ("coverage_end_date", "Coverage end date"),
        ("total_receipts", "Total receipts"),
        ("total_disbursements", "Total disbursements"),
        ("contributions_to_candidates", "Contributions to candidates"),
        ("independent_expenditures", "Independent expenditures"),
        ("cash_on_hand_end", "Cash on hand"),
    ]
    lines = ["", "## Committee Summary From FEC WEBK", ""]
    for field, label in labels:
        value = summary.get(field, "")
        if field not in {"committee_name", "coverage_end_date"}:
            value = money(parse_amount(value))
        lines.append(f"- {label}: {value}")
    return lines


def write_month_readme(
    out_dir: Path,
    pac: str,
    year: str,
    month: str,
    buckets: list[Bucket],
    accessed: str,
    webk_summary: dict[str, str] | None,
) -> None:
    all_rows = [row for bucket in buckets for row in bucket.rows]
    amounts = [parse_amount(row.get("transaction_amount", "")) for row in all_rows]
    net = sum(amounts, Decimal("0"))
    positive = sum((amount for amount in amounts if amount > 0), Decimal("0"))
    negative = sum((amount for amount in amounts if amount < 0), Decimal("0"))
    lines = [
        f"# {pac} {year}-{month} Metadata",
        "",
        f"- PAC: {pac}",
        f"- Month: {year}-{month}",
        "- Data source: FEC bulk PAS2 public records",
        f"- Total net reported amount: {money(net)}",
        f"- Total positive reported amount: {money(positive)}",
        f"- Total negative reported amount: {money(negative)}",
        f"- Politicians listed: {len(buckets)}",
        f"- Transactions: {len(all_rows)}",
        f"- Date accessed: {accessed}",
    ]
    lines.extend(webk_summary_lines(webk_summary))
    lines.extend(
        [
            "",
            "## Politicians",
            "",
            "| Politician | Candidate ID | Office | Party | Net amount | Positive amount | Negative amount | Transactions | File |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for bucket in sorted(buckets, key=lambda item: (item.net, item.candidate.display_name), reverse=True):
        file_name = f"{bucket.candidate.file_stem}.md"
        lines.append(
            "| "
            f"{bucket.candidate.display_name} | "
            f"{bucket.candidate.candidate_id} | "
            f"{bucket.candidate.office} | "
            f"{bucket.candidate.party} | "
            f"{money(bucket.net)} | "
            f"{money(bucket.positive)} | "
            f"{money(bucket.negative)} | "
            f"{len(bucket.rows)} | "
            f"[{file_name}]({file_name}) |"
        )
    lines.append("")
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def load_webk(cycles: Iterable[int], cache_dir: Path) -> dict[tuple[str, str, str], dict[str, str]]:
    summaries: dict[tuple[str, str, str], dict[str, str]] = {}
    for cycle in cycles:
        path = download(zip_url("webk", cycle), cache_dir)
        for row in rows_from_zip(path, WEBK_COLUMNS):
            pac = PAC_IDS.get(row.get("committee_id", ""))
            if not pac:
                continue
            coverage = parse_fec_date(row.get("coverage_end_date", ""))
            if not coverage:
                continue
            summaries[(pac, f"{coverage.year:04d}", f"{coverage.month:02d}")] = row
    return summaries


def import_bulk(cycles: Iterable[int], cache_dir: Path, clean: bool = True) -> dict[str, int]:
    cycles = tuple(cycles)
    candidates = load_candidates(cycles, cache_dir)
    used_stems = {profile.file_stem: candidate_id for candidate_id, profile in candidates.items()}
    webk = load_webk(cycles, cache_dir)
    buckets: dict[tuple[str, str, str, str], Bucket] = {}
    skipped = 0
    for cycle in cycles:
        path = download(zip_url("pas2", cycle), cache_dir)
        for row in rows_from_zip(path, PAS2_COLUMNS):
            pac = PAC_IDS.get(row.get("committee_id", ""))
            candidate_id = row.get("candidate_id", "")
            date = parse_fec_date(row.get("transaction_date", ""))
            if not pac or not candidate_id or not date:
                skipped += 1
                continue
            candidate = candidates.get(candidate_id)
            if not candidate:
                candidate = fallback_profile(candidate_id, row, used_stems)
                candidates[candidate_id] = candidate
            key = (pac, f"{date.year:04d}", f"{date.month:02d}", candidate_id)
            buckets.setdefault(key, Bucket(pac=key[0], year=key[1], month=key[2], candidate=candidate)).rows.append(row)

    for pac in sorted(PAC_IDS.values()):
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
        write_month_readme(ROOT_DIR / pac / year / month, pac, year, month, month_buckets, accessed, webk.get((pac, year, month)))

    return {
        "cycles": len(cycles),
        "politicianMonths": len(buckets),
        "months": len(monthly),
        "transactions": sum(len(bucket.rows) for bucket in buckets.values()),
        "skippedRows": skipped,
    }


def parse_cycles(value: str) -> tuple[int, ...]:
    return tuple(int(part.strip()) for part in value.split(",") if part.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Import FEC bulk PAS2/WEBK data into monthly markdown files.")
    parser.add_argument("--cycles", default=",".join(str(cycle) for cycle in DEFAULT_CYCLES))
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "foreign_influence_fec")
    parser.add_argument("--no-clean", action="store_true", help="Do not remove existing PAC output before writing.")
    args = parser.parse_args()
    stats = import_bulk(parse_cycles(args.cycles), args.cache_dir, clean=not args.no_clean)
    for key, value in stats.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
