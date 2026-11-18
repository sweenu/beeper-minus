// npm test: the formatting allowlist, on a DOM from linkedom.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { DOMParser, parseHTML } from 'linkedom'

const { window } = parseHTML('<!doctype html><html><body></body></html>')
Object.assign(globalThis, { document: window.document, DOMParser, Node: window.Node, Element: window.Element })
const { richText, plain } = await import('./richtext.ts')

const render = (text: string) => {
  const node = window.document.createElement('p')
  richText(node as unknown as HTMLElement, text)
  return node.innerHTML
}

test('keeps formatting', () => {
  assert.equal(render('<em>it</em> <strong>b</strong> <code>c</code><br><ul><li>x</li></ul>'), '<em>it</em> <strong>b</strong> <code>c</code><br><ul><li>x</li></ul>')
})

test('drops scripts, attributes and unknown tags', () => {
  const out = render('<img src=x onerror="alert(1)"><script>alert(2)</script><b onclick="alert(3)" style="x">b</b><span style="color:red">s</span><mx-reply>quoted</mx-reply>')
  assert.equal(out, '<b>b</b>s')
})

test('links: only http(s) and mailto, opened safely', () => {
  assert.equal(render('<a href="javascript:alert(1)">js</a>'), 'js')
  assert.equal(render('<a href=" javascript:alert(1)">js</a>'), 'js')
  assert.equal(render('<a href="data:text/html,x">d</a>'), 'd')
  const node = window.document.createElement('p')
  richText(node as unknown as HTMLElement, '<a href="https://example.com" onclick="x">ok</a>')
  const a = node.querySelector('a')!
  assert.deepEqual(
    Object.fromEntries(a.getAttributeNames().map((n: string) => [n, a.getAttribute(n)])),
    { href: 'https://example.com', target: '_blank', rel: 'noopener noreferrer' },
  )
})

test('plain text stays text', () => {
  assert.equal(render('Is 3 < 5? <3 <notatag>'), 'Is 3 &lt; 5? &lt;3 &lt;notatag&gt;')
  assert.equal(plain('<em>hi</em> &amp; bye'), 'hi & bye')
  assert.equal(plain('<b>shown</b><script>alert(1)</script><style>p{}</style>'), 'shown')
})
