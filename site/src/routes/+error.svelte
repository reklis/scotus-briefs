<script lang="ts">
  import { page } from '$app/state';
  import Seo from '$lib/components/Seo.svelte';
</script>

<Seo
  title={page.status === 404 ? 'Page not found' : 'Something went wrong'}
  description="The requested SCOTUS Briefs page could not be found."
/>
<div class="shell page-content narrow error-page">
  <p class="eyebrow">Error {page.status}</p>
  <h1>{page.status === 404 ? 'That page is not in the record.' : 'Something went wrong.'}</h1>
  <p>
    {page.status === 404
      ? 'The address may have changed, or the case may not be published yet.'
      : 'The page could not be displayed.'}
  </p>
  <p>
    <a class="button" href="/search/">Search cases</a>
    <a class="button secondary" href="/terms/">Browse by term</a>
  </p>
</div>

<style>
  .error-page {
    padding-block: 5rem;
  }
</style>
