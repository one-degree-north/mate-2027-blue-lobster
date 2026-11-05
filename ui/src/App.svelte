<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { Dock } from './lib/dock'
  import { HubConnection } from './lib/hub.svelte'
  import ModuleMenu from './lib/ModuleMenu.svelte'
  import SourceChip from './lib/SourceChip.svelte'

  const hub = new HubConnection(['modules/*', 'sources/*', 'video/*'])

  let host: HTMLDivElement
  let dock = $state<Dock>()

  onMount(() => {
    dock = new Dock(host, hub)
  })

  // Create panels as modules announce themselves.
  $effect(() => {
    dock?.sync(hub.moduleIds)
  })

  // Drop saved panels for modules that were renamed or removed since the layout was saved.
  $effect(() => {
    const known = hub.knownModules()
    if (known) dock?.prune(known)
  })

  onDestroy(() => {
    dock?.dispose()
    hub.close()
  })
</script>

<div class="app">
  <header>
    <strong>MATE ROV</strong>
    <span class="status" data-status={hub.status}>hub: {hub.status}</span>
    {#each hub.sourceIds as id (id)}
      <SourceChip {hub} {id} />
    {/each}
    <span class="spacer"></span>
    <ModuleMenu {hub} {dock} />
    <button type="button" onclick={() => dock?.resetLayout()}>Reset layout</button>
  </header>
  <div class="dock" bind:this={host}></div>
</div>

<style>
  .app {
    display: flex;
    flex-direction: column;
    height: 100%;
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0.75rem;
    background: var(--panel);
    border-bottom: 1px solid #2a323f;
  }
  .spacer {
    flex: 1;
  }
  .status {
    font-size: 0.8rem;
    color: var(--muted);
  }
  .status[data-status='open'] {
    color: var(--ok);
  }
  .status[data-status='closed'] {
    color: var(--bad);
  }
  .dock {
    flex: 1;
    min-height: 0;
  }
</style>
