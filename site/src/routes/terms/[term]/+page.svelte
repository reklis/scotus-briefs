<script lang="ts">
  import Seo from '$lib/components/Seo.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import type { PublicCase, TermSummary } from '$lib/types';
  let { data }: { data: { summary: TermSummary; cases: PublicCase[]; terms: TermSummary[] } } =
    $props();
</script>

<Seo
  title={`${data.summary.term} Supreme Court term`}
  description={`Browse ${data.summary.caseCount} Supreme Court case guides from the ${data.summary.term} term.`}
  path={`/terms/${encodeURIComponent(data.summary.term)}/`}
/>
<header class="page-header">
  <div class="shell">
    <p class="eyebrow">Supreme Court case catalog</p>
    <h1>{data.summary.term} term</h1>
    <p>
      {data.summary.caseCount}
      {data.summary.caseCount === 1 ? 'case' : 'cases'} represented{data.summary.latest
        ? ' · Latest term in this catalog'
        : ''}
    </p>
  </div>
</header>
<div class="shell page-content">
  <nav aria-label="Browse other terms">
    <ul class="term-nav">
      {#each data.terms as item}<li>
          <a
            aria-current={item.term === data.summary.term ? 'page' : undefined}
            href={`/terms/${encodeURIComponent(item.term)}/`}>{item.term}</a
          >
        </li>{/each}
    </ul>
  </nav>
  <p class="muted">
    Cases are ordered by parsed docket number. Application dockets follow standard dockets for the
    same docket year.
  </p>
  <ul class="case-list">
    {#each data.cases as item}
      <li>
        <span class="dockets">{item.dockets.join(' · ')}</span>
        <div>
          <h2><a href={`/cases/${item.slug}/`}>{item.title}</a></h2>
          {#if item.dockets.length > 1}<small class="muted">Consolidated case</small>{/if}
        </div>
        <StatusBadge status={item.lifecycle} />
      </li>
    {/each}
  </ul>
</div>
