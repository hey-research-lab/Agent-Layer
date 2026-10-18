# AgentLayer website API policy

The [OpenAPI document](https://www.agent-layer.tech/openapi.json) covers the website's `/api/onboarding/*` endpoints. It does not version the local AgentLayer wallet or the separate hosted MCP services.

## Version selection

Send `X-AgentLayer-API-Version: 1` on onboarding requests. Clients that omit the header use version 1, preserving the existing URLs and request bodies. Every onboarding response echoes `X-AgentLayer-API-Version: 1`. An unsupported version returns HTTP 400 with the JSON error code `unsupported_api_version`. The Better Auth routes under `/api/auth/*` follow Better Auth's own contract.

Future incompatible onboarding contracts will get a new version value. Existing version values will keep their request and response meaning while supported. Compatible additions may add optional fields without a version change; clients should ignore fields they do not use.

## Deprecation and removal

Onboarding responses include a `Link` header with `rel="deprecation"` pointing to this policy; that link alone does not mean an endpoint is deprecated. When a version or endpoint becomes deprecated, its responses will additionally include `Deprecation: @<Unix timestamp>` with the effective date, and this policy will link to migration instructions. If removal is scheduled, the same responses will include a `Sunset` header with an HTTP date. The sunset date will be later than the deprecation date. The endpoint remains usable until sunset. No onboarding version or endpoint is currently deprecated, and no removal date is scheduled.

The `Deprecation` header follows [RFC 9745](https://www.rfc-editor.org/rfc/rfc9745.html). The `Sunset` header follows [RFC 8594](https://www.rfc-editor.org/rfc/rfc8594.html).
