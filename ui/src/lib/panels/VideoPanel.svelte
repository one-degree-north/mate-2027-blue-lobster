<script lang="ts">
  import { onMount } from 'svelte'
  import { HUB_HTTP_URL, type HubConnection } from '../hub.svelte'
  import type { Overlay } from '../types'

  let { hub }: { hub: HubConnection } = $props()

  const RETRY_MS = 1000

  let chosen = $state<string | null>(null)
  let retry = $state(0)
  let box: HTMLDivElement
  let canvas: HTMLCanvasElement
  let size = $state({ width: 0, height: 0 })
  let natural = $state({ width: 0, height: 0 })

  const streams = $derived(hub.videoStreams)
  const current = $derived(
    chosen && streams.includes(chosen)
      ? chosen
      : streams.includes('main')
        ? 'main'
        : (streams[0] ?? null),
  )
  // A new epoch (hub reconnected) or retry restarts the HTTP stream, which doesn't resume itself.
  const src = $derived(
    current ? `${HUB_HTTP_URL}/video/${current}/stream.mjpg?e=${hub.epoch}.${retry}` : null,
  )
  const overlay = $derived(
    current ? (hub.topics[`video/${current}/overlay`] as Overlay | undefined) : undefined,
  )

  onMount(() => {
    const observer = new ResizeObserver(() => {
      size = { width: box.clientWidth, height: box.clientHeight }
    })
    observer.observe(box)
    return () => observer.disconnect()
  })

  $effect(() => {
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.round(size.width * dpr)
    canvas.height = Math.round(size.height * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, size.width, size.height)
    if (!overlay?.boxes || natural.width === 0) return

    // The <img> letterboxes the frame; map normalized boxes onto the visible picture.
    const scale = Math.min(size.width / natural.width, size.height / natural.height)
    const w = natural.width * scale
    const h = natural.height * scale
    const left = (size.width - w) / 2
    const top = (size.height - h) / 2
    ctx.lineWidth = 2
    ctx.strokeStyle = '#ffd24a'
    ctx.fillStyle = '#ffd24a'
    ctx.font = '12px system-ui, sans-serif'
    for (const b of overlay.boxes) {
      ctx.strokeRect(left + b.x * w, top + b.y * h, b.w * w, b.h * h)
      if (b.label) ctx.fillText(b.label, left + b.x * w + 3, top + b.y * h - 4)
    }
  })

  function onLoad(event: Event) {
    const img = event.currentTarget as HTMLImageElement
    natural = { width: img.naturalWidth, height: img.naturalHeight }
  }

  function onError() {
    setTimeout(() => retry++, RETRY_MS)
  }
</script>

<div class="video" bind:this={box}>
  {#if src}
    <img
      {src}
      alt="Live video from {current}"
      onload={onLoad}
      onerror={onError}
    />
  {:else}
    <p class="empty">No video stream</p>
  {/if}
  <canvas bind:this={canvas}></canvas>
  {#if streams.length > 1}
    <select
      value={current}
      onchange={(event) => (chosen = event.currentTarget.value)}
      aria-label="Video source"
    >
      {#each streams as name (name)}
        <option value={name}>{name}</option>
      {/each}
    </select>
  {/if}
</div>

<style>
  .video {
    position: relative;
    width: 100%;
    height: 100%;
    background: #000;
  }
  img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }
  canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }
  .empty {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: #888;
    margin: 0;
  }
  select {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
  }
</style>
