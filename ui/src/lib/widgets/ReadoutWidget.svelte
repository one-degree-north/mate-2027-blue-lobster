<script lang="ts">
  import type { ReadoutSchema } from '../types'

  let { schema, value }: { schema: ReadoutSchema; value: unknown } = $props()

  const text = $derived.by(() => {
    if (value === null || value === undefined) return '—'
    if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2)
    return typeof value === 'string' ? value : JSON.stringify(value)
  })
</script>

<div class="readout">
  <span class="name">{schema.label}</span>
  <output>{text}{schema.unit && typeof value === 'number' ? ` ${schema.unit}` : ''}</output>
</div>

<style>
  .readout {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
  }
  output {
    font-variant-numeric: tabular-nums;
    text-align: right;
    overflow-wrap: anywhere;
  }
</style>
