<script lang="ts">
  import Citations from '$lib/components/Citations.svelte';
  import GuideSection from '$lib/components/GuideSection.svelte';
  import Seo from '$lib/components/Seo.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import type { GuideSection as GuideSectionType, PublicCase } from '$lib/types';

  let { data }: { data: { item: PublicCase } } = $props();
  const item = $derived(data.item);
  const unavailable: GuideSectionType = { state: 'source-limited', paragraphs: [], citations: [] };
  const pending: GuideSectionType = { state: 'pending', paragraphs: [], citations: [] };

  function date(value?: string) {
    if (!value) return undefined;
    const parsed = new Date(`${value}${/^\d{4}-\d{2}-\d{2}$/.test(value) ? 'T00:00:00Z' : ''}`);
    return Number.isNaN(parsed.valueOf())
      ? value
      : new Intl.DateTimeFormat('en-US', { dateStyle: 'long', timeZone: 'UTC' }).format(parsed);
  }
</script>

<Seo
  title={item.title}
  description={item.guide?.overview.paragraphs[0] ??
    `Case information and primary sources for ${item.title}.`}
  path={`/cases/${item.slug}/`}
/>
<header class="case-header">
  <div class="shell narrow">
    <p class="eyebrow">{item.term} Supreme Court term</p>
    <h1>{item.title}</h1>
    <div class="case-line">
      <span class="dockets">Dockets {item.dockets.join(' · ')}</span><StatusBadge
        status={item.lifecycle}
      />
    </div>
    <dl class="metadata">
      {#if item.dates.filed}<div>
          <dt>Filed</dt>
          <dd>{date(item.dates.filed)}</dd>
        </div>{/if}
      {#if item.dates.scheduled}<div>
          <dt>Scheduled</dt>
          <dd>{date(item.dates.scheduled)}</dd>
        </div>{/if}
      {#if item.dates.argued}<div>
          <dt>Argued</dt>
          <dd>{date(item.dates.argued)}</dd>
        </div>{/if}
      {#if item.dates.decided}<div>
          <dt>Decided</dt>
          <dd>{date(item.dates.decided)}</dd>
        </div>{/if}
    </dl>
  </div>
</header>
<div class="shell case-layout">
  <aside class="case-nav">
    <nav aria-label="On this page">
      <strong>On this page</strong>
      <a href="#overview">Overview</a>
      <a href="#background">Background and question</a>
      <a href="#arguments">What each side argues</a>
      <a href="#oral-argument">Oral argument</a>
      <a href="#decision">Decision</a>
      <a href="#significance">Why it matters</a>
      <a href="#terms">Terms to know</a>
      <a href="#sources">Primary sources</a>
    </nav>
  </aside>
  <article class="case-content">
    <div class="ai-note">
      <strong>AI-generated independent summary</strong>
      <p>
        This educational guide is based on the linked primary sources. It is not a Court
        publication, is not affiliated with the Court, and is not legal advice.
      </p>
    </div>

    <section id="overview" class="overview" aria-labelledby="overview-heading">
      <p class="eyebrow">In brief</p>
      <h2 id="overview-heading">Overview</h2>
      {#if item.guide?.overview.paragraphs.length}
        {#each item.guide.overview.paragraphs as paragraph}<p>{paragraph}</p>{/each}
        <Citations citations={item.guide.overview.citations} documents={item.documents} />
      {:else}
        <div class="notice">
          <strong>Guide pending.</strong> Verified case metadata and available source documents are shown
          below while a supported explanation is prepared.
        </div>
      {/if}
    </section>

    <GuideSection
      id="background"
      heading="Background and question before the Court"
      section={item.guide?.background ?? unavailable}
      documents={item.documents}
    />
    <section id="arguments" class="guide-section" aria-labelledby="arguments-heading">
      <h2 id="arguments-heading">What each side argues</h2>
      {#if item.guide?.arguments.length}
        {#each item.guide.arguments as argument}
          <section class="argument">
            <h3>{argument.party}</h3>
            {#if argument.state !== 'available'}<p class="notice">
                <strong>{argument.state === 'pending' ? 'Pending.' : 'Source-limited.'}</strong> The
                record does not yet support a complete account.
              </p>{/if}
            {#each argument.paragraphs as paragraph}<p>{paragraph}</p>{/each}
            <Citations citations={argument.citations} documents={item.documents} />
          </section>
        {/each}
      {:else}<div class="notice">
          <strong>Source-limited.</strong> Supported descriptions of the parties' positions are not available
          yet.
        </div>{/if}
    </section>
    <GuideSection
      id="oral-argument"
      heading="What happened at oral argument"
      section={item.guide?.oralArgument ?? pending}
      documents={item.documents}
    />
    <GuideSection
      id="decision"
      heading="What the Court decided"
      section={item.guide?.decision ?? pending}
      documents={item.documents}
    />
    <GuideSection
      id="significance"
      heading="Why it matters"
      section={item.guide?.significance ?? unavailable}
      documents={item.documents}
    />

    <section id="terms" class="guide-section" aria-labelledby="terms-heading">
      <h2 id="terms-heading">Terms to know</h2>
      {#if item.guide?.glossary.length}
        <dl class="glossary">
          {#each item.guide.glossary as entry}<div>
              <dt>{entry.term}</dt>
              <dd>{entry.definition}</dd>
            </div>{/each}
        </dl>
      {:else}<p class="muted">No glossary terms are available for this guide.</p>{/if}
    </section>

    <section id="sources" class="guide-section" aria-labelledby="sources-heading">
      <h2 id="sources-heading">Primary sources</h2>
      <p>
        Read the source materials on the official site or open the immutable repository copy used
        for this guide.
      </p>
      {#if item.documents.length}
        <ul class="source-list">
          {#each item.documents as document}
            <li id={`source-${document.id}`}>
              <div>
                <strong>{document.title}</strong>{#if document.type}<small
                    >{document.type.replaceAll('_', ' ')}</small
                  >{/if}
              </div>
              <div class="source-actions">
                {#if document.officialUrl}<a href={document.officialUrl} rel="external"
                    >Official PDF</a
                  >{/if}{#if document.archiveUrl}<a href={document.archiveUrl} rel="external"
                    >Archived copy</a
                  >{/if}
              </div>
            </li>
          {/each}
        </ul>
      {:else}<div class="notice">Source links are being prepared for this case.</div>{/if}
      {#if item.guide?.generatedAt}<p class="muted">
          <small
            >Guide generated {date(item.guide.generatedAt)}. Automated validation accepted this
            version.</small
          >
        </p>{/if}
    </section>
  </article>
</div>

<style>
  .case-header {
    background: var(--blue-pale);
    border-bottom: 1px solid var(--line);
    padding-block: 2.8rem;
  }
  .case-header h1 {
    font-size: clamp(2rem, 5vw, 3.6rem);
  }
  .case-line {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
  }
  .case-layout {
    display: grid;
    gap: clamp(2rem, 6vw, 5rem);
    grid-template-columns: 190px minmax(0, 740px);
    justify-content: center;
    padding-top: 2.5rem;
  }
  .case-nav nav {
    display: grid;
    gap: 0.55rem;
    position: sticky;
    top: 1rem;
  }
  .case-nav a {
    color: var(--muted);
    font-size: 0.82rem;
  }
  .ai-note {
    background: var(--gold-pale);
    border-left: 4px solid #ba8a21;
    padding: 1rem 1.2rem;
  }
  .ai-note p {
    font-size: 0.9rem;
    margin: 0.35rem 0 0;
  }
  .overview {
    padding-top: 1.4rem;
  }
  .overview h2 {
    font-size: 2.35rem;
    margin-top: 0.1rem;
  }
  .overview > p:not(.eyebrow) {
    font-family: Georgia, serif;
    font-size: 1.2rem;
  }
  .argument {
    border-left: 3px solid var(--line);
    margin: 1.5rem 0;
    padding-left: 1.2rem;
  }
  .argument p {
    font-family: Georgia, serif;
    font-size: 1.08rem;
  }
  .glossary div {
    border-bottom: 1px solid var(--line);
    padding: 1rem 0;
  }
  .glossary dt {
    color: var(--navy);
    font-weight: 700;
  }
  .glossary dd {
    margin: 0.3rem 0;
  }
  .source-list {
    list-style: none;
    padding: 0;
  }
  .source-list li {
    align-items: center;
    border-bottom: 1px solid var(--line);
    display: flex;
    gap: 1rem;
    justify-content: space-between;
    padding: 1rem 0;
    scroll-margin-top: 1rem;
  }
  .source-list small {
    color: var(--muted);
    display: block;
    text-transform: capitalize;
  }
  .source-actions {
    display: flex;
    flex-shrink: 0;
    gap: 0.8rem;
  }
  @media (max-width: 760px) {
    .case-layout {
      grid-template-columns: 1fr;
    }
    .case-nav {
      display: none;
    }
    .source-list li {
      align-items: flex-start;
      flex-direction: column;
    }
  }
</style>
