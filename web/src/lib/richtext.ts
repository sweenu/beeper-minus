// Messages formatted in their app (Matrix's formatted body) arrive as HTML. Only these tags are
// rebuilt, as fresh elements without attributes (links keep a checked href); everything else
// becomes its text, and the contents of DROP go away.
const KEEP = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'S', 'DEL', 'STRIKE', 'CODE', 'PRE', 'BR', 'P', 'BLOCKQUOTE', 'UL', 'OL', 'LI', 'A', 'SUB', 'SUP'])
const DROP = new Set(['SCRIPT', 'STYLE', 'TEMPLATE', 'NOSCRIPT', 'TITLE', 'IFRAME', 'OBJECT', 'MX-REPLY'])
// Plain text with a stray "<" stays plain: only treat it as HTML when it has a known tag.
const LOOKS_HTML = /<\/?(?:b|strong|i|em|u|s|del|strike|code|pre|br|p|blockquote|ul|ol|li|a|sub|sup|span|font|h[1-6]|mx-reply)\b[^>]*>/i

// DOMParser documents run no scripts and load no images. Browsers parse a bare fragment the same
// way, but a whole document is what every DOM implementation accepts.
const parse = (html: string) => new DOMParser().parseFromString(`<!doctype html><html><body>${html}</body></html>`, 'text/html').body

function rebuild(from: Node, to: Node) {
  for (const n of from.childNodes) {
    if (n.nodeType === Node.TEXT_NODE) {
      to.appendChild(document.createTextNode(n.textContent ?? ''))
    } else if (n instanceof Element) {
      const tag = n.tagName.toUpperCase()
      const href = n.getAttribute('href') ?? ''
      if (DROP.has(tag)) continue
      if (!KEEP.has(tag) || (tag === 'A' && !/^(https?:|mailto:)/i.test(href))) {
        rebuild(n, to)
        continue
      }
      const el = document.createElement(tag.toLowerCase())
      if (tag === 'A') {
        el.setAttribute('href', href)
        el.setAttribute('target', '_blank')
        el.setAttribute('rel', 'noopener noreferrer')
      }
      rebuild(n, el)
      to.appendChild(el)
    }
  }
}

/** Svelte action: show a message's text, with its formatting when it has some. */
export function richText(node: HTMLElement, text: string) {
  const render = (t: string) => {
    node.replaceChildren()
    if (LOOKS_HTML.test(t)) rebuild(parse(t), node)
    else node.textContent = t
  }
  render(text)
  return { update: render }
}

/** The text alone, for previews: what the message shows, without its formatting. */
export function plain(t: string) {
  if (!LOOKS_HTML.test(t)) return t
  const shown = document.createElement('div')
  rebuild(parse(t), shown)
  return shown.textContent ?? ''
}
