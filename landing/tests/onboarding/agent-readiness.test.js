import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it } from 'node:test'

import middleware, { prefersMarkdown } from '../../middleware.js'
import { methodNotAllowed, sendApiError } from '../../api/_lib/http.js'
import { OnboardingError } from '../../api/_lib/invites.js'
import { ROUTES } from '../../src/routes.js'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const request = (pathname, accept = 'text/html') => new Request(`https://www.agent-layer.tech${pathname}`, { headers: { Accept: accept } })

function responseRecorder() {
  const headers = new Headers()
  return {
    headers,
    status(code) { this.statusCode = code; return this },
    setHeader(name, value) { headers.set(name, value) },
    json(payload) { this.payload = payload; return this },
  }
}

describe('agent-facing website responses', () => {
  it('negotiates Markdown on the canonical homepage and keeps HTML for browsers', async () => {
    const markdown = middleware(request('/', 'text/markdown'))
    assert.equal(markdown.status, 200)
    assert.match(markdown.headers.get('content-type'), /^text\/markdown/)
    assert.equal(markdown.headers.get('vary'), 'Accept')
    assert.match(await markdown.text(), /# AgentLayer Agent Wallet/)

    const html = middleware(request('/', 'text/html'))
    assert.equal(html.headers.get('vary'), 'Accept')
    assert.equal(html.headers.get('x-middleware-next'), '1')
  })

  it('respects quality values and specific media types', () => {
    assert.equal(prefersMarkdown('text/html, text/markdown;q=0.5'), false)
    assert.equal(prefersMarkdown('text/markdown, text/html;q=0.5'), true)
    assert.equal(prefersMarkdown('text/markdown;q=0, */*'), false)
    assert.equal(prefersMarkdown('*/*'), false)
  })

  it('serves route-specific Markdown and a useful Markdown 404', async () => {
    const wallet = middleware(request('/wallet', 'text/markdown'))
    assert.match(await wallet.text(), /AgentLayer Wallet/)
    assert.match(await middleware(request('/product', 'text/markdown')).text(), /AgentLayer MCP/)
    const missing = middleware(request('/__ora-404-probe-nxfmib2l', 'text/markdown'))
    assert.equal(missing.status, 404)
    assert.match(missing.headers.get('content-type'), /^text\/markdown/)
    assert.equal(missing.headers.get('vary'), 'Accept')
    assert.match(await missing.text(), /\[documentation\]\(https:\/\/docs\.agent-layer\.tech\/\)/)
    assert.equal(middleware(request('/missing', 'text/html')).headers.get('x-middleware-next'), '1')
    assert.equal(middleware(request('/skill.md', 'text/markdown')).headers.get('x-middleware-next'), '1')

    const head = middleware(new Request('https://www.agent-layer.tech/missing', { method: 'HEAD', headers: { Accept: 'text/markdown' } }))
    assert.equal(head.status, 404)
    assert.equal(await head.text(), '')
  })

  it('returns structured JSON for unknown API routes', async () => {
    const missing = middleware(request('/api/missing', 'text/html'))
    assert.equal(missing.status, 404)
    assert.match(missing.headers.get('content-type'), /^application\/json/)
    assert.deepEqual(Object.keys(await missing.json()), ['ok', 'error', 'message', 'hint'])
    assert.equal(middleware(request('/api/onboarding/session')).headers.get('x-middleware-next'), '1')
  })

  it('selects API contract version 1 without changing existing URLs', async () => {
    const legacy = middleware(request('/api/onboarding/providers'))
    assert.equal(legacy.headers.get('x-middleware-next'), '1')
    assert.equal(legacy.headers.get('x-agentlayer-api-version'), '1')
    assert.match(legacy.headers.get('link'), /rel="deprecation"/)

    const explicit = middleware(new Request('https://www.agent-layer.tech/api/onboarding/providers', {
      headers: { 'X-AgentLayer-API-Version': '1' },
    }))
    assert.equal(explicit.headers.get('x-middleware-next'), '1')

    const unsupported = middleware(new Request('https://www.agent-layer.tech/api/onboarding/providers', {
      headers: { 'X-AgentLayer-API-Version': '2' },
    }))
    assert.equal(unsupported.status, 400)
    assert.equal(unsupported.headers.get('x-agentlayer-api-version'), '1')
    assert.equal((await unsupported.json()).error, 'unsupported_api_version')
    assert.equal(middleware(request('/api/auth/get-session')).headers.get('x-agentlayer-api-version'), null)
  })
})

describe('onboarding API errors', () => {
  it('provides code, message, and resolution hint for method errors', () => {
    const res = responseRecorder()
    methodNotAllowed(res, ['GET'])
    assert.equal(res.statusCode, 405)
    assert.equal(res.payload.error, 'method_not_allowed')
    assert.match(res.payload.message, /GET/)
    assert.match(res.payload.hint, /openapi\.json/)
  })

  it('adds a resolution hint without changing existing error codes', () => {
    const res = responseRecorder()
    sendApiError(res, new OnboardingError('invalid_json', 400, 'Request body must be valid JSON.'))
    assert.equal(res.statusCode, 400)
    assert.equal(res.payload.error, 'invalid_json')
    assert.match(res.payload.hint, /valid JSON/)
  })
})

describe('published OpenAPI document', () => {
  it('is valid JSON with operations for every website onboarding endpoint', async () => {
    const spec = JSON.parse(await readFile(path.join(root, 'public/openapi.json'), 'utf8'))
    assert.equal(spec.openapi, '3.1.0')
    for (const endpoint of ['providers', 'session', 'claim-invite', 'replace-invite', 'bind-wallet']) {
      assert.ok(spec.paths[`/api/onboarding/${endpoint}`])
      assert.equal(spec.paths[`/api/onboarding/${endpoint}`].parameters[0].$ref, '#/components/parameters/ApiVersion')
      assert.ok(await readFile(path.join(root, `api/onboarding/${endpoint}.js`), 'utf8'))
    }
    assert.equal(spec.components.parameters.ApiVersion.name, 'X-AgentLayer-API-Version')
    assert.equal(spec.components.parameters.ApiVersion.schema.default, '1')
    assert.match(spec.externalDocs.url, /api-policy\.md$/)
    assert.equal(spec.paths['/api/onboarding/bind-wallet'].post.responses['200'].content['application/json'].schema.properties.status.type, 'string')
  })

  it('uses the updated brand in homepage metadata', () => {
    assert.match(ROUTES.home.title, /AgentLayer Agent Wallet/)
  })

  it('publishes when-to-use guidance and the official npm CLI in llms.txt', async () => {
    const llms = await readFile(path.join(root, 'public/llms.txt'), 'utf8')
    const manifest = JSON.parse(await readFile(path.join(root, '../package.json'), 'utf8'))
    assert.match(llms, /^# AgentLayer Agent Wallet\n\n> /)
    assert.match(llms, /## When to use AgentLayer/)
    assert.match(llms, /\[Official npm CLI\]\(https:\/\/www\.npmjs\.com\/package\/@agentlayer\.tech\/wallet\)/)
    assert.match(llms, /npx --yes @agentlayer\.tech\/wallet@latest install/)
    assert.equal(manifest.name, '@agentlayer.tech/wallet')
    assert.equal(manifest.bin.wallet, './bin/openclaw-agent-wallet.mjs')
  })

  it('publishes a protocol-correct deprecation policy and discoverable brand metadata', async () => {
    const policy = await readFile(path.join(root, 'public/api-policy.md'), 'utf8')
    const html = await readFile(path.join(root, 'index.html'), 'utf8')
    const manifest = JSON.parse(await readFile(path.join(root, 'public/site.webmanifest'), 'utf8'))
    const structuredData = JSON.parse(html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/)[1])
    assert.match(policy, /Deprecation: @<Unix timestamp>/)
    assert.match(policy, /Sunset/)
    assert.match(html, /rel="describedby" href="https:\/\/www\.agent-layer\.tech\/llms\.txt"/)
    assert.equal(structuredData['@graph'].find((item) => item['@type'] === 'SoftwareApplication').name, 'AgentLayer Agent Wallet')
    assert.equal(manifest.name, 'AgentLayer')
    assert.match(manifest.description, /AgentLayer Agent Wallet/)
    assert.match(html, /AgentLayer Agent Wallet is a local wallet/)
  })
})
