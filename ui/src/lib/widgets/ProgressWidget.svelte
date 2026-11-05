<script lang="ts">
  import type { ProgressSchema } from '../types'

  let { schema, value }: { schema: ProgressSchema; value: unknown } = $props()

  // null or missing means the amount is unknown: <progress> without a value animates.
  const fraction = $derived(typeof value === 'number' ? Math.min(Math.max(value, 0), 1) : undefined)
</script>

<div class="progress">
  <div class="head">
    <span class="name">{schema.label}</span>
    <output>{fraction === undefined ? '—' : `${Math.round(fraction * 100)}%`}</output>
  </div>
  <progress max="1" value={fraction} aria-label={schema.label}></progress>
</div>

<style>
  .head {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
  }
  output {
    font-variant-numeric: tabular-nums;
    color: var(--muted);
  }
  progress {
    width: 100%;
    height: 0.6rem;
    accent-color: var(--accent);
  }
</style>
