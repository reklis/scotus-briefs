<script lang="ts">
  import Seo from '$lib/components/Seo.svelte';
  import type { TermSummary } from '$lib/types';
  let { data }: { data: { terms: TermSummary[] } } = $props();
</script>

<Seo
  title="Browse Supreme Court terms"
  description="Browse plain-language Supreme Court case guides by term."
  path="/terms/"
/>
<header class="page-header">
  <div class="shell">
    <p class="eyebrow">Case catalog</p>
    <h1>Browse by term</h1>
    <p>Supreme Court terms begin in October and are identified by their starting year.</p>
  </div>
</header>
<div class="shell page-content narrow">
  {#if data.terms.length}
    <ul class="term-list">
      {#each data.terms as item}
        <li>
          <a href={`/terms/${encodeURIComponent(item.term)}/`}
            ><strong>{item.term} term</strong><span
              >{item.caseCount}
              {item.caseCount === 1 ? 'case' : 'cases'}{item.latest
                ? ' · Latest represented term'
                : ''}</span
            ></a
          >
        </li>
      {/each}
    </ul>
  {:else}
    <div class="notice">No publishable case records are available yet.</div>
  {/if}
</div>

<style>
  .term-list {
    list-style: none;
    padding: 0;
  }
  .term-list a {
    align-items: center;
    background: #fff;
    border-bottom: 1px solid var(--line);
    display: flex;
    justify-content: space-between;
    padding: 1.2rem;
    text-decoration: none;
  }
  .term-list span {
    color: var(--muted);
    font-size: 0.9rem;
  }
  @media (max-width: 500px) {
    .term-list a {
      align-items: start;
      flex-direction: column;
    }
  }
</style>
