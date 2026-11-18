<script lang="ts">
  import { onMount } from 'svelte'
  import { renderSVG } from 'uqr'
  import { api, beeper, WORKING, type Account, type Bridge, type LoginSession } from '../lib/api'
  import Icon from '../lib/Icon.svelte'

  interface Linking {
    bridge: Bridge
    account?: Account
    flows?: { id: string; name?: string; description?: string }[] // sign-in methods to pick from
    session?: LoginSession
  }

  let bridges = $state<Bridge[] | null>(null)
  let linking = $state<Linking | null>(null)
  let fields = $state<Record<string, string>>({})
  let busy = $state(false)
  let error = $state('')

  const enc = encodeURIComponent
  const base = (id: string) => `/api/beeper/v1/bridges/${enc(id)}`
  // Cloud networks are listed only once connected: they're added from Beeper's own apps.
  const shown = $derived((bridges ?? []).filter((b) => b.accounts.length || b.provider === 'local'))
  const step = $derived(linking?.session?.currentStep)

  const load = () => beeper<{ items: Bridge[] }>('/bridges').then((r) => (bridges = r.items), (e) => (error = e.message))
  onMount(load)

  const who = (a: Account) => a.user?.fullName || a.user?.username || a.user?.phoneNumber || a.user?.email || a.accountID
  const status = (a: Account) => a.statusText || a.status.replaceAll('_', ' ')

  async function start(bridge: Bridge, account?: Account) {
    error = ''
    try {
      const flows = (await api<{ items: NonNullable<Linking['flows']> }>(`${base(bridge.id)}/login-flows`)).items
      linking = { bridge, account, flows: flows.length > 1 ? flows : undefined }
      if (flows.length <= 1) begin(flows[0]?.id)
    } catch (e) {
      error = (e as Error).message
    }
  }

  function begin(flowID?: string) {
    const l = linking!
    l.flows = undefined
    run(() => api(`${base(l.bridge.id)}/login-sessions`, { method: 'POST', body: { flowID, accountID: l.account?.accountID, loginID: l.account?.loginID } }))
  }

  // Each answer is the session's next state. A wait step (QR code, pairing code) is answered by
  // Beeper Server only once the phone confirmed, or with a fresh code when the old one expired.
  async function run(call: () => Promise<LoginSession>) {
    busy = true
    error = ''
    try {
      let s = await call()
      while (linking) {
        linking.session = s
        const next = s.currentStep
        fields = next && 'fields' in next ? Object.fromEntries(next.fields.map((f) => [f.id, f.initialValue ?? ''])) : {}
        if (s.status !== 'waiting_for_display' || !next) break
        busy = false
        s = await api<LoginSession>(`${base(s.bridgeID)}/login-sessions/${enc(s.loginSessionID)}/steps/${enc(next.stepID)}`, {
          method: 'POST',
          body: { type: 'display_and_wait' },
        })
      }
      if (linking?.session?.status === 'complete' || linking?.session?.status === 'cancelled') {
        linking = null
        await load()
      }
    } catch (e) {
      if (linking) error = (e as Error).message
    } finally {
      busy = false
    }
  }

  function submit(e: SubmitEvent) {
    e.preventDefault()
    const s = linking!.session!
    const current = s.currentStep!
    run(() =>
      api(`${base(s.bridgeID)}/login-sessions/${enc(s.loginSessionID)}/steps/${enc(current.stepID)}`, {
        method: 'POST',
        body: { type: current.type, fields: { ...fields } },
      }),
    )
  }

  async function cancel() {
    const s = linking?.session
    linking = null
    error = ''
    if (s && !['complete', 'cancelled', 'failed'].includes(s.status)) {
      await api(`${base(s.bridgeID)}/login-sessions/${enc(s.loginSessionID)}`, { method: 'DELETE' }).catch(() => {})
    }
  }

  // Browsers only know a few input types; the rest are plain text.
  const inputType = (t?: string) => (['password', 'email', 'tel', 'url', 'number'].includes(t ?? '') ? t : 'text')
