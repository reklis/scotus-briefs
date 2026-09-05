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
| Active-term slip opinions | Bounded conditional HTML polling and individually linked PDFs | `/opinions/slipopinion/{two-digit-term}` and `/opinions/{two-digit-term}pdf/{file}.pdf` | Approved independent case discovery for strict individual opinion, per-curiam, and decree rows, including listed revisions |
| Opinions relating to orders | Bounded conditional HTML polling and linked PDFs | `/opinions/relatingtoorders/{two-digit-term}` | Approved related-document candidates only when an official row correlates to a known argued case by exact docket; the current no-redirect indexes cover Terms 2017 onward |
| Live audio | Official live landing page only | `https://www.supremecourt.gov/oral_arguments/live.aspx` | Metadata/availability only; no persistent stream method approved |

The live page returned “There are no Oral Arguments or Live Audio scheduled for today” during review, so no underlying stream endpoint could be validated. The adapter must not derive, scrape, or retain a player endpoint. Live collection remains disabled until an active-session endpoint is observed and separately reviewed; the MVP may use the official archive.

## Access basis and limits

- The official detail page explicitly presents MP3 and transcript **Download** links.
- `https://www.supremecourt.gov/robots.txt`, observed 2026-08-28, allows the oral-argument, docket, and opinion paths for `User-agent: *`, disallows `/images/`, `/rss/`, and `/cdn/`, and specifies `Crawl-delay: 1`.
- The self-hosted nightly build identifies this project as `ragchew-scotus-briefs/1.0 (+https://github.com/reklis/scotus-briefs; contact=https://github.com/reklis)`. These repository/profile URLs are the contact route; no unconfirmed email address is advertised.
- Saved ETag and Last-Modified values are sent conditionally where supported. A 304 creates no processing work. Without a reliable validator, the job performs only a bounded streamed GET and compares the saved byte digest.
- Routine runs occur daily at 03:17 UTC (`17 3 * * *`), preserve at least one second between Court requests, and enforce configured request/byte/document/case/runtime limits. They independently poll the active term's slip-opinion table before bounded selection, check recent transcript/correction resources, and check a small rotating historical slice. Bounded historical bootstrap is separately manually dispatched.
- Backfill begins with October Term 2000. Earlier scanned terms remain out of product scope.
- A Court-hosted PDF marked encrypted may be processed only when it opens with an empty password. Any PDF requiring a password remains rejected; this does not bypass access control.
- Source HTML, copied documents, and extracted text exist only in the permission-restricted transient build workspace and are unconditionally deleted. They are never committed, cached, uploaded, or redistributed. Public briefs retain official links, page labels, validators/digests, and sanitized provenance only.
- Federal government works are generally addressed by 17 U.S.C. §105, but this implementation decision is not legal advice and does not assume rights in third-party material. Only Court-hosted files explicitly linked by approved pages are candidates.

## Supported disposition scope

The independent disposition path covers each strict individual row on the configured
active-term slip-opinion table. It preserves normalized primary and consolidated
dockets, captions, official opinion PDF URLs, original publication dates, and the
latest date/reference listed for a revision. Signed opinions, per-curiam dispositions,
decrees, and emergency `A` dockets are supported even when no oral-argument row or
transcript exists. Exact docket identity merges a disposition with an argued case;
otherwise the row may create a disposition-only case. A later poll that omits a
previously observed row does not imply deletion or retraction.

This approval does not cover parsing an omnibus order-list PDF into cases, publishing
every certiorari denial, expanding lower-court documents, or independently creating
cases from the relating-to-orders index. Those are separate source/product decisions.
Court PDFs and index bodies remain ephemeral and are linked rather than republished.

## Registry decision

Discovery method: `official_page`  
Media method: `downloadable_file`  
Allowed host: `www.supremecourt.gov`  
Review expiry: 2027-08-28  
Enabled for bounded production: **Yes** — owner-authorized on 2026-09-03. Every poll,
document, case, and release still passes the reviewed source, budget, grounding,
privacy, completeness, and integrity gates; manual dispatch cannot bypass them.

A changed host, redirect, TLS/access method, robots policy or crawl delay, terms/privacy notice, authentication requirement, downloadable-link presentation, content type, material URL pattern, or newly introduced third-party host invalidates this review and must set source health to `review_required`. Repeated validator anomalies or a response that exceeds configured bounds also stop normal processing pending review. One transient missing/failed response never implies deletion or retraction of a published case.
