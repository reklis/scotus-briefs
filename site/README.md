# SCOTUS Briefs static site

SvelteKit frontend for the citizen case guide. The production build reads JSON from
`../data/cases`, `../data/guides`, and `../manifests`; it never reads or copies PDF bytes.
Only guides whose validation state is `accepted`, `passed`, or `valid` are published.
Malformed records, duplicate IDs/slugs, unknown case references, unknown citation documents,
and decision text on undecided cases fail the build.

## Commands

```sh
npm ci
npm run dev
npm run format:check
npm run check
npm test
npm run build
```

`npm run build` prerenders all catalog routes and validates internal links, required routes,
metadata, custom-domain files, PDF exclusion, and a 20 MiB default artifact limit. Override the
limit with `PAGES_ARTIFACT_MAX_BYTES`. For isolated builds, set `SCOTUS_DATA_ROOT` and
`SCOTUS_MANIFEST_ROOT`; the test suite does this with files under `tests/fixtures/`.
