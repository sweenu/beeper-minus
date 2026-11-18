<script lang="ts">
  import { presets, toInput, when } from './time'

  let {
    title,
    action,
    withText = false,
    text = '',
    at = null,
    onsubmit,
    onclose,
  }: {
    title: string
    action: string
    withText?: boolean
    text?: string
    at?: number | null
    onsubmit: (at: number, text: string) => Promise<unknown>
    onclose: () => void
  } = $props()

  // The dialog only seeds its fields from the props it opens with.
  // svelte-ignore state_referenced_locally
  let value = $state(at ? toInput(at) : '')
  // svelte-ignore state_referenced_locally
  let body = $state(text)
  let error = $state('')
  let busy = $state(false)
  let dialog: HTMLDialogElement
  const choices = presets()

  $effect(() => dialog.showModal())

  async function submit(e: SubmitEvent) {
    e.preventDefault()
    const ms = new Date(value).getTime()
    if (!value || isNaN(ms)) return (error = 'Pick a date and time.')
    if (ms <= Date.now()) return (error = 'Pick a time in the future.')
    if (withText && !body.trim()) return (error = 'Write a message.')
    busy = true
    try {
      await onsubmit(ms, body)
      dialog.close()
    } catch (err) {
      error = (err as Error).message
    } finally {
      busy = false
    }
  }
</script>

<dialog bind:this={dialog} {onclose}>
  <form onsubmit={submit}>
    <h2>{title}</h2>
    {#if withText}
      <!-- svelte-ignore a11y_autofocus -->
      <textarea bind:value={body} rows="4" placeholder="Message" aria-label="Message" autofocus={!text}></textarea>
    {/if}
    <div class="presets">
      {#each choices as [label, d] (label)}
        <button type="button" class:active={value === toInput(d.getTime())} onclick={() => (value = toInput(d.getTime()))}>
          {label}<small>{when(d.getTime())}</small>
        </button>
      {/each}
    </div>
    <label>Custom <input type="datetime-local" bind:value /></label>
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    <div class="actions">
      <button type="button" onclick={() => dialog.close()}>Cancel</button>
      <button class="primary" disabled={busy}>{action}</button>
    </div>
  </form>
</dialog>
