<script lang="ts">
  import type { NumberSchema } from '../types'

  let {
    schema,
    value,
    send,
    disabled,
  }: { schema: NumberSchema; value: unknown; send: (value: number) => void; disabled: boolean } =
    $props()

  // What the user is typing, until they press Enter or leave the field.
  let draft = $state<string | null>(null)

  const shown = $derived(draft ?? (typeof value === 'number' ? String(value) : ''))

  function commit() {
    if (draft === null) return
    const typed = draft.trim()
    draft = null // from here the module's own value (clamped, if need be) is shown
    if (typed === '') return
    const number = Number(typed)
    if (Number.isFinite(number)) send(number)
  }
</script>

<label class="number">
  <span class="name">{schema.label}</span>
  <span class="field">
    <input
      type="number"
      min={schema.min ?? undefined}
      max={schema.max ?? undefined}
      step={schema.step ?? 'any'}
      value={shown}
      {disabled}
      oninput={(event) => (draft = event.currentTarget.value)}
      onchange={commit}
      onblur={commit}
    />
    {#if schema.unit}<span class="unit">{schema.unit}</span>{/if}
  </span>
</label>

<style>
  .number {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
  }
  .field {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }
  input {
    width: 9rem;
    font: inherit;
    font-variant-numeric: tabular-nums;
    color: var(--fg);
    background: var(--chip);
    border: 1px solid #3a4454;
    border-radius: 4px;
    padding: 0.25rem 0.5rem;
    text-align: right;
  }
  .unit {
    color: var(--muted);
  }
</style>
