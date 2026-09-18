<script lang="ts">
  import { onMount } from 'svelte';
  import Seo from '$lib/components/Seo.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import { searchCases, type RankedResult } from '$lib/search';
  import type { SearchRecord } from '$lib/types';

  let query = $state('');
  let index = $state<SearchRecord[]>([]);
  let results = $state<RankedResult[]>([]);
  let loaded = $state(false);
  let failed = $state(false);

  onMount(async () => {
    query = new URLSearchParams(window.location.search).get('q') ?? '';
    try {
      const response = await fetch('/search-index.json');
      if (!response.ok) throw new Error('Index unavailable');
      index = await response.json();
      runSearch();
    } catch {
      failed = true;
    } finally {
      loaded = true;
    }
  });

  function runSearch() {
    results = searchCases(index, query);
    const url = new URL(window.location.href);
    if (query.trim()) url.searchParams.set('q', query.trim());
    else url.searchParams.delete('q');
    history.replaceState({}, '', url);
  }

  function parts(text: string): Array<{ text: string; match: boolean }> {
    const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
    if (!terms.length) return [{ text, match: false }];
    const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const regex = new RegExp(`(${escaped.join('|')})`, 'gi');
    return text
      .split(regex)
      .filter(Boolean)
      .map((part) => ({ text: part, match: terms.includes(part.toLowerCase()) }));
  }
</script>

<Seo
  title="Search cases"
  description="Search Supreme Court case guides by docket, title, question, or topic."
  path="/search/"
/>
<header class="page-header">
  <div class="shell narrow">
    <p class="eyebrow">Find a case</p>
    <h1>Search the case guides</h1>
    <p>Title and docket matches are ranked first, followed by questions and guide text.</p>
  </div>
</header>
<div class="shell page-content narrow">
  <form
    onsubmit={(event) => {
      event.preventDefault();
      runSearch();
    }}
    role="search"
  >
    <label for="case-search">Case title, docket number, or topic</label>
    <div class="search-row">
      <input
        id="case-search"
        name="q"
        type="search"
        bind:value={query}
        placeholder="For example: 24-304 or free speech"
        autocomplete="off"
      /><button class="button" type="submit">Search</button>
    </div>
  </form>
  <noscript
    ><div class="notice">
      Search needs JavaScript. You can still <a href="/terms/">browse every case by term</a>.
    </div></noscript
  >
  <div class="results" aria-live="polite" aria-busy={!loaded}>
    {#if failed}<div class="notice">
        Search is temporarily unavailable. <a href="/terms/">Browse cases by term instead.</a>
      </div>
    {:else if loaded && query.trim() && !results.length}<div class="empty">
        <h2>No cases found</h2>
        <p>Try a shorter phrase or docket number, or <a href="/terms/">browse by term</a>.</p>
      </div>
    {:else if results.length}
      <h2>{results.length} {results.length === 1 ? 'result' : 'results'}</h2>
      <ol class="result-list">
        {#each results as result}
          <li>
            <div class="result-meta">
              <span class="dockets">{result.dockets.join(' · ')}</span><StatusBadge
                status={result.lifecycle}
              />
            </div>
            <h3><a href={`/cases/${result.slug}/`}>{result.title}</a></h3>
            <p>
              {#each parts(result.excerpt) as part}{#if part.match}<mark>{part.text}</mark
                  >{:else}{part.text}{/if}{/each}
            </p>
            <small>{result.term} term</small>
          </li>
        {/each}
      </ol>
    {:else if loaded}<p class="prompt">Enter a title, docket number, or subject to begin.</p>{/if}
  </div>
</div>

<style>
  form label {
    display: block;
    font-weight: 700;
    margin-bottom: 0.4rem;
  }
  .search-row {
    display: flex;
    gap: 0.5rem;
  }
  input {
    border: 2px solid #75838b;
    border-radius: 3px;
    font: inherit;
    min-width: 0;
    padding: 0.72rem;
    width: 100%;
  }
  .results {
    margin-top: 2.5rem;
    min-height: 10rem;
  }
  .result-list {
    list-style: none;
    padding: 0;
  }
  .result-list li {
    background: #fff;
    border-bottom: 1px solid var(--line);
    padding: 1.3rem;
  }
  .result-list h3 {
    margin: 0.5rem 0;
  }
  .result-list p {
    color: var(--muted);
    margin: 0.4rem 0;
  }
  .result-meta {
    align-items: center;
    display: flex;
    justify-content: space-between;
  }
  mark {
    background: #ffe39b;
    color: inherit;
  }
  .prompt,
  .empty {
    color: var(--muted);
    text-align: center;
    padding: 2rem;
  }
  @media (max-width: 450px) {
    .search-row {
      align-items: stretch;
      flex-direction: column;
    }
  }
</style>
