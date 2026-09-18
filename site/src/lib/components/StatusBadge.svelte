<script lang="ts">
  import type { Lifecycle } from '$lib/types';

  let { status }: { status: Lifecycle } = $props();
  const labels: Record<Lifecycle, string> = {
    pending: 'Pending',
    scheduled: 'Scheduled',
    argued: 'Argued',
    'awaiting-decision': 'Awaiting decision',
    decided: 'Decided',
    unresolved: 'Status unresolved'
  };
</script>

<span class:decided={status === 'decided'} class="status">{labels[status]}</span>

<style>
  .status {
    display: inline-block;
    border: 1px solid var(--line);
    border-radius: 999px;
    color: var(--muted);
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.035em;
    padding: 0.28rem 0.62rem;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .decided {
    background: var(--green-pale);
    border-color: #8cac9b;
    color: #24523a;
  }
</style>
