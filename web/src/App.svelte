<script lang="ts">
  import Icon, { type IconName } from './lib/Icon.svelte'
  import Chats from './pages/Chats.svelte'
  import Conversation from './pages/Conversation.svelte'
  import Snoozed from './pages/Snoozed.svelte'
  import ScheduledPage from './pages/Scheduled.svelte'
  import Transcriptions from './pages/Transcriptions.svelte'
  import Setup from './pages/Setup.svelte'
  import { beeper, config } from './lib/api'

  let hash = $state(location.hash || '#/')
  // If Beeper is unreachable, show the app anyway: its pages report the error.
  let setup = $state<'checking' | 'needed' | 'refused' | 'done'>('checking')
  // Beeper Server answers this without a token only while it is signed out.
  beeper<{ state: string }>('/app/setup').then(
    (s) => (setup = s.state === 'ready' ? 'done' : 'needed'),
    (e) => (setup = e.status === 401 ? 'refused' : 'done'),
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
{:else if setup === 'refused'}
  <main class="setup">
    <h1>Beeper Server won't let Beeper Minus in</h1>
    {#await config then c}
      {#if c.token}
        <p>It refused the token in <code>beeper.token_file</code>. Check that the file holds a valid read+write token for this Beeper Server.</p>
      {:else}
        <p>It is already signed in, but Beeper Minus has no token for it, so this page can't sign it in either. Either:</p>
        <ul>
          <li>give Beeper Minus the token you use with this Beeper Server, in <code>beeper.token_file</code>, or</li>
          <li>reset Beeper Server so this page can sign it in: stop it, delete its data folder (<code>/var/lib/beeper/server</code> with the NixOS module), and start it again. This signs the server out of your account: you'll verify it again and its chats sync again.</li>
        </ul>
      {/if}
    {/await}
    <button onclick={() => location.reload()}>Try again</button>
  </main>
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
