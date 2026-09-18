<script lang="ts">
  import '../styles.css';
  import type { Snippet } from 'svelte';
  import type { TermSummary } from '$lib/types';

  let { data, children }: { data: { terms: TermSummary[] }; children: Snippet } = $props();
  const latestTerm = $derived(data.terms.find((term) => term.latest));
</script>

<a class="skip-link" href="#main-content">Skip to main content</a>
<header class="site-header">
  <div class="shell masthead">
    <a class="brand" href="/" aria-label="SCOTUS Briefs home">
      <span class="brand-mark" aria-hidden="true">§</span>
      <span>SCOTUS <strong>Briefs</strong></span>
    </a>
    <nav aria-label="Primary navigation">
      <a href={latestTerm ? `/terms/${encodeURIComponent(latestTerm.term)}/` : '/terms/'}
        >Browse cases</a
      >
      <a href="/search/">Search</a>
      <a href="/about/">About</a>
    </nav>
  </div>
</header>
<main id="main-content">
  {@render children()}
</main>
<footer>
  <div class="shell footer-grid">
    <div>
      <strong>SCOTUS Briefs</strong>
      <p>Independent, AI-generated plain-language guides to U.S. Supreme Court cases.</p>
    </div>
    <div>
      <strong>Important notice</strong>
      <p>This site is not affiliated with the Supreme Court and does not provide legal advice.</p>
    </div>
    <nav aria-label="Footer navigation">
      <a href="/terms/">All terms</a>
      <a href="/search/">Search</a>
      <a href="/about/">How this works</a>
    </nav>
  </div>
</footer>
