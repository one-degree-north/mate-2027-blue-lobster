/** Dockview layout: video in the centre, one auto-generated panel per module. */

import {
  createDockview,
  themeDark,
  type DockviewApi,
  type IContentRenderer,
} from 'dockview-core/dist/dockview-core.js'
import { mount, unmount } from 'svelte'
import type { HubConnection } from './hub.svelte'
import ModulePanel from './panels/ModulePanel.svelte'
import VideoPanel from './panels/VideoPanel.svelte'

// Bump when the saved layout format changes incompatibly.
const LAYOUT_KEY = 'rovkit.layout.v1'
const SEEN_KEY = 'rovkit.seenModules.v1'
const VIDEO_PANEL = 'video'
const SIDE_PANEL_WIDTH = 340

const panelId = (moduleId: string) => `module:${moduleId}`

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null // private mode or storage disabled: just don't persist
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* not persisting */
  }
}

export class Dock {
  readonly api: DockviewApi
  readonly #hub: HubConnection
  #seen: Set<string>
  #saveTimer: ReturnType<typeof setTimeout> | undefined

  constructor(element: HTMLElement, hub: HubConnection) {
    this.#hub = hub
    this.#seen = new Set(this.#loadSeen())

    this.api = createDockview(element, {
      theme: themeDark,
      createComponent: ({ name }) => this.#createRenderer(name),
    })

    const saved = readStorage(LAYOUT_KEY)
    if (saved) {
      try {
        this.api.fromJSON(JSON.parse(saved))
      } catch (error) {
        console.warn('discarding unreadable saved layout', error)
        this.api.clear()
      }
    }
    this.#ensureVideo()
    this.api.onDidLayoutChange(() => this.#scheduleSave())
  }

  /** Add a panel for each module not seen before. Closed panels stay closed. */
  sync(moduleIds: string[]): void {
    for (const id of moduleIds) {
      if (this.#seen.has(id)) continue
      this.#seen.add(id)
      writeStorage(SEEN_KEY, JSON.stringify([...this.#seen]))
      if (!this.api.getPanel(panelId(id))) this.#addModulePanel(id)
    }
  }

  isOpen(moduleId: string): boolean {
    return this.api.getPanel(panelId(moduleId)) !== undefined
  }

  /** Open a module's panel, or focus it if it is already open. */
  open(moduleId: string): void {
    const existing = this.api.getPanel(panelId(moduleId))
    if (existing) existing.api.setActive()
    else this.#addModulePanel(moduleId)
  }

  resetLayout(): void {
    writeStorage(LAYOUT_KEY, null)
    this.api.clear()
    this.#ensureVideo()
    for (const id of this.#hub.moduleIds) this.#addModulePanel(id)
  }

  dispose(): void {
    clearTimeout(this.#saveTimer)
    this.api.dispose()
  }

  #loadSeen(): string[] {
    try {
      const parsed: unknown = JSON.parse(readStorage(SEEN_KEY) ?? '[]')
      return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : []
    } catch {
      return []
    }
  }

  #ensureVideo(): void {
    if (this.api.getPanel(VIDEO_PANEL)) return
    this.api.addPanel({ id: VIDEO_PANEL, component: 'video', title: 'Video' })
  }

  #addModulePanel(moduleId: string): void {
    // Tab new panels into the first existing module group; otherwise dock right of the video.
    const sibling = this.api.panels.find((p) => p.id.startsWith('module:'))
    const title = this.#hub.schema(moduleId)?.title ?? moduleId
    this.api.addPanel({
      id: panelId(moduleId),
      component: 'module',
      title,
      params: { moduleId },
      position: sibling
        ? { referencePanel: sibling }
        : { referencePanel: VIDEO_PANEL, direction: 'right' },
      ...(sibling ? {} : { initialWidth: SIDE_PANEL_WIDTH }),
    })
  }

  #scheduleSave(): void {
    clearTimeout(this.#saveTimer)
    this.#saveTimer = setTimeout(
      () => writeStorage(LAYOUT_KEY, JSON.stringify(this.api.toJSON())),
      300,
    )
  }

  #createRenderer(name: string): IContentRenderer {
    const element = document.createElement('div')
    element.style.height = '100%'
    let instance: ReturnType<typeof mount> | undefined
    return {
      element,
      init: ({ params }) => {
        const hub = this.#hub
        instance =
          name === 'video'
            ? mount(VideoPanel, { target: element, props: { hub } })
            : mount(ModulePanel, {
                target: element,
                props: { hub, moduleId: String(params['moduleId']) },
              })
      },
      dispose: () => {
        if (instance) void unmount(instance)
      },
    }
  }
}
