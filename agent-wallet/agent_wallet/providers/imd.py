"""IMD (https://imd.fun) paid actions: open jobs and other work for 0.5 IMD each.

IMD sells actions (``job.open``, ``schedule.create``...) through x402 v2
``exact`` with Permit2 on Ethereum mainnet, plus an IMD-specific EIP-712
QuoteApproval that binds the payment to the quote. A stock x402 client makes
only the first signature, and the flow is quote -> challenge -> submit rather
than a single 402 retry, so this provider drives it end to end.

Every value that reaches a signature is checked here against the pinned IMD
constants, and again by the wallet daemon (wdk-evm-wallet/src/imd.js) before
it signs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from agent_wallet.config import resolve_openclaw_home
from agent_wallet.exceptions import ProviderError
from agent_wallet.http_client import get_client

PROVIDER = "imd"
IMD_API_ORIGIN = "https://api.imd.fun"
IMD_NETWORK = "eip155:1"
IMD_TOKEN_ADDRESS = "0xd34a99bc0f67ae1bbd63c660e6d0b0dd03e263b7"
IMD_DECIMALS = 18
IMD_PAY_TO_ALLOWLIST = frozenset({"0x4e0fa57bde726079356537e2f34d671e9f41adbc"})
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
X402_EXACT_PERMIT2_PROXY_ADDRESS = "0x402085c248EeA27D92E8b30b2C58ed07f9E20001"
IMD_ACTIONS = (
    "job.open",
    "job.continue",
    "launch.open",
    "workflow.open",
    "oracle.request",
    "schedule.create",
    "schedule.topup",
)
# Actions this integration opens. launch/workflow/oracle stay out until they
# get their own UX (deployments, token economics, on-chain answers).
IMD_ENABLED_ACTIONS = ("job.open", "job.continue", "schedule.create", "schedule.topup")
IMD_PRICE_PER_ACTION_RAW = 500_000_000_000_000_000
IMD_MAX_PER_PAYMENT_RAW = 5 * 10**18
IMD_DEFAULT_DAILY_LIMIT_RAW = 5 * 10**18
DAY_SECONDS = 24 * 60 * 60
# The permit must end before the quote does; keep a margin for the submit.
PERMIT_DEADLINE_MARGIN_SECONDS = 30
PERMIT_MAX_LIFETIME_SECONDS = 300
MIN_QUOTE_REMAINING_SECONDS = 60
ORDER_POLL_ATTEMPTS = 4
ORDER_POLL_INTERVAL_SECONDS = 2.0


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """IMD's canonical JSON: sorted keys, no whitespace, integers only.

    Mirrors the reference ``canon`` in IMD's docs (JSON.stringify per scalar),
    which the paymentHash in the QuoteApproval is computed over.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        raise ProviderError(PROVIDER, "IMD canonical JSON allows integers only.")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                f"{json.dumps(str(key), ensure_ascii=False)}:{canonical_json(value[key])}"
                for key in sorted(value, key=str)
            )
            + "}"
        )
    raise ProviderError(PROVIDER, f"Unsupported value in IMD canonical JSON: {type(value).__name__}.")


def payment_hash(payment: dict[str, Any]) -> str:
    return "0x" + hashlib.sha256(canonical_json(payment).encode("utf-8")).hexdigest()


def input_hash(action: str, action_input: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json({"action": action, "input": action_input}).encode("utf-8")).hexdigest()


