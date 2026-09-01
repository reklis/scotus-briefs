# Configuration

Non-secret, versioned settings are split by product path. Environment variables prefixed with `RAGCHEW_` contain deployment endpoints and secrets.

## Active SCOTUS Legal Briefs configuration

`config/scotus.yaml` is the active product configuration. `RAGCHEW_SCOTUS_CONFIG_PATH` selects another file and `RAGCHEW_PRODUCT_MODE` defaults to `scotus_legal_briefs`.

### Discovery

Configured Court terms, polling interval, one-second minimum crawl delay, backfill case cap, and queue priorities bound source work. Newly published transcript work has higher priority than historical backfill. The source remains disabled until private launch gates pass.

### Documents and parser

Only approved Court-hosted transcript/docket/order/opinion documents are candidates. PDFs default to a 50 MiB and 500-page maximum with 8 MiB in-memory spooling before temporary-disk spill. Audio download and STT are explicit `false` settings; typed validation rejects enabling either.

The parser name/version, minimum line coverage, and zero ambiguous-page default are evidence-version inputs. Changes require replay and comparison rather than overwriting prior parses.

### Retention and generation

Copied documents default to 24 hours and private extracted text to 30 days. Failed downloads default to 24 hours. Digests, official URLs, page/line provenance, case history, approved claims, and public corrections can remain after private source deletion.

Generation uses the official OpenAI API with `gpt-5`, is paraphrase-first and claim-ledger bound, and prohibits justice-vote predictions and personalized legal advice. The API key may use the standard `OPENAI_API_KEY` name or deployment-prefixed `RAGCHEW_OPENAI_API_KEY`; it is always treated as a secret. Plain-language controls target readers without legal training: 30 words per sentence, 120 words per paragraph, everyday headings, and no unexplained legalese. Public case pages require a complete official transcript.

### Launch gates

Source collection and the public runtime have independent fail-closed switches: `enabled` and `publication.enabled`. Defaults require seven private-preview cycles, at least 20 reviewed fixture profiles, perfect page/line provenance and factual grounding, and zero legal-status upgrades or sensitive leaks. Live discovery remains separately labeled unvalidated when no newly posted transcript exists in the recorded corpus.

## Dormant configurations

`config/proceedings.yaml` retains disabled multi-government-source settings. `config/mvp.yaml` retains the original DCFD radio settings. Neither is active for SCOTUS Legal Briefs, and all non-Supreme sources remain disabled.

Production operators must provide database, object-storage, model, and authentication secrets through deployment secret stores rather than YAML files.
