# Generated content and source rights

This policy identifies what the repository licenses and what it does not.

| Material | Treatment |
|---|---|
| Repository-authored software | Apache-2.0 (`LICENSE`) |
| Repository-authored documentation | Apache-2.0 (`LICENSE`) |
| Original prose in generated legal briefs | CC BY 4.0 (`LICENSE.generated-content`) |
| Public provenance metadata and facts | Published for verification; facts are not made proprietary |
| Official Court opinions, orders, dockets, transcripts, and source pages | Linked, not redistributed; excluded from the project's licenses |
| Third-party quotations or material | Governed by the third party's rights and explicitly excluded unless stated otherwise |
| Test fixtures | Invented/synthetic and repository-licensed; `tests/fixtures/README.md` governs them |
| Court or third-party names, seals, and logos | No trademark license or affiliation; logos are not included |

## Attribution for briefs

When reusing original generated brief prose, identify “SCOTUS Legal Briefs,” link to
the specific brief/revision when practical, state the CC BY 4.0 license, and indicate
whether the text was changed. Keep the automated-analysis disclosure and official
source links with substantial excerpts so readers can understand the limitations and
verify the source.

CC BY 4.0 applies only to copyrightable original generated prose. It does not claim
rights in case facts, citations, official government works, or third-party material.
The complete license text is in `LICENSE.generated-content`.

## Data boundary

Generated-content may contain only versioned public projection JSON, immutable public
case revisions, bounded discovery validators/digests, public release manifests, and
opaque model-attempt receipts whose local estimated cost is zero. It must never contain
copied PDFs/media, full or partial source
page bodies, extracted transcript text, prompts, model responses, approved/rejected
claim ledgers, object keys, credentials, internal UUIDs, or private processing logs.

Official source material is transient private input. It is downloaded only by a
trusted bounded job on the self-hosted runner, recomputed for a changed case, removed
during unconditional post-build cleanup, and never uploaded as a cache or artifact.
Pre-build cleanup also removes residue from an interrupted prior job. A URL and page label provide
provenance without republishing the underlying document.

This policy is not a legal opinion about any Court or third-party work. If source
rights or presentation change, publication fails closed pending review.
