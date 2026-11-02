<script lang="ts">
  import type { SliderSchema } from '../types'

  let {
    schema,
    value,
    send,
    disabled,
  }: { schema: SliderSchema; value: unknown; send: (value: number) => void; disabled: boolean } =
    $props()

  const SEND_INTERVAL_MS = 50

  // While dragging, show the local value so the module's echo can't fight the pointer.
  let draft = $state<number | null>(null)
  let lastSent = 0
  let trailing: ReturnType<typeof setTimeout> | undefined

  const shown = $derived(draft ?? (typeof value === 'number' ? value : schema.min))
  const decimals = $derived(
    schema.step === null ? 2 : Math.max(0, -Math.floor(Math.log10(schema.step))),
  )

  function onInput(event: Event & { currentTarget: HTMLInputElement }) {
    draft = event.currentTarget.valueAsNumber
    const wait = SEND_INTERVAL_MS - (performance.now() - lastSent)
    clearTimeout(trailing)
    if (wait <= 0) flush()
    else trailing = setTimeout(flush, wait)
  }

  function flush() {
    if (draft === null) return
    lastSent = performance.now()
    send(draft)
  }

  function onRelease() {
    clearTimeout(trailing)
    flush()
    draft = null
  }
</script>

<label class="slider">
  <span class="name">{schema.label}</span>
  <output>{shown.toFixed(decimals)}{schema.unit ? ` ${schema.unit}` : ''}</output>
  <input
    type="range"
    min={schema.min}
    max={schema.max}
    step={schema.step ?? 'any'}
    value={shown}
    {disabled}
    oninput={onInput}
    onchange={onRelease}
    onpointerup={onRelease}
    onblur={onRelease}
  />
</label>

<style>
  .slider {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 0.25rem 0.5rem;
  }
  input {
    grid-column: 1 / -1;
    width: 100%;
    accent-color: var(--accent);
  }
  output {
    font-variant-numeric: tabular-nums;
    color: var(--muted);
  }
</style>
