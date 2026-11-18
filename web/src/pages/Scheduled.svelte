<script lang="ts">
  import { onMount } from 'svelte'
  import { api, chatPath, editScheduled, unschedule, type Scheduled } from '../lib/api'
  import { when } from '../lib/time'
  import WhenPicker from '../lib/WhenPicker.svelte'
  import type { Picker } from '../lib/Picker'

  let items = $state<Scheduled[] | null>(null)
  let error = $state('')
  let picker = $state<Picker | null>(null)

  onMount(() => api<Scheduled[]>('/api/scheduled').then((s) => (items = s), (e) => (error = e.message)))

  const edit = (s: Scheduled) =>
    (picker = {
      title: `Edit message to ${s.chat_title}`,
      action: 'Save',
      withText: true,
      text: s.text,
      at: s.send_at,
      onsubmit: async (at, text) => {
        const updated = await editScheduled(s.id, text, at)
        items = items!.map((x) => (x.id === s.id ? updated : x)).sort((a, b) => a.send_at - b.send_at)
      },
    })

  async function remove(s: Scheduled) {
    if (!confirm(`Unschedule this message to ${s.chat_title}?`)) return
    try {
      await unschedule(s.id)
      items = items!.filter((x) => x.id !== s.id)
    } catch (e) {
      error = (e as Error).message
    }
  }
</script>

<h1>Scheduled</h1>
{#if error}<p class="error" role="alert">{error}</p>{/if}
<ul class="list">
  {#each items ?? [] as s (s.id)}
    <li class="card">
      <div class="line">
        <a href={`#${chatPath(s.chat_id)}`}><strong>{s.chat_title}</strong></a>
        <time>🕒 {when(s.send_at)}</time>
      </div>
      <p class="text">{s.text}</p>
      {#if s.error}<p class="error">Failed: {s.error}. Edit it to retry.</p>{/if}
      <div class="actions">
        <span class="meta">{s.network}</span>
        <button onclick={() => edit(s)}>Edit</button>
        <button class="danger" onclick={() => remove(s)}>Unschedule</button>
      </div>
    </li>
  {:else}
    {#if items}<li class="empty">No scheduled messages.</li>{/if}
  {/each}
</ul>

{#if picker}<WhenPicker {...picker} onclose={() => (picker = null)} />{/if}
