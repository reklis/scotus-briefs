# Official SCOTUS discovery sources

The discovery adapter accepts an explicit JSON list of official index pages. Only HTTPS PDF
links on `supremecourt.gov` (including its subdomains) are admitted. The initial supported
sources are:

| Material | Authoritative index | Typical configuration |
|---|---|---|
| Oral-argument transcripts | `https://www.supremecourt.gov/oral_arguments/argument_transcript/2024` | `document_type: oral_argument_transcript`, `date_kind: argument` |
| Slip opinions | `https://www.supremecourt.gov/opinions/slipopinion/24` | `document_type: opinion`, `date_kind: decision` |
| Orders of the Court | `https://www.supremecourt.gov/orders/ordersofthecourt.aspx` | `document_type: order` |
| Case docket and filed briefs | `https://www.supremecourt.gov/docket/docketfiles/html/public/<docket>.html` | omit `document_type` to classify PDF anchor labels |

The Court's docket page is the authority for a case title, docket identity, filing links, and
available briefs. Transcript, opinion, and order indexes are independently authoritative for
those document classes. Discovery does not infer a holding from an order or transcript.

These pages are HTML rather than a versioned API. `tests/fixtures/discovery/official-index.html`
preserves the table features used by the adapter. A fixture update must be reviewed when the
Court changes markup; an unexpectedly empty page is a failed source run, never evidence that
previous records should be removed.

## Operator configuration

Example `sources.json`:

```json
[
  {
    "url": "https://www.supremecourt.gov/oral_arguments/argument_transcript/2024",
    "term": 2024,
    "document_type": "oral_argument_transcript",
    "date_kind": "argument"
  },
  {
    "url": "https://www.supremecourt.gov/opinions/slipopinion/24",
    "term": 2024,
    "document_type": "opinion",
    "date_kind": "decision"
  }
]
```

Run `uv run scotus-pipeline discover --sources sources.json`. The client rejects non-Court
index URLs, sends a descriptive user agent, limits connection concurrency, waits between source
pages, and retries HTTP 429 and 5xx responses at most three times with bounded
exponential/`Retry-After` delays. Discovery writes candidates, failures, and `changed_case_ids`
to a report. Its append-and-update metadata snapshot defaults to
`manifests/discovered-cases.json`; cases absent during a failed or partial run are never removed.
Use the report as input to `reconcile` only after reviewing any source failures.
