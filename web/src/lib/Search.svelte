<script lang="ts">
  import { beeper, chatPath, type Chat, type Page } from './api'

  let query = $state('')
  let results = $state<Chat[]>([])
  let open = $state(false)
  let pending = $state(false)
  let active = $state(0)
  let error = $state('')
  let input: HTMLInputElement
  let timer: ReturnType<typeof setTimeout>
  let latest = 0
  const shown = $derived(open && !!query.trim())

  function search() {
    clearTimeout(timer)
    const q = query.trim()
    pending = !!q
    if (!q) return (results = [])
    timer = setTimeout(async () => {
      const mine = ++latest
      try {
        const page = await beeper<Page<Chat>>('/chats/search', { query: q, scope: 'titles', limit: 50 })
        if (mine !== latest) return // a newer search is on its way
        const words = q.toLowerCase().split(/\s+/)
        // Beeper's "titles" scope also matches network names: keep chats whose name has every word.
        results = page.items.filter((c) => !c.mergedIntoChatID && words.every((w) => c.title.toLowerCase().includes(w)))
        active = 0
        error = ''
      } catch (e) {
        if (mine === latest) error = (e as Error).message
      } finally {
        if (mine === latest) pending = false
      }
    }, 200)
  }

  function reset() {
    query = ''
    results = []
    open = false
    input.blur()
  }

  function keydown(e: KeyboardEvent) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      open = true
      active = Math.max(0, Math.min(results.length - 1, active + (e.key === 'ArrowDown' ? 1 : -1)))
    } else if (e.key === 'Enter' && shown && results[active]) {
      e.preventDefault()
      location.hash = `#${chatPath(results[active].id)}`
      reset()
    } else if (e.key === 'Escape') {
      reset()
    }
  }
</script>

<div class="search" onfocusout={(e) => !e.currentTarget.contains(e.relatedTarget as Node | null) && (open = false)}>
  <input
    bind:this={input}
    bind:value={query}
    type="search"
    placeholder="Search chats"
    aria-label="Search chats by name"
    role="combobox"
    aria-autocomplete="list"
    aria-controls="search-results"
    aria-expanded={shown}
    aria-activedescendant={shown && results[active] ? `search-${active}` : undefined}
    autocomplete="off"
    oninput={() => ((open = true), search())}
    onfocus={() => (open = true)}
    onkeydown={keydown}
  />
  {#if shown}
    <ul id="search-results" role="listbox" aria-label="Chats">
      {#each results as c, i (c.id)}
        <li id={`search-${i}`} role="option" aria-selected={i === active}>
          <a href={`#${chatPath(c.id)}`} tabindex="-1" onclick={reset} onpointermove={() => (active = i)}>
            <span class="avatar" aria-hidden="true">{[...c.title][0] ?? '?'}</span>
            <span class="body">
              <strong class="title">{c.title}</strong>
              <span class="meta">{c.network}{c.isArchived ? ' · archived' : ''}</span>
            </span>
          </a>
        </li>
      {:else}
        <li class="empty">{pending ? 'Searching…' : error || 'No chat with that name.'}</li>
      {/each}
    </ul>
  {/if}
</div>
