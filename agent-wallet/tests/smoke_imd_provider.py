"""Smoke coverage for the IMD paid-action provider (no network, no daemon)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["AGENT_WALLET_IMD_STATE_DIR"] = tempfile.mkdtemp(prefix="imd-state-")

from agent_wallet.exceptions import ProviderError  # noqa: E402
from agent_wallet.providers import imd  # noqa: E402

OWNER = "0xDF7eD8B45ae91a0881c83A876747AF1FfB48C36E"
PAY_TO = "0x4e0fa57bde726079356537e2f34d671e9f41adbc"
HALF = "500000000000000000"

# Same fixture and digest as wdk-evm-wallet/tests/unit_imd.mjs, which hashes
# it with IMD's reference JavaScript canon().
PAYMENT_HASH_FIXTURE = {
    "x402Version": 2,
    "resource": {
        "url": "https://api.imd.fun/requests/ORDER_ID/submit",
        "description": "IMD job.open — ü",
        "mimeType": "application/json",
    },
    "accepted": {
        "scheme": "exact",
        "network": "eip155:1",
        "asset": imd.IMD_TOKEN_ADDRESS,
        "amount": HALF,
        "payTo": PAY_TO,
        "maxTimeoutSeconds": 300,
        "extra": {"assetTransferMethod": "permit2"},
    },
    "payload": {
        "signature": "0x" + "ab" * 65,
        "permit2Authorization": {
            "from": OWNER,
            "permitted": {"token": imd.IMD_TOKEN_ADDRESS, "amount": HALF},
            "spender": imd.X402_EXACT_PERMIT2_PROXY_ADDRESS,
            "nonce": "123456789",
            "deadline": "1790000300",
            "witness": {"to": PAY_TO, "validAfter": "1789999400"},
        },
    },
}
JS_REFERENCE_DIGEST = "0x90b1544310eb9f669a9082d03441fb4badf33534d5c941e48562488f458e9a4f"


def expect_error(fn, fragment: str) -> None:
    try:
        fn()
    except ProviderError as exc:
        assert fragment in str(exc), f"expected {fragment!r} in {exc}"
        return
    raise AssertionError(f"expected ProviderError containing {fragment!r}")


def test_canonical_json_matches_js_reference() -> None:
    assert imd.payment_hash(PAYMENT_HASH_FIXTURE) == JS_REFERENCE_DIGEST
    assert imd.canonical_json({"b": [1, None, True], "a": "x"}) == '{"a":"x","b":[1,null,true]}'
    expect_error(lambda: imd.canonical_json({"a": 1.5}), "integers only")


def _quote(expires_in: int = 500, **payment_overrides) -> dict:
    payment = {
        "network": "eip155:1",
        "asset": imd.IMD_TOKEN_ADDRESS,
        "amount": HALF,
        "payTo": PAY_TO,
        "decimals": 18,
        "scheme": "exact",
        **payment_overrides,
    }
    return {
        "v": 1,
        "id": "ORDER_1",
        "action": "job.open",
        "inputHash": "ih",
        "issuedAt": int(time.time()),
        "expiresAt": int(time.time()) + expires_in,
        "payment": payment,
        "quoteHash": "cd" * 32,
    }


def _challenge(quote: dict, **requirement_overrides) -> dict:
    requirement = {
        "scheme": "exact",
        "network": "eip155:1",
        "asset": imd.IMD_TOKEN_ADDRESS,
        "amount": HALF,
        "payTo": PAY_TO,
        "maxTimeoutSeconds": 600,
        "extra": {"assetTransferMethod": "permit2"},
        **requirement_overrides,
    }
    return {
        "x402Version": 2,
        "accepts": [requirement],
        "resource": {"url": "https://api.imd.fun/requests/ORDER_1/submit", "mimeType": "application/json", "x": 1},
        "resourceUrl": "https://api.imd.fun/requests/ORDER_1/submit",
        "requesterScopeHash": "ef" * 32,
        "quote": quote,
        "input": {},
    }


def test_challenge_validation_refuses_tampering() -> None:
    quote = _quote()
    assert imd.validate_challenge(_challenge(quote), quote)["amount"] == HALF
    expect_error(lambda: imd.validate_challenge(_challenge(quote, payTo="0x" + "11" * 20), quote), "treasury")
    expect_error(lambda: imd.validate_challenge(_challenge(quote, network="eip155:8453"), quote), "eip155:1")
    expect_error(lambda: imd.validate_challenge(_challenge(quote, amount="600000000000000000"), quote), "differs")
    expect_error(
        lambda: imd.validate_challenge(_challenge(quote, extra={"assetTransferMethod": "eip3009"}), quote), "Permit2"
    )
    bad = _challenge(quote)
    bad["resourceUrl"] = "https://evil.example/x"
    expect_error(lambda: imd.validate_challenge(bad, quote), "api.imd.fun")
    expect_error(lambda: imd.validate_payment_terms({**quote["payment"], "amount": str(6 * 10**18)}, where="q"), "5 IMD")


def test_permit_and_payment_shape() -> None:
    quote = _quote()
    challenge = _challenge(quote)
    requirement = challenge["accepts"][0]
    now = int(time.time())
    permit = imd.build_permit(requirement=requirement, owner=OWNER, quote_expires_at=quote["expiresAt"], now=now)
    assert int(permit["deadline"]) <= quote["expiresAt"] - imd.PERMIT_DEADLINE_MARGIN_SECONDS
    assert int(permit["deadline"]) <= now + imd.PERMIT_MAX_LIFETIME_SECONDS
    assert permit["spender"] == imd.X402_EXACT_PERMIT2_PROXY_ADDRESS
    payment = imd.build_payment(challenge=challenge, requirement=requirement, permit=permit, signature="0xsig")
    assert set(payment) == {"x402Version", "accepted", "payload", "resource"}
    assert "extensions" not in payment
    assert payment["accepted"] == requirement
    assert set(payment["resource"]) == {"url", "mimeType"}
    assert set(payment["payload"]["permit2Authorization"]) == {
        "from", "permitted", "spender", "nonce", "deadline", "witness"
    }
    decoded = json.loads(base64.b64decode(imd.encode_payment_header(payment)))
    assert imd.payment_hash(decoded) == imd.payment_hash(payment)
    approval = imd.build_quote_approval(challenge=challenge, quote=quote, payment=payment)
    assert approval["paymentHash"] == imd.payment_hash(payment)
    assert approval["requesterScopeHash"] == "0x" + "ef" * 32
    expect_error(
        lambda: imd.build_permit(requirement=requirement, owner=OWNER, quote_expires_at=now + 10, now=now),
        "expires too soon",
    )


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeImdApi:
    def __init__(self, *, quote_amount: str = HALF):
        self.quote = _quote(**({"amount": quote_amount} if quote_amount != HALF else {}))
        self.calls: list[tuple[str, str, dict]] = []
        self.submitted: dict | None = None

    async def request(self, method, url, *, headers=None, json=None, follow_redirects=True):
        assert follow_redirects is False
        assert url.startswith("https://api.imd.fun/")
        path = url.removeprefix("https://api.imd.fun")
        self.calls.append((method, path, dict(headers or {})))
        if path == "/requests/capabilities":
            return FakeResponse(200, {"actions": [{"action": "job.open", "payment": self.quote["payment"], "quoteTtlSeconds": 600}]})
        if path == "/requests/check":
            return FakeResponse(200, {"action": "job.open", "blockers": [], "suggestions": []})
        if path == "/requests/quote":
            assert headers["Authorization"].startswith("Bearer ")
            return FakeResponse(201, {"created": True, "order": {"id": "ORDER_1", "status": "quoted", "quote": self.quote}})
        if path == "/requests/ORDER_1/submit" and "PAYMENT-SIGNATURE" not in (headers or {}):
            return FakeResponse(402, _challenge(self.quote))
        if path == "/requests/ORDER_1/submit":
            self.submitted = {"header": headers["PAYMENT-SIGNATURE"], "body": json}
            return FakeResponse(202, {"status": "admission_pending"})
        if path == "/requests/ORDER_1":
            return FakeResponse(
                200,
                {
                    "status": "admitted",
                    "payment": {"status": "confirmed", "paid": True, "transactionHash": "0xabc"},
                    "admission": {"action": "job.open", "result": {"kind": "job", "jobId": "JOB_1", "statusUrl": "/jobs/JOB_1", "resultUrl": "/jobs/JOB_1/result"}},
                },
            )
        raise AssertionError(f"unexpected {method} {path}")


class FakeEvmBackend:
    network = "ethereum"

    def __init__(self):
        self.permits: list[dict] = []
        self.approvals: list[dict] = []

    async def get_address(self):
        return OWNER

    async def get_imd_account_state(self):
        return {
            "address": OWNER,
            "imdBalanceRaw": "700000000000000000",
            "permit2AllowanceRaw": HALF,
            "ethBalanceWei": "1000",
        }

    async def sign_imd_permit2(self, permit):
        self.permits.append(permit)
        return {"signature": "0x" + "aa" * 65}

    async def sign_imd_quote_approval(self, approval):
        self.approvals.append(approval)
        return {"signature": "0x" + "bb" * 65}


def test_full_paid_flow() -> None:
    api = FakeImdApi()
    imd.get_client = lambda: api  # type: ignore[assignment]
    imd.ORDER_POLL_INTERVAL_SECONDS = 0
    backend = FakeEvmBackend()
    body = {"objective": "Write a short report.", "skill": "research-report", "github": False}

    preview = asyncio.run(imd.preview_paid_action(backend, action="job.open", action_input=body))
    assert preview["amount_raw"] == HALF and preview["account"]["ready"] is True
    assert preview["blocked"] is False

    result = asyncio.run(
        imd.pay_paid_action(backend, action="job.open", action_input=body, approved_amount_raw=int(HALF), purpose="test")
    )
    assert result["status"] == "admitted"
    assert result["links"]["statusUrl"] == "https://api.imd.fun/jobs/JOB_1"
    sent_payment = json.loads(base64.b64decode(api.submitted["header"]))
    assert backend.approvals[0]["paymentHash"] == imd.payment_hash(sent_payment)
    assert api.submitted["body"] == {"quoteSignature": "0x" + "bb" * 65}
    assert sent_payment["payload"]["signature"] == "0x" + "aa" * 65
    journal = imd.read_journal(OWNER)
    assert [entry["event"] for entry in journal][-2:] == ["signed", "submitted"]
    assert journal[-1]["job_id"] == "JOB_1"
    token_file = Path(os.environ["AGENT_WALLET_IMD_STATE_DIR"]) / f"token-{OWNER.lower()}"
    assert oct(token_file.stat().st_mode & 0o777) == "0o600"


def test_quote_amount_must_match_approval() -> None:
    api = FakeImdApi(quote_amount="1000000000000000000")
    imd.get_client = lambda: api  # type: ignore[assignment]
    backend = FakeEvmBackend()
    try:
        asyncio.run(
            imd.pay_paid_action(
                backend, action="job.open", action_input={"objective": "x", "skill": "create-image"},
                approved_amount_raw=int(HALF), purpose="test",
            )
        )
    except ProviderError as exc:
        assert "was approved" in str(exc)
    else:
        raise AssertionError("expected an amount mismatch error")
    assert backend.permits == [] and backend.approvals == []


def test_daily_limit_blocks_signing() -> None:
    os.environ["AGENT_WALLET_IMD_DAILY_LIMIT_RAW"] = HALF
    try:
        api = FakeImdApi()
        imd.get_client = lambda: api  # type: ignore[assignment]
        backend = FakeEvmBackend()
        try:
            asyncio.run(
                imd.pay_paid_action(
                    backend, action="job.open", action_input={"objective": "y", "skill": "create-image"},
                    approved_amount_raw=int(HALF), purpose="test",
                )
            )
        except ProviderError as exc:
            assert "daily limit" in str(exc)
        else:
            raise AssertionError("expected the daily limit to block a second payment")
        assert backend.permits == []
    finally:
        os.environ.pop("AGENT_WALLET_IMD_DAILY_LIMIT_RAW", None)


def test_schedule_amount_and_ceiling() -> None:
    assert imd._expected_amount_raw("schedule.create", {"runs": 4}, int(HALF)) == 2 * 10**18
    expect_error(lambda: imd._expected_amount_raw("schedule.create", {"runs": 11}, int(HALF)), "5 IMD")
    expect_error(lambda: imd.validate_action("launch.open"), "action must be one of")


if __name__ == "__main__":
    test_canonical_json_matches_js_reference()
    test_challenge_validation_refuses_tampering()
    test_permit_and_payment_shape()
    test_full_paid_flow()
    test_quote_amount_must_match_approval()
    test_daily_limit_blocks_signing()
    test_schedule_amount_and_ceiling()
    print("smoke_imd_provider: ok")
