<script lang="ts">
  import type { HubConnection } from './hub.svelte'

  let { hub, id }: { hub: HubConnection; id: string } = $props()

  const status = $derived(hub.sourceStatus(id))
  const info = $derived(hub.sourceInfo(id))

  // The process state wins (a crashed source can't be "live"); otherwise show what it reports.
  const view = $derived.by(() => {
    if (status?.state === 'crashed') return { tone: 'bad', text: 'crashed' }
    if (status?.state !== 'running') return { tone: 'idle', text: status?.state ?? 'unknown' }
    switch (info?.state) {
      case 'live':
        return { tone: 'ok', text: info.fps === undefined ? 'live' : `live ${info.fps.toFixed(1)} fps` }
      case 'no signal':
        return { tone: 'bad', text: 'no signal' }
      case 'error':
        return { tone: 'bad', text: 'error' }
      default:
        return { tone: 'idle', text: info?.state ?? 'starting' }
    }
  })
  const detail = $derived(
    status?.state === 'crashed' ? status.tail.join('\n') : (info?.detail ?? ''),
  )
</script>

<span class="chip" data-tone={view.tone} title={detail}>
  <span class="dot"></span>{id}: {view.text}
</span>

<style>
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.1rem 0.55rem;
    border-radius: 1rem;
    background: var(--chip);
    font-size: 0.8rem;
  }
  .dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--muted);
  }
  .chip[data-tone='ok'] .dot {
    background: var(--ok);
  }
  .chip[data-tone='bad'] .dot {
    background: var(--bad);
  }
</style>
