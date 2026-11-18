<script lang="ts">
  import Icon from '../lib/Icon.svelte'
  import { onMount } from 'svelte'
  import { api, chatPath, type Transcription } from '../lib/api'
  import { duration, when } from '../lib/time'

  let items = $state<Transcription[] | null>(null)
  let error = $state('')

  onMount(() => api<Transcription[]>('/api/transcriptions?limit=100').then((t) => (items = t), (e) => (error = e.message)))
</script>

<h1>Transcripts</h1>
{#if error}<p class="error" role="alert">{error}</p>{/if}
<ul class="list">
  {#each items ?? [] as t (`${t.chat_id}/${t.message_id}`)}
    <li class="card">
      <div class="line">
        <a href={`#${chatPath(t.chat_id)}`}><strong>{t.sender ?? '?'}</strong>{#if t.chat_title !== t.sender}<span class="muted"> in {t.chat_title}</span>{/if}</a>
        <time>{when(t.sent_at)}</time>
      </div>
      <p class="text">{t.text || '(silence)'}</p>
      <span class="meta">{t.network} · <Icon name="mic" size={13} /> {duration(t.duration)} · {t.language}</span>
    </li>
  {:else}
    {#if items}<li class="empty">No transcripts yet.</li>{/if}
  {/each}
</ul>
