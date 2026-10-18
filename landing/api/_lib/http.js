import { OnboardingError } from './invites.js'

export function setNoStore(res) {
  res.setHeader('Cache-Control', 'no-store')
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('X-Content-Type-Options', 'nosniff')
}

export function sendJson(res, status, payload) {
  setNoStore(res)
  return res.status(status).json(payload)
}

export function requestJson(req, maxBytes = 4_096) {
  const contentLength = Number.parseInt(String(req.headers?.['content-length'] || '0'), 10)
  if (Number.isFinite(contentLength) && contentLength > maxBytes) {
    throw new OnboardingError('request_too_large', 413, 'Request body is too large.')
  }

  if (req.body === undefined || req.body === null || req.body === '') return {}
  if (typeof req.body === 'object' && !Buffer.isBuffer(req.body)) return req.body
  try {
    return JSON.parse(Buffer.from(req.body).toString('utf8'))
  } catch {
    throw new OnboardingError('invalid_json', 400, 'Request body must be valid JSON.')
  }
}

export function methodNotAllowed(res, allowed = ['POST']) {
  res.setHeader('Allow', allowed.join(', '))
  return sendJson(res, 405, {
    ok: false,
    error: 'method_not_allowed',
    message: `Use ${allowed.join(' or ')} for this endpoint.`,
    hint: 'See /openapi.json for supported methods and request bodies.',
  })
}

const ERROR_HINTS = {
  invalid_json: 'Send a valid JSON request body.',
  request_too_large: 'Keep the JSON request body under 4096 bytes.',
  provider_required: 'Choose a connected GitHub or X account.',
  provider_not_connected: 'Connect GitHub or X on /onboard first.',
  authentication_required: 'Sign in with GitHub or X on /onboard first.',
  already_claimed: 'Use the existing invite or request a replacement on /onboard.',
  invalid_invite: 'Claim a fresh invite on /onboard.',
  invite_already_bound: 'Use the wallet address already bound to this invite.',
  invite_expired: 'Request a replacement invite on /onboard.',
  invite_not_replaceable: 'Claim a new invite on /onboard if eligible.',
  invite_revoked: 'Request a replacement invite on /onboard.',
  invalid_base_address: 'Provide a valid Base wallet address.',
  address_already_used: 'Use a different Base wallet address.',
}

export function sendApiError(res, error) {
  if (error instanceof OnboardingError) {
    return sendJson(res, error.status, {
      ok: false,
      error: error.code,
      message: error.message,
      hint: ERROR_HINTS[error.code] || 'See /openapi.json for the request requirements.',
    })
  }

  console.error('onboarding_api_error', {
    name: error?.name || 'Error',
    message: error?.message || 'Unknown onboarding API error',
  })
  return sendJson(res, 500, {
    ok: false,
    error: 'internal_error',
    message: 'The onboarding service is temporarily unavailable.',
    hint: 'Retry later. If the problem persists, contact AgentLayer support.',
  })
}