def encode_payment_header(payment: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(payment, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).decode(
        "ascii"
    )


def format_imd(amount_raw: int | str) -> str:
    amount = int(amount_raw)
    whole, frac = divmod(amount, 10**IMD_DECIMALS)
    frac_text = str(frac).rjust(IMD_DECIMALS, "0").rstrip("0")
    return f"{whole}.{frac_text}" if frac_text else str(whole)


def _same_address(left: Any, right: Any) -> bool:
    return isinstance(left, str) and isinstance(right, str) and left.lower() == right.lower()


def validate_action(action: Any) -> str:
    if not isinstance(action, str) or action not in IMD_ENABLED_ACTIONS:
        raise ProviderError(
            PROVIDER,
            f"action must be one of: {', '.join(IMD_ENABLED_ACTIONS)}.",
            details={"action": action},
        )
    return action


def validate_payment_terms(payment: Any, *, where: str) -> dict[str, Any]:
    """Check an IMD payment block (quote or challenge) against the pinned terms."""
    if not isinstance(payment, dict):
        raise ProviderError(PROVIDER, f"IMD {where} has no payment terms.")
    problems = []
    if payment.get("network") != IMD_NETWORK:
        problems.append(f"network {payment.get('network')!r} is not {IMD_NETWORK}")
    if not _same_address(payment.get("asset"), IMD_TOKEN_ADDRESS):
        problems.append("asset is not the IMD token")
    if not any(_same_address(payment.get("payTo"), allowed) for allowed in IMD_PAY_TO_ALLOWLIST):
        problems.append("payTo is not the known IMD treasury")
    try:
        amount = int(str(payment.get("amount")))
    except (TypeError, ValueError):
        amount = 0
        problems.append("amount is not an integer")
    if amount <= 0 or amount > IMD_MAX_PER_PAYMENT_RAW:
        problems.append("amount is outside (0, 5 IMD]")
    if problems:
        raise ProviderError(
            PROVIDER,
            f"IMD {where} payment terms do not match the pinned IMD terms ({'; '.join(problems)}); nothing was signed.",
            details={"problems": problems, "payment": payment},
        )
    return payment


def validate_challenge(challenge: Any, quote: dict[str, Any]) -> dict[str, Any]:
    """Validate the 402 challenge and return its accepts[0]."""
    if not isinstance(challenge, dict):
        raise ProviderError(PROVIDER, "IMD submit challenge is not a JSON object.")
    accepts = challenge.get("accepts")
    if not isinstance(accepts, list) or not accepts or not isinstance(accepts[0], dict):
        raise ProviderError(PROVIDER, "IMD challenge has no accepts[0].")
    requirement = accepts[0]
    validate_payment_terms(requirement, where="challenge")
    if requirement.get("scheme") != "exact":
        raise ProviderError(PROVIDER, "IMD challenge scheme is not 'exact'.")
    extra = requirement.get("extra") if isinstance(requirement.get("extra"), dict) else {}
    if extra.get("assetTransferMethod") != "permit2":
        raise ProviderError(PROVIDER, "IMD challenge does not ask for a Permit2 transfer.")
    quote_payment = quote.get("payment") or {}
    for key in ("amount", "asset", "payTo", "network"):
        left, right = requirement.get(key), quote_payment.get(key)
        same = _same_address(left, right) if key in {"asset", "payTo"} else str(left) == str(right)
        if not same:
            raise ProviderError(
                PROVIDER,
                f"IMD challenge {key} differs from the quote; nothing was signed.",
                details={"challenge": left, "quote": right},
            )
    resource_url = challenge.get("resourceUrl")
    if not isinstance(resource_url, str) or not resource_url.startswith(f"{IMD_API_ORIGIN}/"):
        raise ProviderError(PROVIDER, "IMD challenge resourceUrl is not an api.imd.fun URL.")
    scope = str(challenge.get("requesterScopeHash") or "").removeprefix("0x")
    if len(scope) != 64:
        raise ProviderError(PROVIDER, "IMD challenge requesterScopeHash is not 32 bytes.")
    return requirement


def build_permit(
    *,
    requirement: dict[str, Any],
    owner: str,
    quote_expires_at: int,
    now: int | None = None,
    nonce: int | None = None,
) -> dict[str, Any]:
    current = int(time.time()) if now is None else int(now)
    deadline = min(current + PERMIT_MAX_LIFETIME_SECONDS, quote_expires_at - PERMIT_DEADLINE_MARGIN_SECONDS)
    if deadline <= current:
        raise ProviderError(PROVIDER, "The IMD quote expires too soon to pay; request a fresh quote.")
    return {
        "from": owner,
        "permitted": {"token": requirement["asset"], "amount": str(requirement["amount"])},
        "spender": X402_EXACT_PERMIT2_PROXY_ADDRESS,
        "nonce": str(secrets.randbits(256) if nonce is None else nonce),
        "deadline": str(deadline),
        "witness": {"to": requirement["payTo"], "validAfter": str(current - 600)},
    }


def build_payment(
    *,
    challenge: dict[str, Any],
    requirement: dict[str, Any],
    permit: dict[str, Any],
    signature: str,
) -> dict[str, Any]:
    """Assemble the payment exactly as IMD's strict parser accepts it."""
    payment: dict[str, Any] = {
        "x402Version": 2,
        "accepted": json.loads(json.dumps(requirement)),
        "payload": {"signature": signature, "permit2Authorization": permit},
    }
    resource = challenge.get("resource")
    if isinstance(resource, dict):
        payment["resource"] = {
            key: resource[key] for key in ("url", "description", "mimeType") if key in resource
        }
        payment["resource"]["url"] = challenge["resourceUrl"]
    return payment


def build_quote_approval(
    *,
    challenge: dict[str, Any],
    quote: dict[str, Any],
    payment: dict[str, Any],
) -> dict[str, Any]:
    quote_payment = quote["payment"]
    return {
        "resource": challenge["resourceUrl"],
        "requesterScopeHash": "0x" + str(challenge["requesterScopeHash"]).removeprefix("0x"),
        "quoteId": quote["id"],
        "quoteHash": "0x" + str(quote["quoteHash"]).removeprefix("0x"),
        "paymentHash": payment_hash(payment),
        "action": quote["action"],
        "asset": quote_payment["asset"],
        "amount": str(quote_payment["amount"]),
        "payTo": quote_payment["payTo"],
        "expiresAt": str(quote["expiresAt"]),
    }


# --------------------------------------------------------------------------
# Local state: the request token and the order journal
# --------------------------------------------------------------------------


def _state_dir() -> Path:
    override = os.getenv("AGENT_WALLET_IMD_STATE_DIR")
    path = Path(override).expanduser() if override else resolve_openclaw_home() / "agent-wallet" / "imd"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _token_path(owner: str) -> Path:
    return _state_dir() / f"token-{owner.lower()}"


def _journal_path(owner: str) -> Path:
    return _state_dir() / f"orders-{owner.lower()}.jsonl"


def load_or_create_request_token(owner: str) -> str:
    """One IMD request token per wallet; it reads that wallet's orders later."""
    path = _token_path(owner)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) == 64:
            return token
    token = secrets.token_hex(32)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def append_journal(owner: str, entry: dict[str, Any]) -> None:
    fd = os.open(str(_journal_path(owner)), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, separators=(",", ":")) + "\n")


