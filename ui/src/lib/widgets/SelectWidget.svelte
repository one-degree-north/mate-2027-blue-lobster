<script lang="ts">
  import type { SelectSchema } from '../types'

  let {
    schema,
    value,
    send,
    disabled,
  }: { schema: SelectSchema; value: unknown; send: (value: string) => void; disabled: boolean } =
    $props()

  const current = $derived(typeof value === 'string' ? value : (schema.options[0]?.value ?? ''))
</script>

<label class="select">
  <span class="name">{schema.label}</span>
  <select value={current} {disabled} onchange={(event) => send(event.currentTarget.value)}>
    {#each schema.options as option (option.value)}
      <option value={option.value}>{option.label}</option>
    {/each}
  </select>
</label>

<style>
  .select {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
  }
</style>
