import { next } from '@vercel/functions'
import { DEFAULT_DESCRIPTION, PATH_REDIRECTS, PATH_TO_PAGE, ROUTES, normalizePathname } from './src/routes.js'

const PUBLIC_API_PATHS = new Set([
  '/api/onboarding/providers',
  '/api/onboarding/session',
  '/api/onboarding/claim-invite',
  '/api/onboarding/replace-invite',
  '/api/onboarding/bind-wallet',
])

const SITE_LINKS = [
  '[Agent Wallet](https://www.agent-layer.tech/wallet)',
  '[Use cases](https://www.agent-layer.tech/use-cases)',
  '[Documentation](https://docs.agent-layer.tech/)',
  '[Sitemap](https://www.agent-layer.tech/sitemap.xml)',
]
const API_POLICY_LINK = '<https://www.agent-layer.tech/api-policy.md>; rel="deprecation"; type="text/markdown"'

function qualityFor(accept, type) {
  let best = { specificity: -1, quality: 0 }
  for (const part of accept.split(',')) {
    const [media, ...parameters] = part.trim().toLowerCase().split(';')
    const specificity = media === type ? 2 : media === 'text/*' ? 1 : media === '*/*' ? 0 : -1
    if (specificity < 0) continue
    const qualityParameter = parameters.map((parameter) => parameter.trim()).find((parameter) => parameter.startsWith('q='))
    const quality = qualityParameter ? Number(qualityParameter.slice(2)) : 1
    if (!Number.isFinite(quality) || quality < 0 || quality > 1) continue
    if (specificity > best.specificity || (specificity === best.specificity && quality > best.quality)) {
      best = { specificity, quality }
    }
  }
  return best
}

export function prefersMarkdown(accept = '') {
  const markdown = qualityFor(accept, 'text/markdown')
  const html = qualityFor(accept, 'text/html')
  return markdown.quality > 0 && (
    markdown.quality > html.quality ||
    (markdown.quality === html.quality && markdown.specificity > html.specificity)
  )
}

function markdownPage(page) {
  const route = ROUTES[page]
  const introduction = page === 'home' ? DEFAULT_DESCRIPTION : route.description
  return `# ${route.title}\n\n${introduction}\n\nAgentLayer Agent Wallet is a local wallet for AI agents. Keys and signing stay on the user's machine; transactions are subject to wallet policy and approvals.\n\n## Get started\n\n\`npx --yes @agentlayer.tech/wallet@latest install\`\n\n${SITE_LINKS.map((link) => `- ${link}`).join('\n')}\n`
}

function markdownResponse(body, status = 200, isHead = false) {
  return new Response(isHead ? null : body, {
    status,
    headers: {
      'Content-Type': 'text/markdown; charset=utf-8',
      'Vary': 'Accept',
      'X-Content-Type-Options': 'nosniff',
    },
  })
}

export default function middleware(request) {
  const pathname = normalizePathname(new URL(request.url).pathname)
  const isRead = request.method === 'GET' || request.method === 'HEAD'

  if (pathname.startsWith('/api/')) {
    if (pathname === '/api/auth' || pathname.startsWith('/api/auth/')) return next()
    const version = request.headers.get('x-agentlayer-api-version') || '1'
    if (version !== '1') {
      return new Response(JSON.stringify({
        ok: false,
        error: 'unsupported_api_version',
        message: `API version ${version} is not supported.`,
        hint: 'Use X-AgentLayer-API-Version: 1 or omit the header. See /api-policy.md.',
      }), {
        status: 400,
        headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-AgentLayer-API-Version': '1', Link: API_POLICY_LINK },
      })
    }
    if (PUBLIC_API_PATHS.has(pathname)) return next({ headers: { 'X-AgentLayer-API-Version': '1', Link: API_POLICY_LINK } })
    return new Response(JSON.stringify({
      ok: false,
      error: 'not_found',
      message: 'This API endpoint does not exist.',
      hint: 'See /openapi.json for the available onboarding API endpoints.',
    }), {
      status: 404,
      headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-AgentLayer-API-Version': '1', Link: API_POLICY_LINK },
    })
  }

  const page = PATH_TO_PAGE[PATH_REDIRECTS[pathname] || pathname]
  if (page && isRead) {
    if (prefersMarkdown(request.headers.get('accept') || '')) return markdownResponse(markdownPage(page), 200, request.method === 'HEAD')
    return next({ headers: { Vary: 'Accept' } })
  }

  if (!page && isRead && prefersMarkdown(request.headers.get('accept') || '') && !pathname.includes('.')) {
    return markdownResponse('# 404 Not Found\n\nThis AgentLayer page does not exist. See the [documentation](https://docs.agent-layer.tech/) or [sitemap](https://www.agent-layer.tech/sitemap.xml).\n', 404, request.method === 'HEAD')
  }

  return next()
}
