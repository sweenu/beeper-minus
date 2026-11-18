const at = (d: Date, h: number, m = 0) => {
  const r = new Date(d)
  r.setHours(h, m, 0, 0)
  return r
}

export function presets(now = new Date()): [string, Date][] {
  const tomorrow = new Date(now)
  tomorrow.setDate(now.getDate() + 1)
  const monday = new Date(now)
  monday.setDate(now.getDate() + ((8 - now.getDay()) % 7 || 7))
  const list: [string, Date][] = [['In 1 hour', new Date(now.getTime() + 3600_000)]]
  if (now.getHours() < 17) list.push(['This evening', at(now, 18)])
  list.push(['Tomorrow morning', at(tomorrow, 9)], ['Monday morning', at(monday, 9)])
  return list
}

// <input type="datetime-local"> speaks local time without a zone.
export function toInput(ms: number) {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

export function when(ms: number | string) {
  const d = new Date(ms)
  const now = new Date()
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === now.toDateString()) return time
  const days = Math.abs(d.getTime() - now.getTime()) / 86400_000
  const day = d.toLocaleDateString([], days < 6 ? { weekday: 'short' } : { day: 'numeric', month: 'short', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' })
  return `${day} ${time}`
}

export const duration = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
