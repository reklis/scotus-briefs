## Why

Supreme Court dockets and opinions are written for legal professionals, leaving ordinary citizens without a clear, approachable way to understand what each case concerns, what the parties argue, what the Court decides, and why the outcome matters. The project already has a public GitHub repository, custom domain, self-hosted runner, local GPT-OSS service, and a backfill corpus, making it possible to launch an independently rebuildable citizen guide now.

## What Changes

- Replace the existing `reklis/scotus-briefs` application with a clean implementation while preserving the repository, runner registration, GitHub Pages site, and `scotusbriefs.us` domain.
- Add an incremental downloader that discovers Supreme Court case documents, validates them, records provenance, and commits immutable PDFs and manifests to the repository.
- Import the existing PDF corpus for historical backfill.
- Extract page-aware text from PDFs and use `ragchew-gpt-oss:120b-32k` on the Ollama service at `192.168.1.41` to translate legal material into evidence-backed, plain-language citizen guides.
- Publish guide sections for the case overview, each side's arguments, the Court's decision when available, and why the case or ruling matters, while clearly representing pending and incomplete cases.
- Add a statically generated SvelteKit website with case pages, term and docket-number browsing, client-side search, source citations, and links to official and archived documents.
- Add manual, backfill, and nightly GitHub Actions operation on the existing `spark` self-hosted runner, including checked-in generated content and deployment to GitHub Pages.
- Favor a direct MVP publication flow without requiring branch review or branch protection.

## Capabilities

### New Capabilities
- `court-document-archive`: Discover, validate, identify, version, and commit Supreme Court PDFs and their provenance metadata.
- `citizen-guide-generation`: Extract source text and generate structured, cited, plain-language case explanations with the local GPT-OSS model.
- `case-catalog`: Present searchable and browsable Supreme Court cases organized by term and docket number, including lifecycle-aware case pages.
- `static-publication`: Build and deploy the SvelteKit static site and run resumable backfill and incremental nightly updates through GitHub Actions.

### Modified Capabilities

None.

## Impact

- Replaces the current application code in `reklis/scotus-briefs`; existing implementation details and generated formats are not retained.
- Adds a SvelteKit/TypeScript static web application, PDF extraction tooling, downloader and generation tooling, structured case data, prompts, schemas, and automated tests.
- Uses the public GitHub repository as durable storage for source PDFs, manifests, generated guides, and site source; PDF files are excluded from the Pages artifact itself.
- Uses the existing ARM64 `spark` self-hosted runner for LAN access to Ollama and GitHub Pages workflows for public deployment.
- Repository history will grow as immutable PDFs and corrected document revisions are added.