def read_journal(owner: str, *, limit: int = 50) -> list[dict[str, Any]]:
    path = _journal_path(owner)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries[-limit:]


def daily_limit_raw() -> int:
    raw = os.getenv("AGENT_WALLET_IMD_DAILY_LIMIT_RAW")
    if raw and raw.strip().isdigit():
        return int(raw.strip())
    return IMD_DEFAULT_DAILY_LIMIT_RAW


def spent_last_day_raw(owner: str, *, now: int | None = None) -> int:
    current = int(time.time()) if now is None else int(now)
    total = 0
    for entry in read_journal(owner, limit=10_000):
        if entry.get("event") != "signed":
            continue
        if current - int(entry.get("ts") or 0) > DAY_SECONDS:
            continue
        try:
            total += int(entry.get("amount_raw") or 0)
        except (TypeError, ValueError):
            continue
    return total


def assert_within_daily_limit(owner: str, amount_raw: int) -> None:
    limit = daily_limit_raw()
    spent = spent_last_day_raw(owner)
    if spent + amount_raw > limit:
        raise ProviderError(
            PROVIDER,
            f"IMD daily limit reached: {format_imd(spent)} of {format_imd(limit)} IMD signed in the last 24h; "
            f"this payment needs {format_imd(amount_raw)} more.",
            details={"spent_raw": str(spent), "limit_raw": str(limit), "amount_raw": str(amount_raw)},
        )


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


