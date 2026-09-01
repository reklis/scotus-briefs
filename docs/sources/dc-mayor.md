# DC mayoral source review

Review date: 2026-08-28  
Reviewer: project source-access review  
Registry source: `dc_mayor`

## Approved MVP methods

| Material | Exact official endpoint/pattern | Decision |
|---|---|---|
| Newsroom and public-calendar releases | `https://mayor.dc.gov/newsroom` and same-host `/release/...` pages | Approved official-page input |
| Release/calendar feed | `https://mayor.dc.gov/rss.xml` | Approved official-feed discovery and document input |
| Public events listing | `https://mayor.dc.gov/events` | Approved metadata page, subject to coverage validation |
| Same-host briefing attachments | HTTPS files directly linked from approved `mayor.dc.gov` releases | Approved document candidates |
| Live/archive video or audio | YouTube, Facebook, bit.ly destinations, and unvalidated OCTFME/player services | **Not approved for automated media collection** |

## Access basis and limits

- The Mayor's official site publishes an RSS feed and newsroom/public-calendar release pages.
- DC.gov Terms and Conditions state that, except for protected third-party or otherwise noted content, site content is licensed under Creative Commons Attribution 3.0. Public output must attribute and link to the official page.
- `https://mayor.dc.gov/robots.txt`, observed 2026-08-28, permits the relevant feed, newsroom, event, and release paths for `User-agent: *`, specifies `Crawl-delay: 10`, and disallows administrative/system paths.
- Polling uses a descriptive user agent, at least a ten-second host-local interval, conditional requests where available, bounded responses, and no access-control bypass.

## Excluded methods

The newsroom links to the Mayor's YouTube and Facebook accounts, and some releases use short links for video. No stable, documented same-host live or archive media endpoint was established. `video.oct.dc.gov` was not established as the exact source for a mayoral briefing, and its robots content-signals notice requires separate use review. The adapter does not follow shorteners or platform links and emits no media descriptor.

The RSS feed can establish the text and publication time of an announcement. A public-calendar item can establish a planned event. Neither proves that a briefing occurred, that a statement was spoken, or that an announced program was implemented.

## Registry decision

Discovery method: `official_feed`  
Media method: `none`  
Allowed host: `mayor.dc.gov`  
Review expiry: 2027-08-28  
Enabled for launch: **No** — feed freshness, calendar parsing, and source-specific private validation are still required.

A changed host, terms/license, robots policy, feed schema, short-link destination, or media method invalidates this review. Platform media remains fail-closed.
