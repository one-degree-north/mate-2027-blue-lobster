/** Shapes published by rovkit modules; keep in sync with src/rovkit/widgets.py. */

interface WidgetBase {
  id: string
  label: string
}

export interface ButtonSchema extends WidgetBase {
  kind: 'button'
}

export interface SliderSchema extends WidgetBase {
  kind: 'slider'
  min: number
  max: number
  step: number | null
  unit: string | null
}

export interface ToggleSchema extends WidgetBase {
  kind: 'toggle'
}

export interface ReadoutSchema extends WidgetBase {
  kind: 'readout'
  unit: string | null
}

export interface PlotSchema extends WidgetBase {
  kind: 'plot'
  unit: string | null
  window: number
  yMin: number | null
  yMax: number | null
}

export interface ProgressSchema extends WidgetBase {
  kind: 'progress'
}

export type WidgetSchema =
  | ButtonSchema
  | SliderSchema
  | ToggleSchema
  | ReadoutSchema
  | ProgressSchema
  | PlotSchema

export interface ModuleSchema {
  id: string
  title: string
  widgets: WidgetSchema[]
}

export interface ModuleStatus {
  state: 'starting' | 'running' | 'crashed' | 'stopped'
  restarts: number
  tail: string[]
  retryIn?: number
}

/** What a video source reports about itself (`Source.report`). */
export interface SourceInfo {
  state: string
  fps?: number
  detail?: string
  code?: number | null
}

export interface OverlayBox {
  x: number
  y: number
  w: number
  h: number
  label?: string
}

export interface Overlay {
  boxes: OverlayBox[]
}
