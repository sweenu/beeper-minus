<script lang="ts">
  import Icon from '../lib/Icon.svelte'
  import { onMount } from 'svelte'
  import { api, chatPath, unsnooze, type Snooze } from '../lib/api'
  import { when } from '../lib/time'

  let snoozes = $state<Snooze[] | null>(null)
  let error = $state('')

  onMount(() => api<Snooze[]>('/api/snoozes').then((s) => (snoozes = s), (e) => (error = e.message)))

  async function wake(s: Snooze) {
    try {
      await unsnooze(s.chat_id)
      snoozes = snoozes!.filter((x) => x.chat_id !== s.chat_id)
    } catch (e) {
      error = (e as Error).message
    }
  }
</script>

<h1>Snoozed</h1>
{#if error}<p class="error" role="alert">{error}</p>{/if}
<ul class="list">
  {#each snoozes ?? [] as s (s.chat_id)}
    <li class="chat">
      <a class="row" href={`#${chatPath(s.chat_id)}`}>
        <span class="avatar" aria-hidden="true">{[...(s.chat_title ?? '?')][0]}</span>
        <span class="body">
          <strong class="title">{s.chat_title}</strong>
          <span class="meta">{s.network} · <Icon name="snooze" size={13} /> until {when(s.until)}</span>
        </span>
      </a>
      <button onclick={() => wake(s)}>Unsnooze</button>
    </li>
  {:else}
    {#if snoozes}<li class="empty">Nothing snoozed.</li>{/if}
  {/each}
</ul>
