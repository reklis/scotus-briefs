<script lang="ts">
  import type { Citation, DocumentSource } from '$lib/types';

  let { citations, documents }: { citations: Citation[]; documents: DocumentSource[] } = $props();

  function documentFor(citation: Citation) {
    return documents.find(
      (document) => document.id === citation.documentId || document.sha256 === citation.documentId
    );
  }

  function pageLabel(citation: Citation): string {
    if (!citation.pageStart) return '';
    return citation.pageEnd && citation.pageEnd !== citation.pageStart
      ? `, pp. ${citation.pageStart}–${citation.pageEnd}`
      : `, p. ${citation.pageStart}`;
  }
</script>

{#if citations.length}
  <ul class="citations" aria-label="Sources for this section">
    {#each citations as citation, index}
      {@const source = documentFor(citation)}
      <li>
        <a href={source ? `#source-${source.id}` : '#sources'}>
          [{index + 1}] {citation.label ?? source?.title ?? 'Source document'}{pageLabel(citation)}
        </a>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .citations {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem 0.8rem;
    list-style: none;
    margin: 1rem 0 0;
    padding: 0;
  }
  .citations a {
    font-size: 0.88rem;
  }
</style>
