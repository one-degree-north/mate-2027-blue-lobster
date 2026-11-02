<script lang="ts">
  import type { ButtonSchema } from '../types'

  let {
    schema,
    send,
    disabled,
  }: { schema: ButtonSchema; send: () => void; disabled: boolean } = $props()
</script>

<button type="button" {disabled} onclick={send}>{schema.label}</button>

<style>
  button {
    width: 100%;
  }
</style>
