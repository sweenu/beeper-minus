<script lang="ts">
  import Icon from '../lib/Icon.svelte'
  import { onMount } from 'svelte'
  import { api, beeper, chatPath, schedule, snooze, type Chat, type Page, type Snooze } from '../lib/api'
  import { when } from '../lib/time'
  import { previewText } from '../lib/preview'
  import WhenPicker from '../lib/WhenPicker.svelte'
  import type { Picker } from '../lib/Picker'

  let { archived = false }: { archived?: boolean } = $props()

  let chats = $state<Chat[]>([])
  let snoozes = $state<Record<string, number>>({})
  let cursor = $state<string | undefined>()
  let hasMore = $state(false)
  let loading = $state(true)
  let error = $state('')
  let notice = $state('')
  let picker = $state<Picker | null>(null)

  async function load(more = false) {
    loading = true
    error = ''
    try {
      const page = await beeper<Page<Chat>>('/chats/search', {
        inbox: archived ? 'archive' : 'primary',
        limit: 50,
        ...(more && { cursor, direction: 'before' }),
      })
      // Merged chats list once; their member chats carry mergedIntoChatID.
      const items = page.items.filter((c) => !c.mergedIntoChatID)
      chats = more ? [...chats, ...items] : items
      cursor = page.oldestCursor
      hasMore = !!page.hasMore
    } catch (e) {
      error = (e as Error).message
    } finally {
      loading = false
    }
  }

  onMount(() => {
    load()
    api<Snooze[]>('/api/snoozes').then((s) => (snoozes = Object.fromEntries(s.map((x) => [x.chat_id, x.until]))), () => {})
  })

  const openSnooze = (c: Chat) =>
    (picker = {
      title: `Snooze ${c.title}`,
      action: 'Snooze',
      onsubmit: async (at) => {
        await snooze(c.id, at)
        if (archived) snoozes = { ...snoozes, [c.id]: at }
        else chats = chats.filter((x) => x.id !== c.id)
        notice = `${c.title} snoozed until ${when(at)}.`
      },
    })

  const openSchedule = (c: Chat) =>
    (picker = {
      title: `Send later to ${c.title}`,
      action: 'Schedule',
      withText: true,
      onsubmit: async (at, text) => {
        await schedule(c.id, text, at)
        notice = `Message to ${c.title} scheduled for ${when(at)}.`
      },
    })
</script>

<h1>{archived ? 'Archived' : 'Inbox'}</h1>
{#if notice}<p class="notice" role="status">{notice}</p>{/if}
{#if error}<p class="error" role="alert">{error} <button onclick={() => load()}>Retry</button></p>{/if}

<ul class="list">
  {#each chats as c (c.id)}
    <li class="chat">
      <a class="row" href={`#${chatPath(c.id)}`}>
        <span class="avatar" aria-hidden="true">{[...c.title][0] ?? '?'}</span>
        <span class="body">
          <span class="line">
            <strong class="title">{c.title}</strong>
            {#if c.lastActivity}<time>{when(c.lastActivity)}</time>{/if}
          </span>
          <span class="line">
            <span class="muted ellipsis">{previewText(c.preview)}</span>
            {#if c.unreadCount}<span class="badge">{c.unreadCount}</span>{/if}
          </span>
          <span class="meta">
            {c.network}{#if snoozes[c.id]} · <Icon name="snooze" size={13} /> until {when(snoozes[c.id])}{/if}
          </span>
        </span>
      </a>
      <button class="icon" title="Send later" aria-label={`Send later to ${c.title}`} onclick={() => openSchedule(c)}><Icon name="later" /></button>
      <button class="icon" title="Snooze" aria-label={`Snooze ${c.title}`} onclick={() => openSnooze(c)}><Icon name="snooze" /></button>
    </li>
  {:else}
    {#if !loading && !error}<li class="empty">No chats.</li>{/if}
  {/each}
</ul>
{#if loading}<p class="muted center">Loading…</p>{/if}
{#if hasMore && !loading}<button class="more" onclick={() => load(true)}>Load more</button>{/if}

{#if picker}<WhenPicker {...picker} onclose={() => (picker = null)} />{/if}