async def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    expected: tuple[int, ...] = (200,),
) -> tuple[int, Any]:
    if not path.startswith("/"):
        raise ProviderError(PROVIDER, "IMD path must start with '/'.")
    request_headers = {"Accept": "application/json"}
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    if headers:
        request_headers.update(headers)
    client = get_client()
    try:
        response = await client.request(
            method,
            f"{IMD_API_ORIGIN}{path}",
            headers=request_headers,
            json=json_body,
            follow_redirects=False,
        )
    except Exception as exc:
        raise ProviderError(
            PROVIDER,
            f"IMD request failed before a response: {type(exc).__name__}.",
            details={"path": path, "transport_error": True},
        ) from exc
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": (response.text or "")[:2000]}
    if response.status_code not in expected:
        error = payload.get("error") if isinstance(payload, dict) else None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise ProviderError(
            PROVIDER,
            f"IMD {method} {path} returned {response.status_code}"
            + (f" {error}" if error else "")
            + (f": {detail}" if detail else ""),
            details={"status": response.status_code, "path": path, "response": payload},
        )
    return response.status_code, payload


async def get_capabilities() -> dict[str, Any]:
    _, payload = await _request("GET", "/requests/capabilities")
    return payload


async def check_request(action: str, action_input: dict[str, Any]) -> dict[str, Any]:
    _, payload = await _request(
        "POST", "/requests/check", json_body={"action": action, "input": action_input}
    )
    return payload


async def import_repository(url: str, kind: str, ref: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"url": url, "kind": kind}
    if ref:
        body["ref"] = ref
    _, payload = await _request("POST", "/requests/import", json_body=body)
    return payload


async def get_order(token: str, order_id: str) -> dict[str, Any]:
    _, payload = await _request("GET", f"/requests/{_path_id(order_id)}", token=token)
    return payload


async def get_paid_by(address: str) -> dict[str, Any]:
    _, payload = await _request("GET", f"/requests/paid-by/{_path_id(address)}")
    return payload


async def get_job(job_id: str) -> dict[str, Any]:
    _, payload = await _request("GET", f"/jobs/{_path_id(job_id)}")
    return payload


async def get_job_result(job_id: str) -> dict[str, Any]:
    _, payload = await _request("GET", f"/jobs/{_path_id(job_id)}/result", expected=(200, 404, 409))
    return payload


def _path_id(value: str) -> str:
    text = str(value or "").strip()
    if not text or not all(ch.isalnum() or ch in "-_" for ch in text) or len(text) > 128:
        raise ProviderError(PROVIDER, "IMD id must be alphanumeric (with - or _).", details={"id": value})
    return text


# --------------------------------------------------------------------------
# Account readiness
# --------------------------------------------------------------------------


async def account_status(backend: Any) -> dict[str, Any]:
    state = await backend.get_imd_account_state()
    capabilities = await get_capabilities()
    actions = {
        item.get("action"): item
        for item in capabilities.get("actions", [])
        if isinstance(item, dict)
    }
    job_open = actions.get("job.open") or {}
    price_raw = int(str((job_open.get("payment") or {}).get("amount") or IMD_PRICE_PER_ACTION_RAW))
    terms_ok = True
    terms_problem = None
    try:
        validate_payment_terms(job_open.get("payment"), where="capabilities")
    except ProviderError as exc:
        terms_ok, terms_problem = False, str(exc)
    balance = int(state["imdBalanceRaw"])
    allowance = int(state["permit2AllowanceRaw"])
    owner = state["address"]
    spent = spent_last_day_raw(owner)
    return {
        "address": owner,
        "network": "ethereum",
        "token": IMD_TOKEN_ADDRESS,
        "imd_balance_raw": str(balance),
        "imd_balance": format_imd(balance),
        "permit2_allowance_raw": str(allowance),
        "permit2_allowance": format_imd(allowance),
        "eth_balance_wei": state["ethBalanceWei"],
        "price_per_action_raw": str(price_raw),
        "price_per_action": format_imd(price_raw),
        "actions_affordable": balance // price_raw if price_raw else 0,
        "actions_covered_by_allowance": allowance // price_raw if price_raw else 0,
        "ready": terms_ok and balance >= price_raw and allowance >= price_raw,
        "needs_imd": balance < price_raw,
        "needs_permit2_allowance": allowance < price_raw,
        "pinned_terms_match": terms_ok,
        "pinned_terms_problem": terms_problem,
        "per_payment_limit": format_imd(IMD_MAX_PER_PAYMENT_RAW),
        "daily_limit": format_imd(daily_limit_raw()),
        "signed_last_24h": format_imd(spent),
        "enabled_actions": list(IMD_ENABLED_ACTIONS),
        "quote_ttl_seconds": job_open.get("quoteTtlSeconds"),
        "limits": capabilities.get("limits"),
    }


