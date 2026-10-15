// Which interpreter checks a staged release (MCP handshake, boot-key unseal).
//
// The release's own venv comes first: it is what users will run. The env variables
// are fallbacks only. OPENCLAW_AGENT_WALLET_PYTHON names the interpreter a venv is
// built from (no wallet packages installed), and AGENT_WALLET_PYTHON points at the
// previous runtime, so neither can vouch for the new release when its venv exists.
export function releaseCheckPython(venvPython, env = process.env) {
  return venvPython || env.AGENT_WALLET_PYTHON || env.OPENCLAW_AGENT_WALLET_PYTHON || null;
}