</script>

<header class="title">
  <a href="#/" class="icon" aria-label="Back to inbox"><Icon name="back" /></a>
  <h1>Accounts</h1>
</header>
{#if error}<p class="error" role="alert">{error}</p>{/if}

{#if linking}
  <section class="card linking" aria-live="polite">
    <h2>{linking.account ? 'Reconnect' : 'Connect'} {linking.bridge.displayName}</h2>
    {#if linking.flows}
      <p>How do you want to sign in?</p>
      {#each linking.flows as f (f.id)}
        <button class="choice" onclick={() => begin(f.id)}><strong>{f.name ?? f.id}</strong>{#if f.description}<small>{f.description}</small>{/if}</button>
      {/each}
    {:else if !linking.session}
      <p class="muted">Starting…</p>
    {:else if linking.session.status === 'failed'}
      <p class="error">{linking.session.error?.message ?? 'Signing in failed.'}</p>
      <button onclick={() => start(linking!.bridge, linking!.account)}>Try again</button>
    {:else if step}
      {#if step.instructions}<p>{step.instructions}</p>{/if}
      {#if step.type === 'display_and_wait'}
        {#if step.display.type === 'qr'}
          <div class="qr" role="img" aria-label="QR code to scan with your phone">{@html renderSVG(step.display.data)}</div>
        {:else if step.display.type === 'code'}
          <p class="key center">{step.display.data}</p>
        {:else if step.display.type === 'emoji'}
          <img class="emoji-step" src={step.display.imageURL} alt="Emoji to match on your phone" />
        {/if}
        <p class="muted">Waiting for your phone…</p>
      {:else if step.type === 'user_input' || step.type === 'cookies'}
        <form onsubmit={submit}>
          {#if step.type === 'cookies'}
            <p>
              Open <a href={step.url} target="_blank" rel="noopener noreferrer">{step.url}</a> in your browser and sign in. Then copy
              these values from its developer tools (Storage or Application tab) and paste them here.
            </p>
          {/if}
          {#each step.fields as f (f.id)}
            <label>
              {f.label ?? f.name ?? f.id}{#if step.type === 'cookies' && f.type && f.type !== 'cookie'}<small> ({f.type.replace('_', ' ')})</small>{/if}
              <input type={inputType(f.type)} bind:value={fields[f.id]} placeholder={f.placeholder} required={!f.optional} autocomplete="off" />
            </label>
          {/each}
          <button class="primary" disabled={busy}>Continue</button>
        </form>
      {/if}
    {/if}
    <button class="link" onclick={cancel}>Cancel</button>
  </section>
{/if}

<ul class="list">
  {#each shown as b (b.id)}
    {#each b.accounts as a (a.accountID)}
      <li class="card account">
        <div class="line">
          <strong>{b.displayName}</strong>
          <span class:error={!WORKING.includes(a.status)} class:muted={WORKING.includes(a.status)}>{status(a)}</span>
        </div>
        <div class="line">
          <span class="meta ellipsis">{who(a)} · {b.provider === 'local' ? 'on this server' : 'Beeper Cloud'}</span>
          {#if b.provider === 'local'}
            <button disabled={!!linking} onclick={() => start(b, a)}>Reconnect</button>
          {/if}
        </div>
      </li>
    {/each}
    {#if !b.accounts.length}
      <li class="card account">
        <div class="line">
          <strong>{b.displayName}</strong>
          <span class="muted">not connected</span>
        </div>
        <div class="line">
          <span class="meta">on this server</span>
          {#if b.status === 'available'}<button disabled={!!linking} onclick={() => start(b)}>Connect</button>{/if}
        </div>
      </li>
    {/if}
  {:else}
    {#if bridges}<li class="empty">No accounts.</li>{/if}
  {/each}
</ul>
<p class="muted">
  Networks marked Beeper Cloud are connected and reconnected from Beeper on your phone or computer; this server picks them up on its
  own. Networks on this server have to be linked here.
</p>
