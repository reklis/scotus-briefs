<script lang="ts">
  import Seo from '$lib/components/Seo.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import type { PublicCase, TermSummary } from '$lib/types';

  let {
    data
  }: {
    data: {
      latest?: TermSummary;
      latestCases: PublicCase[];
      recentCases: PublicCase[];
      totalCases: number;
    };
  } = $props();
</script>

<Seo
  title="SCOTUS Briefs"
  description="Independent, source-cited plain-language guides to U.S. Supreme Court cases."
/>
<section class="hero">
  <div class="shell">
    <p class="eyebrow" style="color: #f4c06b">A citizen's guide to the Supreme Court</p>
    <h1>Understand the cases shaping public life.</h1>
    <p class="lede">
      Explore plain-language explanations of what each case is about, what each side argues, what
      the Court decides, and why it matters.
    </p>
    <div class="actions">
      <a class="button" href="/search/">Search cases</a>
      <a class="button ghost" href={data.latest ? `/terms/${data.latest.term}/` : '/terms/'}>
        Browse {data.latest ? `${data.latest.term} term` : 'by term'}
      </a>
    </div>
  </div>
</section>
<section class="shell page-content intro" aria-labelledby="how-heading">
  <div>
    <p class="eyebrow">Start with the essentials</p>
    <h2 id="how-heading">Primary sources, translated carefully</h2>
    <p>
      Each published guide is built from Court documents and keeps citations close at hand. Pending
      cases and gaps in the available record are labeled rather than guessed at.
    </p>
  </div>
  <aside class="disclosure">
    <strong>Know what you're reading</strong>
    <p>
      Explanations are generated with AI and automatically checked against cited sources. They are
      independent educational summaries—not Court publications or legal advice.
    </p>
    <a href="/about/">Read about the process</a>
  </aside>
</section>
<section class="shell page-content" aria-labelledby="latest-heading">
  <p class="eyebrow">{data.latest ? `${data.latest.term} term` : 'Case catalog'}</p>
  <h2 id="latest-heading">
    {data.totalCases
      ? `Explore the ${data.latest?.term ?? 'latest'} term`
      : 'The catalog is being prepared'}
  </h2>
  {#if data.latestCases.length}
    <div class="card-grid">
      {#each data.latestCases as item}
        <article class="card">
          <div class="card-top">
            <span class="dockets">{item.dockets.join(' · ')}</span><StatusBadge
              status={item.lifecycle}
            />
          </div>
          <a class="card-link" href={`/cases/${item.slug}/`}><h3>{item.title}</h3></a>
          <p class="muted">
            {item.guide?.overview.paragraphs[0] ?? 'Guide preparation is pending.'}
          </p>
        </article>
      {/each}
    </div>
    <p><a class="button secondary" href={`/terms/${data.latest?.term}/`}>View the full term</a></p>
  {:else}
    <div class="notice">
      Verified case records and guides will appear here as they are accepted. You can still learn
      <a href="/about/">how the project works</a>.
    </div>
  {/if}
</section>
{#if data.recentCases.length}
  <section class="shell page-content" aria-labelledby="recent-heading">
    <p class="eyebrow">From the record</p>
    <h2 id="recent-heading">Recently updated cases</h2>
    <ul class="recent-list">
      {#each data.recentCases as item}
        <li>
          <span><strong class="dockets">{item.dockets.join(' · ')}</strong> · {item.term} term</span
          ><a href={`/cases/${item.slug}/`}>{item.title}</a>
        </li>
      {/each}
    </ul>
  </section>
{/if}
<section class="status-help">
  <div class="shell narrow">
    <p class="eyebrow">Reading case status</p>
    <h2>Cases change as the Court's work progresses</h2>
    <dl>
      <div>
        <dt>Scheduled or argued</dt>
        <dd>The Court has not issued a decision. No outcome is predicted.</dd>
      </div>
      <div>
        <dt>Awaiting decision</dt>
        <dd>Oral argument may be available, but the decision section remains pending.</dd>
      </div>
      <div>
        <dt>Decided</dt>
        <dd>A current Court opinion supports the decision explanation.</dd>
      </div>
      <div>
        <dt>Source-limited</dt>
        <dd>Available materials do not yet support a complete section.</dd>
      </div>
    </dl>
  </div>
</section>

<style>
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.8rem;
    margin-top: 1.7rem;
  }
  .ghost {
    background: transparent;
    border-color: #fff;
  }
  .intro {
    align-items: start;
    display: grid;
    gap: clamp(2rem, 6vw, 5rem);
    grid-template-columns: 1.3fr 0.8fr;
  }
  .disclosure {
    background: var(--gold-pale);
    border-top: 4px solid #ba8a21;
    padding: 1.4rem;
  }
  .card-top {
    align-items: center;
    display: flex;
    justify-content: space-between;
  }
  .recent-list {
    list-style: none;
    padding: 0;
  }
  .recent-list li {
    border-bottom: 1px solid var(--line);
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(10rem, 0.35fr) 1fr;
    padding: 1rem 0;
  }
  .recent-list li > span {
    color: var(--muted);
    font-size: 0.84rem;
  }
  .status-help {
    background: var(--blue-pale);
    padding-block: 3rem;
  }
  .status-help dl {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(2, 1fr);
  }
  .status-help dt {
    font-weight: 700;
  }
  .status-help dd {
    color: var(--muted);
    margin: 0.2rem 0;
  }
  @media (max-width: 650px) {
    .intro,
    .status-help dl,
    .recent-list li {
      grid-template-columns: 1fr;
    }
  }
</style>
