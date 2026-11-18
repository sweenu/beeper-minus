<script lang="ts">
  import { onMount, tick } from 'svelte'
  import {
    api, assetURL, beeper, config, schedule, snooze, transcribe, unschedule,
    type Chat, type Message, type Page, type Scheduled, type Transcription,
  } from '../lib/api'
  import { duration, when } from '../lib/time'
  import WhenPicker from '../lib/WhenPicker.svelte'
  import type { Picker } from '../lib/Picker'

  let { id }: { id: string } = $props()

  let chat = $state<Chat | null>(null)
  let messages = $state<Message[]>([])
  let transcripts = $state<Record<string, Transcription>>({})
  let pending = $state<Record<string, string>>({}) // message key -> "busy" or an error
  let scheduled = $state<Scheduled[]>([])
  let canTranscribe = $state(false)
  let cursor = $state<string | undefined>()
  let hasMore = $state(false)
  let draft = $state('')
  let sending = $state(false)
  let error = $state('')
  let notice = $state('')
  let picker = $state<Picker | null>(null)

  const enc = encodeURIComponent
  const key = (m: { chatID: string; id: string }) => `${m.chatID}/${m.id}`
  // A merged chat holds no messages itself: read its member chats.
  const members = $derived(chat?.merge?.chatIDs ?? [id])

  function merge(list: Message[]) {
    const byId = new Map(messages.map((m) => [key(m), m]))
    for (const m of list) byId.set(key(m), m)
    messages = [...byId.values()].filter((m) => m.type !== 'REACTION').sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  }

  async function refresh() {
    const pages = await Promise.all(members.map((m) => beeper<Page<Message>>(`/chats/${enc(m)}/messages`)))
    merge(pages.flatMap((p) => p.items))
    const ts = await Promise.all(members.map((m) => api<Transcription[]>(`/api/transcriptions?chatID=${enc(m)}`)))
    transcripts = Object.fromEntries(ts.flat().map((t) => [`${t.chat_id}/${t.message_id}`, t]))
    scheduled = (await api<Scheduled[]>('/api/scheduled')).filter((s) => s.chat_id === id)
    return pages
  }

  async function older() {
    const page = await beeper<Page<Message>>(`/chats/${enc(id)}/messages`, { cursor, direction: 'before' })
    merge(page.items)
    cursor = page.oldestCursor
    hasMore = !!page.hasMore
  }

  onMount(() => {
    let stop = false
    ;(async () => {
      try {
        chat = await beeper<Chat>(`/chats/${enc(id)}`)
        const pages = await refresh()
        // shortcut: merged chats only show each member's latest page; page per member if older history matters.
        if (members.length === 1) {
          cursor = pages[0].oldestCursor
          hasMore = !!pages[0].hasMore
        }
        canTranscribe = (await config).transcription
        await tick()
        window.scrollTo(0, document.body.scrollHeight)
      } catch (e) {
        error = (e as Error).message
      }
    })()
    const timer = setInterval(() => !stop && !document.hidden && chat && refresh().catch(() => {}), 10_000)
    return () => {
      stop = true
      clearInterval(timer)
    }
  })

  async function send(e: SubmitEvent) {
    e.preventDefault()
    if (!draft.trim()) return
    sending = true
    try {
      await api(`/api/beeper/v1/chats/${enc(id)}/messages`, { method: 'POST', body: { text: draft } })
      draft = ''
      await refresh()
      await tick()
      window.scrollTo(0, document.body.scrollHeight)
    } catch (err) {
      error = (err as Error).message
    } finally {
      sending = false
    }
  }

  async function requestTranscript(m: Message) {
    pending = { ...pending, [key(m)]: 'busy' }
    try {
      const t = await transcribe(m.chatID, m.id)
      transcripts = { ...transcripts, [key(m)]: t }
      const { [key(m)]: _, ...rest } = pending
      pending = rest
    } catch (err) {
      pending = { ...pending, [key(m)]: (err as Error).message }
    }
  }

  const later = () =>
    (picker = {
      title: `Send later to ${chat?.title ?? ''}`,
      action: 'Schedule',
      withText: true,
      text: draft,
      onsubmit: async (at, text) => {
        scheduled = [...scheduled, await schedule(id, text, at)]
        draft = ''
      },
    })

  const openSnooze = () =>
    (picker = {
      title: `Snooze ${chat?.title ?? ''}`,
      action: 'Snooze',
      onsubmit: async (at) => {
        await snooze(id, at)
        notice = `Snoozed until ${when(at)}.`
      },
    })

  async function cancel(s: Scheduled) {
    await unschedule(s.id)
    scheduled = scheduled.filter((x) => x.id !== s.id)
  }
