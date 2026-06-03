# Foreign Influence PAC Tracker

This repository tracks large political action committees sole purpose is to advocate on behalf of a foreign nation. The records here are organized into a read-only public mirror using data pulled from [FEC.gov](https://www.fec.gov/) public campaign finance records.

The current dataset is organized by PAC, year, and month. Monthly folders contain one markdown file per politician, summarizing the FEC-reported transactions associated with that politician for that month. Each monthly folder also contains a `README.md` file with the monthly totals and a rollup of all politicians listed for that period, so GitHub displays the monthly summary automatically.

Daily FEC filing scans are folded into the same `PAC/YYYY/MM/PoliticianName.md` files and monthly `README.md` rollups as the bulk records.

Example layout:

```text
PACName/
  2026/
    05/
      ChuckSchumer.md
      README.md
```

## Data Source

The source data is FEC bulk data exported from FEC.gov, primarily `pas2` committee-to-candidate transaction files and `webk` committee summary files. Candidate names and office metadata are resolved from the FEC candidate master files. These are public records. This repository does not create or alter the underlying campaign finance reports; it restructures the public records into markdown for easier auditing, indexing, and long-term public access.

The tracker currently includes these FEC committees:

| Organization | FEC committee name | FEC committee ID | Focus |
| --- | --- | --- | --- |
| AIPAC PAC | American Israel Public Affairs Committee Political Action Committee | C00797670 | Israel |
| United Democracy Project | United Democracy Project | C00799031 | Israel |
| JStreetPAC | JStreetPAC | C00441949 | Israel (Two-State Solution) |
| NORPAC | NORPAC | C00247403 | Israel |
| DMFI PAC | Democratic Majority for Israel PAC | C00711341 | Israel |
| USINPAC | United States India Political Action Committee | C00381699 | India |
| Turkish Coalition | TC-USA PAC | C00434316 | Turkey |
| Armenian National | Armenian National Committee PAC | C00465591 | Armenia |
| Cuban American | Cuban American National Foundation PAC | C00155556 | Cuba |
| Iranian American | Iranian American Political Action Committee | C00386763 | Iran (Diaspora interests) |

Older FEC cycles are checked during import, but committees only appear in the repository for cycles where FEC bulk data contains matching records.

## Public Records And Takedown Notice

The material in this repository is derived from public campaign finance records published by the Federal Election Commission. These records are public records and are mirrored here for civic transparency, archival access, research, journalism, and public accountability.

Do not submit a DMCA takedown request for this repository unless you have a valid copyright claim in the specific material at issue. A takedown demand aimed at suppressing public FEC records, criticism, reporting, indexing, or factual data is improper. Knowingly making a false or bad-faith DMCA claim may create liability under 17 U.S.C. § 512(f). No private party, PAC, campaign, foreign principal, or government actor has a legitimate copyright basis to remove public FEC records from this mirror merely because the records are politically inconvenient.

## Reading The Amounts

The monthly `README.md` files include:

- Total net candidate-linked reported amount for the month
- Total positive candidate-linked reported amount
- Total negative candidate-linked reported amount, such as refunds or reversals
- Total transaction count
- A politician-by-politician dollar rollup

Individual politician files include the transaction-level detail available in the FEC bulk export, including source committee, date, date basis, amount, transaction type, election fields, and FEC filing image links where present. When FEC leaves a transaction date blank, the importer uses the filing image date so Super PAC independent-expenditure rows can still be placed into month folders. Candidate-linked amounts may include direct PAC contributions and Super PAC independent-expenditure rows; Super PAC spending is not money received directly by a candidate campaign.

## Automated Import

The GitHub workflow at `.github/workflows/import-fec-daily.yml` imports daily electronic and paper filings from the FEC daily bulk repositories. It runs automatically at 6am Eastern and can also be run manually with a date range or lookback window.

The GitHub workflow at `.github/workflows/import-fec-bulk.yml` is a manual catch-up workflow for cycle bulk files. It downloads FEC `webk`, `pas2`, and candidate master files, writes the PAC markdown tree, regenerates `listing.json`, verifies that monthly rollups are named `README.md`, checks for local path leaks, and commits generated changes back to `main`.

## Notes

This repository is part of Citizens for Government Transparency. It exists to preserve and make legible public records concerning PACs that advocate for foreign interests in American elections.

All data here should be treated as a public-record mirror, not as legal analysis. For authoritative campaign finance records, consult FEC.gov directly.
