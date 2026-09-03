# Supreme Court source review

Review date: 2026-08-28  
Reviewer: project source-access review  
Registry source: `supreme_court`

## Approved MVP methods

| Material | Official method | Exact index/pattern | Decision |
|---|---|---|---|
| Argument calendar and day lists | Bounded HTML polling and linked PDF collection | `https://www.supremecourt.gov/oral_arguments/calendarsandlists.aspx` and same-host PDF links | Approved metadata/document candidate |
| Archived oral-argument index | Bounded HTML polling | `https://www.supremecourt.gov/oral_arguments/argument_audio/{term}` | Approved discovery method |
| Audio detail | Same-host HTML page | `https://www.supremecourt.gov/oral_arguments/audio/{term}/{docket}` | Approved discovery detail |
| Archived argument audio | Explicit downloadable MP3 | `https://www.supremecourt.gov/media/audio/mp3files/{docket}.mp3` as linked by the detail page | Approved private media input |
| Official argument transcript | Explicit linked PDF | `/oral_arguments/argument_transcripts/{term}/{file}.pdf` as linked by the detail or term transcript page | Approved official-document input |
| Y2K transcript archive | Bounded term HTML polling and linked PDF collection | `/oral_arguments/argument_transcript/{term}` and `/pdfs/transcripts/{term}/{file}.pdf` for October Terms 2000–2009 | Approved official-document backfill input |
| Case docket | Official same-host docket page | `/docket/docketfiles/html/public/{docket}.html` | Approved metadata/document input where available; archive backfill currently requests these for Terms 2015 onward |
| Opinions and orders | Official HTML indexes and linked PDFs | `/opinions/slipopinion/{term}` and `/opinions/relatingtoorders/{term}` | Approved official-document candidates when correlated by exact docket; the current no-redirect indexes cover Terms 2017 onward |
| Live audio | Official live landing page only | `https://www.supremecourt.gov/oral_arguments/live.aspx` | Metadata/availability only; no persistent stream method approved |

The live page returned “There are no Oral Arguments or Live Audio scheduled for today” during review, so no underlying stream endpoint could be validated. The adapter must not derive, scrape, or retain a player endpoint. Live collection remains disabled until an active-session endpoint is observed and separately reviewed; the MVP may use the official archive.

## Access basis and limits

- The official detail page explicitly presents MP3 and transcript **Download** links.
- `https://www.supremecourt.gov/robots.txt`, observed 2026-08-28, allows the oral-argument, docket, and opinion paths for `User-agent: *`, disallows `/images/`, `/rss/`, and `/cdn/`, and specifies `Crawl-delay: 1`.
- GitHub-hosted nightly requests identify this project as `SCOTUS-Legal-Briefs/0.1 contact=https://github.com/reklis/scotus-briefs`. This repository URL is the contact route; no unconfirmed email address is advertised.
- Saved ETag and Last-Modified values are sent conditionally where supported. A 304 creates no processing work. Without a reliable validator, the job performs only a bounded streamed GET and compares the saved byte digest.
- Routine runs are at most once nightly, preserve at least one second between Court requests, and enforce configured request/byte/document/case/runtime limits. They check the active term and recent transcript/correction/opinion window plus a small rotating historical slice. Bounded historical bootstrap is separately manually dispatched.
- Backfill begins with October Term 2000. Earlier scanned terms remain out of product scope.
- A Court-hosted PDF marked encrypted may be processed only when it opens with an empty password. Any PDF requiring a password remains rejected; this does not bypass access control.
- Source HTML, copied documents, and extracted text exist only in the permission-restricted transient build workspace and are unconditionally deleted. They are never committed, cached, uploaded, or redistributed. Public briefs retain official links, page labels, validators/digests, and sanitized provenance only.
- Federal government works are generally addressed by 17 U.S.C. §105, but this implementation decision is not legal advice and does not assume rights in third-party material. Only Court-hosted files explicitly linked by approved pages are candidates.

## Registry decision

Discovery method: `official_page`  
Media method: `downloadable_file`  
Allowed host: `www.supremecourt.gov`  
Review expiry: 2027-08-28  
Enabled for launch: **No** — adapter tests and source-specific private validation are still required.

A changed host, redirect, TLS/access method, robots policy or crawl delay, terms/privacy notice, authentication requirement, downloadable-link presentation, content type, material URL pattern, or newly introduced third-party host invalidates this review and must set source health to `review_required`. Repeated validator anomalies or a response that exceeds configured bounds also stop normal processing pending review. One transient missing/failed response never implies deletion or retraction of a published case.
