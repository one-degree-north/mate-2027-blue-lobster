/** Reactive WebSocket client for the rovkit hub. Reconnects and re-subscribes on its own. */

import type { ModuleSchema, ModuleStatus, SourceInfo } from './types'

export type ConnectionStatus = 'connecting' | 'open' | 'closed'

interface HubMessage {
  type: 'message'
  topic: string
  ts: number
  data: unknown
}

type Listener = (data: unknown) => void

// In dev the UI runs on Vite's port and the hub on 8765; in production the hub serves the UI.
const HUB_HOST: string =
  import.meta.env.VITE_HUB_HOST ??
  (import.meta.env.DEV ? `${location.hostname || 'localhost'}:8765` : location.host)
const SECURE = location.protocol === 'https:'

export const HUB_WS_URL = `${SECURE ? 'wss' : 'ws'}://${HUB_HOST}/ws`
export const HUB_HTTP_URL = `${SECURE ? 'https' : 'http'}://${HUB_HOST}`

const MIN_BACKOFF_MS = 250
const MAX_BACKOFF_MS = 5000
const SCHEMA_TOPIC = /^modules\/([a-z][a-z0-9_]*)\/schema$/
const META_TOPIC = /^video\/([a-z][a-z0-9_-]*)\/meta$/
const SOURCE_TOPIC = /^sources\/([a-z][a-z0-9_]*)\/status$/

function isHubMessage(value: unknown): value is HubMessage {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return v.type === 'message' && typeof v.topic === 'string' && typeof v.ts === 'number'
}

export class HubConnection {
  status = $state<ConnectionStatus>('connecting')
  /** Latest payload per topic. */
  topics = $state<Record<string, unknown>>({})
  /** Bumped on every (re)connect so views can reload things like the video stream. */
  epoch = $state(0)

  moduleIds = $derived(this.#matching(SCHEMA_TOPIC))
  videoStreams = $derived(this.#matching(META_TOPIC))
  sourceIds = $derived(this.#matching(SOURCE_TOPIC))

  readonly #patterns: string[]
  readonly #listeners = new Map<string, Set<Listener>>()
  #ws: WebSocket | null = null
  #backoff = MIN_BACKOFF_MS
  #retryTimer: ReturnType<typeof setTimeout> | undefined
  #closed = false

  constructor(patterns: string[]) {
    this.#patterns = patterns
    this.#connect()
  }

  schema(moduleId: string): ModuleSchema | undefined {
    return this.topics[`modules/${moduleId}/schema`] as ModuleSchema | undefined
  }

  moduleStatus(moduleId: string): ModuleStatus | undefined {
    return this.topics[`modules/${moduleId}/status`] as ModuleStatus | undefined
  }

  sourceStatus(sourceId: string): ModuleStatus | undefined {
    return this.topics[`sources/${sourceId}/status`] as ModuleStatus | undefined
  }

  sourceInfo(sourceId: string): SourceInfo | undefined {
    return this.topics[`sources/${sourceId}/info`] as SourceInfo | undefined
  }

  widgetValue(moduleId: string, widgetId: string): unknown {
    return this.topics[`modules/${moduleId}/state/${widgetId}`]
  }

  /** Send a click or new value to a module's widget. */
  command(moduleId: string, widgetId: string, data: unknown = null): void {
    this.publish(`modules/${moduleId}/cmd/${widgetId}`, data)
  }

  publish(topic: string, data: unknown = null, retain = false): void {
    if (this.#ws?.readyState !== WebSocket.OPEN) return
    this.#ws.send(JSON.stringify({ type: 'publish', topic, data, retain }))
  }

  /** Call `listener` for every message on `topic` (not just the latest). Returns an unsubscribe. */
  listen(topic: string, listener: Listener): () => void {
    let set = this.#listeners.get(topic)
    if (!set) this.#listeners.set(topic, (set = new Set()))
    set.add(listener)
    return () => set.delete(listener)
  }

  close(): void {
    this.#closed = true
    clearTimeout(this.#retryTimer)
    this.#ws?.close()
  }

  #matching(pattern: RegExp): string[] {
    const ids: string[] = []
    for (const topic of Object.keys(this.topics)) {
      const match = pattern.exec(topic)
      if (match?.[1]) ids.push(match[1])
    }
    return ids.sort()
  }

  #connect(): void {
    this.status = 'connecting'
    const ws = new WebSocket(HUB_WS_URL)
    this.#ws = ws

    ws.onopen = () => {
      this.#backoff = MIN_BACKOFF_MS
      this.status = 'open'
      this.epoch++
      ws.send(JSON.stringify({ type: 'subscribe', patterns: this.#patterns }))
    }

    ws.onmessage = (event: MessageEvent<unknown>) => {
      if (typeof event.data !== 'string') return
      let frame: unknown
      try {
        frame = JSON.parse(event.data)
      } catch {
        return
      }
      if (!isHubMessage(frame)) {
        console.warn('unexpected frame from hub', frame)
        return
      }
      // A retained null means the publisher cleared the topic (e.g. a video stream closed).
      if (frame.data === null) delete this.topics[frame.topic]
      else this.topics[frame.topic] = frame.data
      this.#listeners.get(frame.topic)?.forEach((listener) => listener(frame.data))
    }

    ws.onclose = () => {
      this.status = 'closed'
      if (this.#closed) return
      this.#retryTimer = setTimeout(() => this.#connect(), this.#backoff)
      this.#backoff = Math.min(this.#backoff * 2, MAX_BACKOFF_MS)
    }
  }
}
