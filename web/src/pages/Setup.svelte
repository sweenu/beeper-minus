<script lang="ts">
  import { onMount } from 'svelte'
  import { api, beeper } from '../lib/api'

  // Signs Beeper Server in and unlocks encrypted messages, through its /v1/app/setup API.
  let { ondone }: { ondone: () => void } = $props()

  interface Verification {
    id: string
    state: 'requested' | 'ready' | 'sas_ready' | 'qr_scanned' | 'done' | 'cancelled' | 'error'
    direction: 'incoming' | 'outgoing'
    availableActions: string[]
    otherDevice?: { name?: string }
    sas?: { emojis: string; decimals?: string }
    error?: { reason: string }
  }

  let setup = $state<{ state: string; verification?: Verification | null } | null>(null)
  let step = $state<'email' | 'code' | 'username'>('email')
  let email = $state('')
  let code = $state('')
  let username = $state('')
  let recoveryKey = $state('')
  let newKey = $state('') // a freshly created recovery key, shown once
  let suggestions = $state<string[]>([])
  let busy = $state(false)
  let error = $state('')
  let requestID = ''
  let leadToken = ''

  const post = <T = any>(path: string, body: object = {}) => api<T>(`/api/beeper/v1/app/setup${path}`, { method: 'POST', body })
  const v = $derived(setup?.verification)
  const verifying = $derived(v && !['done', 'cancelled', 'error'].includes(v.state))

  async function refresh() {
    setup = await beeper('/app/setup')
    if (setup?.state === 'ready') ondone()
  }

  onMount(() => {
    refresh().catch((e) => (error = e.message))
    // Follows initial sync and verifications happening on another device.
    const timer = setInterval(() => !busy && refresh().catch(() => {}), 2000)
    return () => clearInterval(timer)
  })

  async function act(e: Event | null, fn: () => Promise<unknown>) {
    e?.preventDefault()
    busy = true
    error = ''
    try {
      await fn()
      await refresh()
    } catch (err) {
      error = (err as Error).message
    } finally {
      busy = false
    }
  }

  const sendEmail = (e: Event) =>
    act(e, async () => {
      requestID = (await post<{ setupRequestID: string }>('/start')).setupRequestID
      await post('/email', { setupRequestID: requestID, email: email.trim() })
      step = 'code'
    })

  const sendCode = (e: Event) =>
    act(e, async () => {
      const r = await post('/response', { setupRequestID: requestID, response: code.trim() })
      if (r.registrationRequired) {
        requestID = r.setupRequestID
        leadToken = r.leadToken
        suggestions = r.usernameSuggestions ?? []
        username = suggestions[0] ?? ''
        step = 'username'
      }
    })

  const register = (e: Event) =>
    act(e, () => post('/register', { setupRequestID: requestID, leadToken, username: username.trim(), acceptTerms: true }))

  const unlock = (e: Event) => act(e, () => post('/verification/recovery-key', { recoveryKey: recoveryKey.trim() }))
  const createKey = () => act(null, async () => (newKey = (await post<{ recoveryKey: string }>('/verification/recovery-key/reset')).recoveryKey))
  const confirmKey = () =>
    act(null, async () => {
      await post('/verification/recovery-key/reset/confirm', { recoveryKey: newKey })
      newKey = ''
    })
  const startVerification = () => act(null, () => post('/verifications', { purpose: 'login' }))
  // Actions are named like "sas.start"; their paths like ".../sas/start".
  const verify = (action: string) => act(null, () => post(`/verifications/${v!.id}/${action.replace('.', '/')}`))
</script>