# --------------------------------------------------------------------------
# The paid flow
# --------------------------------------------------------------------------


async def preview_paid_action(backend: Any, *, action: str, action_input: dict[str, Any]) -> dict[str, Any]:
    """Free: IMD's /requests/check plus the price, nothing held or signed."""
    validate_action(action)
    if not isinstance(action_input, dict) or not action_input:
        raise ProviderError(PROVIDER, "input must be a non-empty object (the IMD action body).")
    if len(canonical_json(action_input).encode("utf-8")) > 15_000:
        raise ProviderError(PROVIDER, "IMD request bodies are limited to 16 KiB.")
    status = await account_status(backend)
    check = await check_request(action, action_input)
    blockers = check.get("blockers") if isinstance(check, dict) else None
    amount_raw = _expected_amount_raw(action, action_input, int(status["price_per_action_raw"]))
    return {
        "provider": PROVIDER,
        "network": "ethereum",
        "action": action,
        "input": action_input,
        "input_sha256": input_hash(action, action_input),
        "owner": status["address"],
        "amount_raw": str(amount_raw),
        "amount": format_imd(amount_raw),
        "asset": IMD_TOKEN_ADDRESS,
        "asset_symbol": "IMD",
        "pay_to": sorted(IMD_PAY_TO_ALLOWLIST)[0],
        "check": check,
        "blocked": bool(blockers),
        "account": status,
        "terms": {
            "refundable": False,
            "result_guaranteed": False,
            "public_results": True,
            "note": "IMD job pages, results and artifacts are public. github:true also commits the "
            "output to an IMD-owned public GitHub repository.",
        },
    }


def _expected_amount_raw(action: str, action_input: dict[str, Any], price_raw: int) -> int:
    if action in {"schedule.create", "schedule.topup"}:
        runs = action_input.get("runs")
        if not isinstance(runs, int) or isinstance(runs, bool) or runs < 1:
            raise ProviderError(PROVIDER, "A schedule needs a positive integer 'runs'.")
        amount = price_raw * runs
    else:
        amount = price_raw
    if amount > IMD_MAX_PER_PAYMENT_RAW:
        raise ProviderError(
            PROVIDER,
            f"This payment would be {format_imd(amount)} IMD, above the 5 IMD per-payment limit. "
            "Split it into smaller schedules or top-ups.",
        )
    return amount


