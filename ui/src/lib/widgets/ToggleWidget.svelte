<script lang="ts">
  import type { ToggleSchema } from '../types'

  let {
    schema,
    value,
    send,
    disabled,
  }: { schema: ToggleSchema; value: unknown; send: (value: boolean) => void; disabled: boolean } =
    $props()
</script>

<label class="toggle">
  <input
    type="checkbox"
    role="switch"
    checked={value === true}
    {disabled}
    onchange={(event) => send(event.currentTarget.checked)}
  />
  <span>{schema.label}</span>
</label>

<style>
  .toggle {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  input {
    accent-color: var(--accent);
  }
</style>
