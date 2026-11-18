// Beeper Client API objects, reduced to the fields the web app reads.
export interface Attachment {
  type: 'unknown' | 'img' | 'video' | 'audio'
  id?: string
  srcURL?: string
  fileName?: string
  isVoiceNote?: boolean
  transcription?: { transcription: string; language?: string }
}

export interface Message {
  id: string
  chatID: string
  senderName?: string
  timestamp: string
  text?: string
  type?: string
  isSender?: boolean
  isDeleted?: boolean
  attachments?: Attachment[]
}

export interface Chat {
  id: string
  title: string
  network: string
  type: 'single' | 'group'
  unreadCount: number
  isArchived?: boolean
  lastActivity?: string
  preview?: Message
  merge?: { chatIDs: string[] }
  mergedIntoChatID?: string
}

export interface Page<T> {
  items: T[]
  hasMore?: boolean
  oldestCursor?: string
}

export interface Account {
  accountID: string
  loginID?: string
  network?: string
  status: string
  statusText?: string
  user?: { fullName?: string; username?: string; phoneNumber?: string; email?: string }
}

export interface Bridge {
  id: string
  displayName: string
  provider: 'cloud' | 'self-hosted' | 'local' | 'platform-sdk'
  network?: string
  status: string
  accounts: Account[]
}

export interface LoginField { id: string; label?: string; name?: string; type?: string; placeholder?: string; optional?: boolean; initialValue?: string }

export type LoginStep = { stepID: string; instructions?: string } & (
  | { type: 'user_input'; fields: LoginField[] }
  | { type: 'cookies'; url: string; fields: LoginField[] }
  | { type: 'display_and_wait'; display: { type: 'qr' | 'code'; data: string } | { type: 'emoji'; imageURL: string } | { type: 'nothing' } }
  | { type: 'complete' }
)

export interface LoginSession {
  bridgeID: string
  loginSessionID: string
  status: 'waiting_for_input' | 'waiting_for_cookies' | 'waiting_for_display' | 'complete' | 'cancelled' | 'failed'
  currentStep?: LoginStep
  error?: { message: string }
}

// Healthy account states; anything else needs the user.
export const WORKING = ['connected', 'connecting', 'backfilling']

// Beeper Minus' own records.
export interface Snooze { chat_id: string; chat_title: string; network: string; until: number }
export interface Scheduled { id: number; chat_id: string; chat_title: string; network: string; text: string; send_at: number; error: string | null }
export interface Transcription {
  chat_id: string; message_id: string; chat_title: string; network: string; sender: string
  sent_at: number; language: string; duration: number; text: string; created_at: number
}

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const r = await fetch(path, {
    method: init?.method ?? 'GET',
    headers: init?.body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
  })
  const text = await r.text()
  if (!r.ok) {
    let message = text
    try {
      message = JSON.parse(text).message ?? text
    } catch {}
    throw Object.assign(new Error(message || `${r.status} ${r.statusText}`), { status: r.status })
  }
  return text ? JSON.parse(text) : (undefined as T)
}

export const beeper = <T>(path: string, query: Record<string, string | number | undefined> = {}) => {
  const q = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]))
  return api<T>(`/api/beeper/v1${path}${q.size ? `?${q}` : ''}`)
}

export const chatPath = (id: string) => `/chats/${encodeURIComponent(id)}`
export const assetURL = (a: Attachment) => `/api/beeper/v1/assets/serve?url=${encodeURIComponent(a.id ?? a.srcURL ?? '')}`

export const snooze = (chatID: string, until: number) => api('/api/snoozes', { method: 'POST', body: { chatID, until } })
export const unsnooze = (chatID: string) => api(`/api/snoozes/${encodeURIComponent(chatID)}`, { method: 'DELETE' })
export const schedule = (chatID: string, text: string, sendAt: number) =>
  api<Scheduled>('/api/scheduled', { method: 'POST', body: { chatID, text, sendAt } })
export const editScheduled = (id: number, text: string, sendAt: number) =>
  api<Scheduled>(`/api/scheduled/${id}`, { method: 'PATCH', body: { text, sendAt } })
export const unschedule = (id: number) => api(`/api/scheduled/${id}`, { method: 'DELETE' })
export const transcribe = (chatID: string, messageID: string) =>
  api<Transcription>('/api/transcriptions', { method: 'POST', body: { chatID, messageID } })

export const config = api<{ transcription: boolean; token: boolean }>('/api/config').catch(() => ({ transcription: false, token: true }))