<main class="setup">
  <h1>Set up Beeper</h1>
  {#if !setup}
    <p class="muted">Connecting to Beeper Server…</p>
  {:else if setup.state === 'needs-login'}
    {#if step === 'email'}
      <form onsubmit={sendEmail}>
        <p>Sign this server in to your Beeper account. We'll email you a code.</p>
        <label>Email <input type="email" bind:value={email} required autocomplete="email" /></label>
        <button class="primary" disabled={busy}>Send code</button>
      </form>
    {:else if step === 'code'}
      <form onsubmit={sendCode}>
        <p>Enter the code sent to {email}.</p>
        <label>Code <input bind:value={code} required inputmode="numeric" autocomplete="one-time-code" /></label>
        <button class="primary" disabled={busy}>Sign in</button>
        <button type="button" class="link" onclick={() => (step = 'email')}>Use another email</button>
      </form>
    {:else}
      <form onsubmit={register}>
        <p>No Beeper account uses {email} yet. Choose a username to create one.</p>
        <label>Username <input bind:value={username} required autocomplete="username" /></label>
        {#if suggestions.length}
          <p class="muted">Suggestions: {#each suggestions as s, i (s)}{i ? ', ' : ''}<button type="button" class="link" onclick={() => (username = s)}>{s}</button>{/each}</p>
        {/if}
        <p class="muted">
          By continuing, you agree to the <a href="https://www.beeper.com/terms-onboarding" target="_blank" rel="noopener">Terms of Use</a>
          and acknowledge the <a href="https://www.beeper.com/privacy" target="_blank" rel="noopener">Privacy Policy</a>.
        </p>
        <button class="primary" disabled={busy}>Continue</button>
      </form>
    {/if}
  {:else if setup.state === 'needs-cross-signing-setup'}
    {#if newKey}
      <p>This is your recovery key. Save it somewhere safe: you'll need it to read your messages on a new device.</p>
      <p class="key">{newKey}</p>
      <button onclick={() => navigator.clipboard?.writeText(newKey)}>Copy</button>
      <button class="primary" disabled={busy} onclick={confirmKey}>I've saved it</button>
    {:else}
      <p>Your account doesn't have encrypted messaging set up yet. Create a recovery key to turn it on.</p>
      <button class="primary" disabled={busy} onclick={createKey}>Create recovery key</button>
    {/if}
  {:else if setup.state === 'needs-verification' || setup.state === 'needs-secrets'}
    {#if verifying && v}
      {#if v.state === 'sas_ready' && v.sas}
        <p>Check that {v.otherDevice?.name ?? 'your other device'} shows the same emojis, in the same order:</p>
        <p class="emojis">{v.sas.emojis}</p>
        {#if v.sas.decimals}<p class="muted center">{v.sas.decimals}</p>{/if}
      {:else if v.state === 'requested' && v.direction === 'outgoing'}
        <p>Open Beeper on another device you're signed in to and accept the verification request.</p>
      {:else if v.state === 'requested'}
        <p>{v.otherDevice?.name ?? 'Another device'} wants to verify this server.</p>
      {:else if v.availableActions.includes('sas.start')}
        <p>{v.otherDevice?.name ?? 'Your other device'} accepted. Compare emojis to finish.</p>
      {:else}
        <p>Follow the steps on {v.otherDevice?.name ?? 'your other device'}.</p>
      {/if}
      <div class="actions">
        {#if v.availableActions.includes('accept')}<button class="primary" disabled={busy} onclick={() => verify('accept')}>Accept</button>{/if}
        {#if v.availableActions.includes('sas.start')}<button class="primary" disabled={busy} onclick={() => verify('sas.start')}>Compare emojis</button>{/if}
        {#if v.availableActions.includes('sas.confirm')}<button class="primary" disabled={busy} onclick={() => verify('sas.confirm')}>They match</button>{/if}
        {#if v.availableActions.includes('cancel')}
          <button class="danger" disabled={busy} onclick={() => verify('cancel')}>{v.state === 'sas_ready' ? "They don't match" : 'Cancel'}</button>
        {/if}
      </div>
    {:else}
      {#if v?.error}<p class="error">Verification stopped: {v.error.reason}</p>{/if}
      <p>Unlock your encrypted messages on this server.</p>
      <form onsubmit={unlock}>
        <label>Recovery key <input bind:value={recoveryKey} required autocomplete="off" spellcheck="false" /></label>
        <button class="primary" disabled={busy}>Unlock</button>
      </form>
      {#if setup.state === 'needs-verification'}
        <p class="muted center">or</p>
        <button disabled={busy} onclick={startVerification}>Verify with another device</button>
      {/if}
    {/if}
  {:else}
    <p class="muted">Getting your chats ready… This can take a few minutes.</p>
  {/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</main>