</script>

<header class="bar">
  <a href={chat?.isArchived ? '#/archived' : '#/'} class="icon" aria-label="Back">←</a>
  <div class="ellipsis">
    <strong>{chat?.title ?? '…'}</strong>
    {#if chat}<div class="meta">{chat.network}{chat.isArchived ? ' · archived' : ''}</div>{/if}
  </div>
  <button class="icon" title="Snooze" aria-label="Snooze this chat" onclick={openSnooze}>💤</button>
</header>

{#if notice}<p class="notice" role="status">{notice} <a href="#/">Back to inbox</a></p>{/if}
{#if error}<p class="error" role="alert">{error}</p>{/if}
{#if hasMore}<button class="more" onclick={() => older().catch((e) => (error = e.message))}>Load older messages</button>{/if}

<ol class="messages">
  {#each messages as m (key(m))}
    <li class="msg" class:mine={m.isSender}>
      {#if !m.isSender && chat?.type === 'group'}<div class="sender">{m.senderName}</div>{/if}
      {#each m.attachments ?? [] as a, i (i)}
        {#if a.type === 'audio'}
          <audio controls preload="none" src={assetURL(a)}></audio>
          {@const t = transcripts[key(m)]}
          {#if t}
            <p class="transcript">{t.text || '(silence)'}<small> · {t.language} · {duration(t.duration)}</small></p>
          {:else if a.transcription}
            <p class="transcript">{a.transcription.transcription}</p>
          {:else if canTranscribe}
            {#if pending[key(m)] === 'busy'}
              <p class="muted">Transcribing…</p>
            {:else}
              <button class="link" onclick={() => requestTranscript(m)}>Transcribe</button>
              {#if pending[key(m)]}<p class="error">{pending[key(m)]}</p>{/if}
            {/if}
          {/if}
        {:else if a.type === 'img'}
          <img src={assetURL(a)} alt={a.fileName ?? 'image'} loading="lazy" />
        {:else if a.type === 'video'}
          <!-- svelte-ignore a11y_media_has_caption -->
          <video controls preload="none" src={assetURL(a)}></video>
        {:else}
          <a href={assetURL(a)} target="_blank" rel="noopener">📎 {a.fileName ?? 'File'}</a>
        {/if}
      {/each}
      {#if m.isDeleted}<p class="muted"><em>Deleted message</em></p>{:else if m.text}<p class="text">{m.text}</p>{/if}
      <time>{when(m.timestamp)}</time>
    </li>
  {/each}
</ol>

{#each scheduled as s (s.id)}
  <div class="msg mine scheduled">
    <p class="text">{s.text}</p>
    <time>🕒 {when(s.send_at)}</time>
    {#if s.error}<p class="error">Failed: {s.error}</p>{/if}
    <button class="link" onclick={() => cancel(s)}>Unschedule</button>
  </div>
{/each}

<form class="composer" onsubmit={send}>
  <textarea
    bind:value={draft}
    rows="1"
    placeholder="Message"
    aria-label="Message"
    onkeydown={(e) => e.key === 'Enter' && !e.shiftKey && !matchMedia('(pointer: coarse)').matches && (e.preventDefault(), e.currentTarget.form?.requestSubmit())}
  ></textarea>
  <button type="button" class="icon" title="Send later" aria-label="Send later" onclick={later}>🕒</button>
  <button class="primary" disabled={sending || !draft.trim()}>Send</button>
</form>

{#if picker}<WhenPicker {...picker} onclose={() => (picker = null)} />{/if}
