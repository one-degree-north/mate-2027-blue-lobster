<script lang="ts">
  import type { Dock } from './dock'
  import type { HubConnection } from './hub.svelte'

  let { hub, dock }: { hub: HubConnection; dock: Dock | undefined } = $props()

  let open = $state(false)
  let root: HTMLDivElement
  let toggle: HTMLButtonElement

  const modules = $derived(
    hub.moduleIds
      .map((id) => ({
        id,
        title: hub.schema(id)?.title ?? id,
        state: hub.moduleStatus(id)?.state ?? 'unknown',
      }))
      .sort((a, b) => a.title.localeCompare(b.title)),
  )

  function choose(id: string) {
    dock?.open(id) // opens the panel, or brings it to the front if it is already open
    open = false
  }

  function onPointerDown(event: PointerEvent) {
    if (open && !root.contains(event.target as Node)) open = false
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === 'Escape' && open) {
      open = false
      toggle.focus()
    }
  }
</script>

<svelte:window onpointerdown={onPointerDown} onkeydown={onKeyDown} />

<div class="menu" bind:this={root}>
  <button
    type="button"
    bind:this={toggle}
    aria-haspopup="true"
    aria-expanded={open}
    onclick={() => (open = !open)}
  >
    Modules <span class="count">{modules.length}</span> ▾
  </button>

  {#if open}
    <div class="popover" role="menu">
      {#if modules.length === 0}
        <p class="empty">No modules yet</p>
      {/if}
      {#each modules as module (module.id)}
        <button type="button" role="menuitem" class="item" onclick={() => choose(module.id)}>
          <span class="dot" data-state={module.state} title={module.state}></span>
          <span class="title">{module.title}</span>
          <!-- Evaluated each time the menu opens, which is the only time it is visible. -->
          <span class="hint">{dock?.isOpen(module.id) ? '' : 'hidden'}</span>
        </button>
      {/each}
    </div>
  {/if}
</div>

<style>
  .menu {
    position: relative;
  }
  .count {
    color: var(--muted);
    margin: 0 0.15rem;
  }
  .popover {
    position: absolute;
    top: calc(100% + 0.3rem);
    right: 0;
    z-index: 1000; /* above the Dockview panels underneath */
    min-width: 16rem;
    max-height: 60vh;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    padding: 0.25rem;
    background: var(--panel);
    border: 1px solid #3a4454;
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  }
  .item {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    border: none;
    background: transparent;
    text-align: left;
    padding: 0.4rem 0.6rem;
  }
  .item:hover {
    background: var(--chip);
  }
  .title {
    flex: 1;
  }
  .hint,
  .empty {
    color: var(--muted);
    font-size: 0.8rem;
  }
  .empty {
    margin: 0;
    padding: 0.4rem 0.6rem;
  }
  .dot {
    width: 0.55rem;
    height: 0.55rem;
    border-radius: 50%;
    background: var(--muted);
  }
  .dot[data-state='running'] {
    background: var(--ok);
  }
  .dot[data-state='crashed'] {
    background: var(--bad);
  }
</style>