async def pay_paid_action(
    backend: Any,
    *,
    action: str,
    action_input: dict[str, Any],
    approved_amount_raw: int,
    purpose: str,
) -> dict[str, Any]:
    """Quote, challenge, sign twice, submit, then follow the order to admission."""
    validate_action(action)
    owner = await backend.get_address()
    if not owner:
        raise ProviderError(PROVIDER, "The EVM wallet has no address.")
    token = load_or_create_request_token(owner)
    request_key = str(uuid.uuid4())

    _, quoted = await _request(
        "POST",
        "/requests/quote",
        token=token,
        json_body={"requestKey": request_key, "action": action, "input": action_input},
        expected=(200, 201),
    )
    order = quoted.get("order") if isinstance(quoted, dict) else None
    quote = order.get("quote") if isinstance(order, dict) else None
    if not isinstance(quote, dict) or not order.get("id"):
        raise ProviderError(PROVIDER, "IMD quote response has no order or quote.", details={"response": quoted})
    order_id = str(order["id"])
    validate_payment_terms(quote.get("payment"), where="quote")
    amount_raw = int(str(quote["payment"]["amount"]))
    if amount_raw != int(approved_amount_raw):
        raise ProviderError(
            PROVIDER,
            f"IMD quoted {format_imd(amount_raw)} IMD but {format_imd(approved_amount_raw)} IMD was approved; "
            "nothing was signed. Preview again.",
            details={"order_id": order_id},
        )
    if quote.get("action") != action:
        raise ProviderError(PROVIDER, "IMD quote is for a different action; nothing was signed.")
    expires_at = int(quote.get("expiresAt") or 0)
    if expires_at - int(time.time()) < MIN_QUOTE_REMAINING_SECONDS:
        raise ProviderError(PROVIDER, "IMD quote expires too soon; preview and pay again.")

    submit_path = f"/requests/{_path_id(order_id)}/submit"
    _, challenge = await _request("POST", submit_path, token=token, expected=(402,))
    requirement = validate_challenge(challenge, quote)
    challenge_quote = challenge.get("quote") if isinstance(challenge.get("quote"), dict) else {}
    if challenge_quote.get("quoteHash") not in (None, quote.get("quoteHash")):
        raise ProviderError(PROVIDER, "IMD challenge quoteHash differs from the quote; nothing was signed.")

    assert_within_daily_limit(owner, amount_raw)
    permit = build_permit(requirement=requirement, owner=owner, quote_expires_at=expires_at)
    permit_signed = await backend.sign_imd_permit2(permit)
    payment = build_payment(
        challenge=challenge,
        requirement=requirement,
        permit=permit,
        signature=permit_signed["signature"],
    )
    quote_approval = build_quote_approval(challenge=challenge, quote=quote, payment=payment)
    approval_signed = await backend.sign_imd_quote_approval(quote_approval)
    append_journal(
        owner,
        {
            "event": "signed",
            "ts": int(time.time()),
            "order_id": order_id,
            "action": action,
            "amount_raw": str(amount_raw),
            "request_key": request_key,
            "input_sha256": input_hash(action, action_input),
            "purpose": purpose[:200],
        },
    )

    header = encode_payment_header(payment)
    body = {"quoteSignature": approval_signed["signature"]}
    submit_status = None
    submit_payload: Any = None
    for attempt in range(2):
        # Retries reuse the order and the same bytes, so IMD never charges twice.
        try:
            submit_status, submit_payload = await _request(
                "POST",
                submit_path,
                token=token,
                json_body=body,
                headers={"PAYMENT-SIGNATURE": header},
                expected=(200, 202),
            )
            break
        except ProviderError as exc:
            if not (exc.details or {}).get("transport_error") or attempt == 1:
                raise

    status = submit_payload if isinstance(submit_payload, dict) else {}
    for _ in range(ORDER_POLL_ATTEMPTS):
        if status.get("status") in {"admitted", "payment_failed", "expired", "refused"}:
            break
        await _sleep(ORDER_POLL_INTERVAL_SECONDS)
        status = await get_order(token, order_id)

    admission = status.get("admission") if isinstance(status, dict) else None
    result = admission.get("result") if isinstance(admission, dict) else None
    append_journal(
        owner,
        {
            "event": "submitted",
            "ts": int(time.time()),
            "order_id": order_id,
            "status": status.get("status"),
            "job_id": result.get("jobId") if isinstance(result, dict) else None,
            "schedule_id": result.get("scheduleId") if isinstance(result, dict) else None,
        },
    )
    return {
        "provider": PROVIDER,
        "network": "ethereum",
        "action": action,
        "order_id": order_id,
        "submit_http_status": submit_status,
        "status": status.get("status"),
        "payment": status.get("payment"),
        "admission": admission,
        "amount_raw": str(amount_raw),
        "amount": format_imd(amount_raw),
        "pay_to": quote["payment"]["payTo"],
        "links": _result_links(result),
        "next_step": (
            "Follow the job with imd_get_job; work takes minutes to hours."
            if status.get("status") == "admitted"
            else "Payment is still settling; check again with imd_get_order before paying anything again."
        ),
    }


def _result_links(result: Any) -> dict[str, str]:
    if not isinstance(result, dict):
        return {}
    links = {}
    for key in ("statusUrl", "resultUrl", "jobUrl", "attestationUrl"):
        value = result.get(key)
        if isinstance(value, str) and value.startswith("/"):
            links[key] = f"{IMD_API_ORIGIN}{value}"
    return links


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def order_status(backend: Any, *, order_id: str | None) -> dict[str, Any]:
    owner = await backend.get_address()
    if not owner:
        raise ProviderError(PROVIDER, "The EVM wallet has no address.")
    if order_id:
        token = load_or_create_request_token(owner)
        return {"order_id": order_id, "order": await get_order(token, order_id)}
    return {
        "address": owner,
        "local_orders": [entry for entry in read_journal(owner) if entry.get("event") == "submitted"],
        "paid_by": await get_paid_by(owner),
    }
