# DC Council source review

Review date: 2026-08-28  
Reviewer: project source-access review  
Registry source: `dc_council`

## Approved MVP methods

| Material | Exact official endpoint/pattern | Decision |
|---|---|---|
| Hearing/meeting calendar | `https://dccouncil.gov/wp-json/tribe/events/v1/events` | Approved documented API |
| Calendar API contract | `https://dccouncil.gov/wp-json/tribe/events/v1/doc` | OpenAPI contract used for fixtures/compatibility checks |
| Council releases/actions | `https://dccouncil.gov/feed/` and same-host release pages | Approved official feed/page input |
| Direct agendas/attachments | HTTPS files on `dccouncil.gov` explicitly linked by an approved event/release | Approved document candidates |
| LIMS legislation/actions | `https://lims.dccouncil.gov/` | Public provenance links only; automated retrieval not approved |
| Hearing Management System | Links/short links from calendar descriptions | Public provenance only; automated retrieval not approved |
| Live/archive video | Granicus, OCTFME/player links, and Council YouTube | **Not approved for automated media collection** |

## Access basis and limits

- `dccouncil.gov` publishes a machine-readable REST API and an OpenAPI description stating that the Events Calendar REST API allows access to upcoming event information.
- `https://dccouncil.gov/robots.txt`, observed 2026-08-28, has no disallowed path for `User-agent: *` and publishes the site sitemap.
- The official site publishes an RSS feed for Council releases and actions.
- Polling uses documented query parameters, bounded page sizes/date windows, a descriptive user agent, conservative intervals, and no access-control bypass. Direct files must remain on the approved host.

## Excluded methods

- `https://lims.dccouncil.gov/robots.txt` specifies `User-agent: *` and `Disallow: /`. LIMS pages may be linked publicly but are not fetched by this implementation.
- `https://dc.granicus.com/robots.txt` specifies `User-agent: *` and `Disallow: /`. Calendar descriptions point to Granicus and other player/short-link destinations; these produce no media descriptor.
- The Council YouTube channel is not an approved machine media source. Hearing Management links and shorteners are not followed.
- Because no approved official vote/legislation API was established, calendar text and releases cannot be upgraded into an adopted vote or enacted law without a separately approved official document.

## Registry decision

Discovery method: `documented_api`  
Media method: `none`  
Allowed host: `dccouncil.gov`  
Review expiry: 2027-08-28  
Enabled for launch: **No** — source coverage is incomplete, media is unavailable, and private validation is still required.

A changed API schema, host, robots/terms policy, link destination, or media method invalidates this review. Platform-only media and LIMS remain fail-closed.
