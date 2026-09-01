# U.S. House floor source review

Review date: 2026-08-28  
Reviewer: project source-access review  
Registry source: `house_floor`

## Approved MVP methods

| Material | Exact official endpoint/pattern | Decision |
|---|---|---|
| Current/daily floor activity | `https://clerk.house.gov/FloorSummary` and linked `https://clerk.house.gov/floor/{YYYYMMDD}.xml` | Approved discovery/document input |
| Bulk floor proceedings | `https://clerk.house.gov/floor/HDoc-{congress}-{session}-FloorProceedings.xml` | Approved archive metadata/document input |
| Roll-call vote | `https://clerk.house.gov/evs/{year}/roll{NNN}.xml` as linked/referenced by floor activity | Approved vote-record input |
| Weekly schedule/bill package | `https://docs.house.gov/floor/` and `https://docs.house.gov/BillsThisWeek-RSS.xml` | Approved schedule/document input |
| Bill/amendment text | Official links from `docs.house.gov`, Clerk XML, or documented Congress.gov/API records | Approved document candidate; each host/method remains allowlisted |
| House floor player | `https://live.house.gov/` | Approved availability/public provenance page only |
| Live/archived player media | House player backend and alternate YouTube stream | **Not approved for automated media collection** |

## Access basis and limits

- The Clerk Floor Summary explicitly presents “Download this Day” and “Download bulk” XML links.
- Daily and bulk Clerk XML state: “Pursuant to Title 17 Section 105 of the United States Code, this file is not subject to copyright protection and is in the public domain.”
- Roll-call XML is a directly published Clerk record. `docs.house.gov` publishes an official Atom feed and linked legislative documents.
- Polling uses conditional requests where available, a descriptive user agent, conservative intervals, bounded responses, no credentials, and no access-control bypass.
- Copied records and extracted text remain private under retention policy; public output links to Clerk, House, docs.house.gov, or Congress.gov provenance.

## Media finding

The reviewed `live.house.gov` application references a House-used Azure backend and Bitmovin player routes for streaming and playback. Those routes are exposed in a compiled web application but are not a documented public download API. The page also identifies YouTube as an alternate stream. Under project policy, neither inferred player routes nor YouTube are approved collection methods. The House adapter therefore emits schedule, floor-activity, bill/amendment, and vote evidence but **no media descriptor**. House audio transcription remains unavailable until the House documents a machine-access method or grants permission.

## Registry decision

Discovery method: `official_page`  
Media method: `none`  
Allowed hosts: `clerk.house.gov`, `docs.house.gov`, `live.house.gov`, `www.congress.gov`  
Review expiry: 2027-08-28  
Enabled for launch: **No** — adapter tests and source-specific private validation are still required.

A changed host, XML statement, endpoint contract, robots/terms policy, or documented media method invalidates this review. Undocumented Azure/player and platform endpoints remain prohibited even if technically reachable.
