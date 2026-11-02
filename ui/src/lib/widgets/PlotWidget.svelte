<script lang="ts">
  import uPlot from 'uplot'
  import 'uplot/dist/uPlot.min.css'
  import { onMount } from 'svelte'
  import type { HubConnection } from '../hub.svelte'
  import type { PlotSchema } from '../types'

  let {
    hub,
    moduleId,
    schema,
  }: { hub: HubConnection; moduleId: string; schema: PlotSchema } = $props()

  const HEIGHT = 120
  let container: HTMLDivElement
  let chart: uPlot | undefined
  let latest = $state<number | null>(null)

  const times: number[] = []
  const values: (number | null)[] = []

  onMount(() => {
    const styles = getComputedStyle(container)
    const axis = styles.getPropertyValue('--muted').trim() || '#888'
    const line = styles.getPropertyValue('--accent').trim() || '#4aa3ff'
    chart = new uPlot(
      {
        width: container.clientWidth || 300,
        height: HEIGHT,
        legend: { show: false },
        cursor: { show: false },
        scales: {
          x: { time: false },
          y: {
            range: (_, min, max) => [schema.yMin ?? min - 1, schema.yMax ?? max + 1],
          },
        },
        axes: [
          { show: false },
          { stroke: axis, size: 40, grid: { stroke: 'rgba(128,128,128,0.2)' } },
        ],
        series: [{}, { stroke: line, width: 2, points: { show: false } }],
      },
      [times, values],
      container,
    )

    const unlisten = hub.listen(`modules/${moduleId}/state/${schema.id}`, (data) => {
      const value = typeof data === 'number' ? data : null
      times.push(performance.now() / 1000)
      values.push(value)
      if (times.length > schema.window) {
        times.shift()
        values.shift()
      }
      latest = value
      chart?.setData([times, values])
    })

    const resize = new ResizeObserver(() => {
      if (container.clientWidth > 0) chart?.setSize({ width: container.clientWidth, height: HEIGHT })
    })
    resize.observe(container)

    return () => {
      unlisten()
      resize.disconnect()
      chart?.destroy()
    }
  })
</script>

<div class="plot">
  <div class="head">
    <span class="name">{schema.label}</span>
    <output
      >{latest === null ? '—' : latest.toFixed(2)}{schema.unit && latest !== null
        ? ` ${schema.unit}`
        : ''}</output
    >
  </div>
  <div bind:this={container}></div>
</div>

<style>
  .head {
    display: flex;
    justify-content: space-between;
  }
  output {
    font-variant-numeric: tabular-nums;
    color: var(--muted);
  }
</style>
