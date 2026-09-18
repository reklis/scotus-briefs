<script lang="ts">
  let {
    title,
    description,
    path = ''
  }: { title: string; description: string; path?: string } = $props();
  const siteName = 'SCOTUS Briefs';
  const canonical = $derived(`https://scotusbriefs.us${path}`);
  const fullTitle = $derived(title === siteName ? title : `${title} | ${siteName}`);
</script>

<svelte:head>
  <title>{fullTitle}</title>
  <meta name="description" content={description} />
  <link rel="canonical" href={canonical} />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content={siteName} />
  <meta property="og:title" content={fullTitle} />
  <meta property="og:description" content={description} />
  <meta property="og:url" content={canonical} />
  <meta name="twitter:card" content="summary" />
</svelte:head>
