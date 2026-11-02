<script lang="ts">
  import type { HubConnection } from '../hub.svelte'
  import ButtonWidget from '../widgets/ButtonWidget.svelte'
  import PlotWidget from '../widgets/PlotWidget.svelte'
  import ReadoutWidget from '../widgets/ReadoutWidget.svelte'
  import SliderWidget from '../widgets/SliderWidget.svelte'
  import ToggleWidget from '../widgets/ToggleWidget.svelte'

  let { hub, moduleId }: { hub: HubConnection; moduleId: string } = $props()

  const schema = $derived(hub.schema(moduleId))
  const status = $derived(hub.moduleStatus(moduleId))
  // Commands only make sense while the module's process is up and the hub is reachable.
  const live = $derived(hub.status === 'open' && status?.state === 'running')
</script>

<div class="module">
  <header>
    <span class="state" data-state={status?.state ?? 'unknown'}>{status?.state ?? 'unknown'}</span>
    {#if status && status.restarts > 0}
      <span class="restarts">{status.restarts} restart{status.restarts === 1 ? '' : 's'}</span>
    {/if}
  </header>

  {#if status?.state === 'crashed' && status.tail.length > 0}
    <pre class="crash">{status.tail.join('\n')}</pre>
  {/if}

  {#if schema}
    <div class="widgets" class:stale={!live}>
      {#each schema.widgets as widget (widget.id)}
        {#if widget.kind === 'slider'}
          <SliderWidget
            schema={widget}
            value={hub.widgetValue(moduleId, widget.id)}
            send={(v) => hub.command(moduleId, widget.id, v)}
            disabled={!live}
          />
        {:else if widget.kind === 'toggle'}
          <ToggleWidget
            schema={widget}
            value={hub.widgetValue(moduleId, widget.id)}
            send={(v) => hub.command(moduleId, widget.id, v)}
            disabled={!live}
          />
        {:else if widget.kind === 'button'}
          <ButtonWidget
            schema={widget}
            send={() => hub.command(moduleId, widget.id)}
            disabled={!live}
          />
        {:else if widget.kind === 'readout'}
          <ReadoutWidget schema={widget} value={hub.widgetValue(moduleId, widget.id)} />
        {:else if widget.kind === 'plot'}
          <PlotWidget {hub} {moduleId} schema={widget} />
        {/if}
      {/each}
    </div>
  {:else}
    <p class="empty">Waiting for module “{moduleId}”…</p>
  {/if}
</div>

<style>
  .module {
    padding: 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    height: 100%;
    overflow-y: auto;
    box-sizing: border-box;
  }
  header {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    font-size: 0.8rem;
  }
  .state {
    padding: 0.1rem 0.5rem;
    border-radius: 1rem;
    background: var(--chip);
  }
  .state[data-state='running'] {
    background: var(--ok);
    color: #fff;
  }
  .state[data-state='crashed'] {
    background: var(--bad);
    color: #fff;
  }
  .restarts,
  .empty {
    color: var(--muted);
  }
  .widgets {
    display: flex;
    flex-direction: column;
    gap: 0.9rem;
  }
  .widgets.stale :global(output) {
    opacity: 0.6;
  }
  .crash {
    margin: 0;
    padding: 0.5rem;
    background: var(--chip);
    border-radius: 4px;
    font-size: 0.75rem;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>
