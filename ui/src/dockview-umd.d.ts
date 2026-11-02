// dockview-core 8.x ships no standalone CSS file; only this UMD build injects its styles
// (the ESM entry does not). Reuse the package's own typings for it.
declare module 'dockview-core/dist/dockview-core.js' {
  export * from 'dockview-core'
}
