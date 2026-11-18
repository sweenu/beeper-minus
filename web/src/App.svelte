<script lang="ts">
  import Icon, { type IconName } from './lib/Icon.svelte'
  import Chats from './pages/Chats.svelte'
  import Conversation from './pages/Conversation.svelte'
  import Snoozed from './pages/Snoozed.svelte'
  import ScheduledPage from './pages/Scheduled.svelte'
  import Transcriptions from './pages/Transcriptions.svelte'
  import Setup from './pages/Setup.svelte'
  import { beeper } from './lib/api'

  let hash = $state(location.hash || '#/')
  // If Beeper is unreachable, show the app anyway: its pages report the error.
  let setup = $state<'checking' | 'needed' | 'done'>('checking')
  beeper<{ state: string }>('/app/setup').then(
    (s) => (setup = s.state === 'ready' ? 'done' : 'needed'),
    () => (setup = 'done'),
  )
  const parts = $derived(hash.slice(2).split('/'))
  const tabs: [string, IconName, string][] = [
    ['#/', 'inbox', 'Inbox'],
    ['#/archived', 'archive', 'Archived'],
    ['#/snoozed', 'snooze', 'Snoozed'],
    ['#/scheduled', 'later', 'Scheduled'],
    ['#/transcriptions', 'mic', 'Transcripts'],
  ]
</script>

<svelte:window onhashchange={() => (hash = location.hash || '#/')} />

{#if setup === 'needed'}
  <Setup ondone={() => (setup = 'done')} />
{:else if setup === 'done'}
{#if parts[0] !== 'chats'}
  <nav>
    {#each tabs as [href, icon, label] (href)}
      <a {href} aria-current={hash === href ? 'page' : undefined}><Icon name={icon} size={22} />{label}</a>
    {/each}
  </nav>
{/if}

<main>
  {#key hash}
    {#if parts[0] === 'chats'}
      <Conversation id={decodeURIComponent(parts[1])} />
    {:else if parts[0] === 'archived'}
      <Chats archived />
    {:else if parts[0] === 'snoozed'}
      <Snoozed />
    {:else if parts[0] === 'scheduled'}
      <ScheduledPage />
    {:else if parts[0] === 'transcriptions'}
      <Transcriptions />
    {:else}
      <Chats />
    {/if}
  {/key}
</main>
{/if}
