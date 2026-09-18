<script lang="ts">
  import Seo from '$lib/components/Seo.svelte';
</script>

<Seo
  title="About this guide"
  description="How SCOTUS Briefs creates independent, source-cited plain-language Supreme Court case guides."
  path="/about/"
/>
<header class="page-header">
  <div class="shell narrow">
    <p class="eyebrow">About the project</p>
    <h1>A public guide built around primary sources</h1>
  </div>
</header>
<article class="shell page-content narrow">
  <h2>Civic purpose</h2>
  <p>
    Supreme Court documents are written for lawyers and judges. SCOTUS Briefs helps anyone get
    oriented to a case, compare the parties' positions, understand a supported decision, and
    continue into the original record.
  </p>
  <h2>How guides are made</h2>
  <p>
    Software archives official Court PDFs, extracts page-aware evidence, and uses an AI model to
    draft plain-language sections. Automated checks require citations, preserve attribution, and
    prevent pending cases from being presented as decided. Only accepted guide records are
    published.
  </p>
  <p>
    AI can still make mistakes. Each case page links its primary documents and identifies incomplete
    or source-limited sections. Readers should verify consequential details in those documents.
  </p>
  <h2>Independent and educational</h2>
  <p>
    This project is independent, is not affiliated with or endorsed by the Supreme Court of the
    United States, and does not speak for the Court or any party. Its summaries are educational
    information, not legal advice.
  </p>
  <h2>Open record</h2>
  <p>
    The <a href="https://github.com/reklis/scotus-briefs" rel="external">public source repository</a
    > retains normalized case records, accepted guides, provenance, and immutable copies of archived
    PDFs. The PDFs are linked from case pages but deliberately excluded from this website's deployment
    artifact.
  </p>
</article>
