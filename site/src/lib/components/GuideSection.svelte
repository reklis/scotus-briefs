<script lang="ts">
  import Citations from './Citations.svelte';
  import type { DocumentSource, GuideSection } from '$lib/types';

  let {
    id,
    heading,
    section,
    documents
  }: { id: string; heading: string; section: GuideSection; documents: DocumentSource[] } = $props();
</script>

<section {id} class="guide-section" aria-labelledby={`${id}-heading`}>
  <h2 id={`${id}-heading`}>{heading}</h2>
  {#if section.state !== 'available'}
    <div class="notice" role="status">
      <strong>{section.state === 'pending' ? 'Pending' : 'Source-limited'}</strong>
      <span>
        {section.state === 'pending'
          ? ' This section will be added when the relevant Court material is available.'
          : ' The available primary sources do not support a complete explanation yet.'}
      </span>
    </div>
  {/if}
  {#each section.paragraphs as paragraph}
    <p>{paragraph}</p>
  {/each}
  <Citations citations={section.citations} {documents} />
</section>
