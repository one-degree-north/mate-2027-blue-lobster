<script lang="ts">
  import type { StatusSchema } from '../types'

  let { schema, value }: { schema: StatusSchema; value: unknown } = $props()

  const status = $derived.by(() => {
    const v = value as { text?: unknown; tone?: unknown } | null | undefined
    return {
      text: typeof v?.text === 'string' && v.text !== '' ? v.text : '—',
      tone: typeof v?.tone === 'string' ? v.tone : 'info',
    }
  })
</script>

<div class="status">
  <span class="name">{schema.label}</span>
  <output data-tone={status.tone}>{status.text}</output>
</div>

<style>
  .status {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
  }
  output {
    text-align: right;
    overflow-wrap: anywhere;
  }
  output[data-tone='ok'] {
    color: var(--ok);
  }
  output[data-tone='warn'] {
    color: var(--warn);
  }
  output[data-tone='error'] {
    color: var(--bad);
  }
</style>
