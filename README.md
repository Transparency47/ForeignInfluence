# Foreign Influence PAC Tracker

This repository tracks large political action committees whose sole purpose is to advocate on behalf of a foreign nation. The records here are organized into a read-only public mirror using data pulled from [FEC.gov](https://www.fec.gov/) public campaign finance records.

The current dataset is organized by PAC, year, and month. Monthly folders contain one markdown file per politician, summarizing the FEC-reported transactions associated with that politician for that month. Each monthly folder also contains a `README.md` file with the monthly totals and a rollup of all politicians listed for that period, so GitHub displays the monthly summary automatically.

Example layout:

```text
AIPAC/
  2026/
    05/
      ChuckSchumer.md
      README.md
```

## Data Source

The source data is FEC Schedule A itemized receipt data exported from FEC.gov. These are public records. This repository does not create or alter the underlying campaign finance reports; it restructures the public records into markdown for easier auditing, indexing, and long-term public access.

## Public Records And Takedown Notice

The material in this repository is derived from public campaign finance records published by the Federal Election Commission. These records are public records and are mirrored here for civic transparency, archival access, research, journalism, and public accountability.

Do not submit a DMCA takedown request for this repository unless you have a valid copyright claim in the specific material at issue. A takedown demand aimed at suppressing public FEC records, criticism, reporting, indexing, or factual data is improper. Knowingly making a false or bad-faith DMCA claim may create liability under 17 U.S.C. § 512(f). No private party, PAC, campaign, foreign principal, or government actor has a legitimate copyright basis to remove public FEC records from this mirror merely because the records are politically inconvenient.

## Reading The Amounts

The monthly `README.md` files include:

- Total net reported amount for the month
- Total positive reported amount
- Total negative reported amount, such as refunds or reversals
- Total transaction count
- A politician-by-politician dollar rollup

Individual politician files include the transaction-level detail available in the FEC export, including transaction date, amount, transaction type, election fields, and FEC filing image links where present.

## Notes

This repository is part of the Restoring American Sovereignty Project. It exists to preserve and make legible public records concerning PACs that advocate for foreign interests in American elections.

All data here should be treated as a public-record mirror, not as legal analysis. For authoritative campaign finance records, consult FEC.gov directly.
