import type { Message } from './api'
import { plain } from './richtext'

const LABELS: Record<string, string> = { img: 'Photo', video: 'Video', audio: 'Audio', unknown: 'File' }

export function previewText(m?: Message) {
  if (!m) return ''
  const a = m.attachments?.[0]
  const body = (m.text && plain(m.text)) || (a ? (a.isVoiceNote ? 'Voice note' : LABELS[a.type]) : '')
  return m.isSender ? `You: ${body}` : body
}
