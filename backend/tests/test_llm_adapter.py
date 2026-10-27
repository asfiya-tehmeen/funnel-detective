"""AnthropicProvider against Messages-API-shaped responses.

Unlike FakeProvider in test_ai_layer.py, these tests run the REAL adapter and the REAL Anthropic SDK
request-building and response-parsing code. Only the HTTP transport is replaced (httpx2.MockTransport),
so we can assert exactly what would go over the wire and how every kind of reply is handled.

The response bodies are HAND-WRITTEN to the documented response schema; they were not captured from the
live API (no key was available when these were written). So these tests prove the adapter's handling of
well-formed and malformed replies and its request shape, but NOT that the live API accepts these
parameters. That needs `python -m scripts.llm_smoke_test` with a real ANTHROPIC_API_KEY.
"""
import json

import anthropic
import httpx2
import pytest

from app.engine.orchestrator import investigate
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMError

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"],
          "additionalProperties": False}


def message(text: str | None, stop_reason: str = "end_turn", blocks: list | None = None) -> dict:
    """A Messages API response body shaped like a recorded real one."""
    content = blocks if blocks is not None else ([{"type": "text", "text": text}] if text is not None else [])
    return {"id": "msg_recorded_01", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 30}}


def provider_for(handler, model: str = "claude-opus-5-5") -> tuple[AnthropicProvider, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def wrapped(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    client = anthropic.Anthropic(api_key="test-key-not-real", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(wrapped)))
    return AnthropicProvider(api_key=None, model=model, client=client), seen


def ok(body: dict, status: int = 200):
    return lambda request: httpx2.Response(status, json=body)


def test_request_shape_on_the_wire():
    prov, seen = provider_for(ok(message('{"answer": "hi"}')))
    out = prov.complete_json("SYSTEM PROMPT", "USER PROMPT", SCHEMA, max_tokens=1234)
    assert out == {"answer": "hi"}
    req = seen[0]
    assert req.url.path.endswith("/v1/messages")
    assert "server-side-fallback-2026-07-01" in req.headers["anthropic-beta"]
    body = json.loads(req.content)
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 1234
    assert body["system"] == "SYSTEM PROMPT"
    assert body["messages"] == [{"role": "user", "content": "USER PROMPT"}]
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert body["fallbacks"] == "default"
    # Sampling parameters and forced tool use are rejected by current models, so we must not send them.
    for banned in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
        assert banned not in body


def test_api_key_is_sent_in_header_not_body():
    prov, seen = provider_for(ok(message('{"answer": "x"}')))
    prov.complete_json("s", "u", SCHEMA)
    assert seen[0].headers["x-api-key"] == "test-key-not-real"
    assert b"test-key-not-real" not in seen[0].content


def test_skips_non_text_blocks_and_reads_the_text_block():
    blocks = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": '{"answer": "z"}'}]
    prov, _ = provider_for(ok(message(None, blocks=blocks)))
    assert prov.complete_json("s", "u", SCHEMA) == {"answer": "z"}


@pytest.mark.parametrize("body, expected", [
    (message('{"answer": "partial', stop_reason="max_tokens"), "truncated"),
    (message("", stop_reason="refusal"), "declined"),
    (message(None), "no text"),
    (message("not json at all"), "invalid JSON"),
    (message("[1, 2, 3]"), "non-object"),
])
def test_bad_replies_become_llm_error(body, expected):
    prov, _ = provider_for(ok(body))
    with pytest.raises(LLMError, match=expected):
        prov.complete_json("s", "u", SCHEMA)


@pytest.mark.parametrize("status, expected", [
    (401, "authentication failed"), (429, "rate limited"), (500, "API error 500"), (529, "API error 529"),
])
def test_http_errors_become_llm_error(status, expected):
    err = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    prov, _ = provider_for(ok(err, status))
    with pytest.raises(LLMError, match=expected):
        prov.complete_json("s", "u", SCHEMA)


def test_400_surfaces_the_api_message_for_diagnosis():
    err = {"type": "error", "error": {"type": "invalid_request_error", "message": "output_config.format: schema not supported"}}
    prov, _ = provider_for(ok(err, 400))
    with pytest.raises(LLMError, match=r"rejected \(400\).*schema not supported") as exc:
        prov.complete_json("s", "u", SCHEMA)
    assert "test-key-not-real" not in str(exc.value)


def test_connection_failure_becomes_llm_error():
    def boom(request):
        raise httpx2.ConnectError("no route to host", request=request)

    prov, _ = provider_for(boom)
    with pytest.raises(LLMError, match="unreachable"):
        prov.complete_json("s", "u", SCHEMA)


# --------------------------------------------------------------------------- full pipeline through the real adapter
PLAN = {"intent": "explain_change", "metric": "listing_to_contact", "rationale": "Riyadh mobile web.",
        "filters": {"market": ["Riyadh"], "device": ["mobile_web"], "channel": [], "property_type": [],
                    "price_band": [], "intent": [], "visitor_type": []},
        "extra_steps": []}


def test_end_to_end_with_recorded_planner_and_narrator_replies(cur):
    """Real adapter + SDK parsing + orchestrator + validator, with only the network recorded."""
    base = investigate("Why did mobile contact conversion decline in Riyadh?", cur)
    o = base.finding.observed_change
    narrator = {"summary": f"Listing to contact fell from {o.baseline_rate * 100:.1f}% to {o.current_rate * 100:.1f}%.",
                "key_points": [{"text": "Form completion is where the drop concentrates.",
                                "evidence_ids": [base.evidence[0].id], "hypothesis_id": base.hypotheses[0].id,
                                "stance": "supports"}],
                "caveats": ["Observational data."]}
    calls = {"planner": 0, "narrator": 0}

    def handler(request):
        system = json.loads(request.content)["system"]
        kind = "planner" if system.startswith("You plan") else "narrator"
        calls[kind] += 1
        return httpx2.Response(200, json=message(json.dumps(PLAN if kind == "planner" else narrator)))

    prov, _ = provider_for(handler)
    inv = investigate("anything at all", cur, provider=prov)
    assert calls == {"planner": 1, "narrator": 1}
    assert inv.mode == "llm" and inv.plan.planner == "llm"
    assert inv.narrative.source == "llm" and inv.narrative.validated and inv.narrative.model == "claude-opus-5-5"


def test_end_to_end_hallucinated_narrator_reply_is_rejected(cur):
    bad = {"summary": "Conversion collapsed 42.7pp because the release broke the form.",
           "key_points": [{"text": "x", "evidence_ids": ["ev_1"], "hypothesis_id": None, "stance": "context"}],
           "caveats": []}

    def handler(request):
        system = json.loads(request.content)["system"]
        return httpx2.Response(200, json=message(json.dumps(PLAN if system.startswith("You plan") else bad)))

    prov, _ = provider_for(handler)
    inv = investigate("anything", cur, provider=prov)
    assert inv.narrative.source == "deterministic" and inv.narrative.validation_issues


def test_end_to_end_provider_outage_falls_back_cleanly(cur):
    prov, _ = provider_for(ok({"type": "error", "error": {"type": "api_error", "message": "down"}}, 500))
    inv = investigate("Why did mobile contact conversion decline in Riyadh?", cur, provider=prov)
    assert inv.mode == "deterministic" and inv.narrative.source == "deterministic"
    assert inv.hypotheses[0].category == "technical_regression"
    assert inv.narrative.fallback_reason and "LLM unavailable" in inv.narrative.fallback_reason


# --------------------------------------------------------------------------- what we actually send to the API
def _walk_schema(node, path="$"):
    """Yield (path, problem) for JSON-Schema features the structured-output API does not support."""
    if isinstance(node, dict):
        if node.get("type") == "object" and node.get("additionalProperties") is not False:
            yield path, "object without additionalProperties: false"
        if isinstance(node.get("type"), list):
            yield path, f"type array {node['type']} (use anyOf instead)"
        for banned in ("minimum", "maximum", "multipleOf", "minLength", "maxLength", "minItems", "maxItems", "$ref"):
            if banned in node:
                yield path, f"unsupported keyword {banned}"
        for k, v in node.items():
            yield from _walk_schema(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_schema(v, f"{path}[{i}]")


def test_schemas_sent_to_the_api_use_only_supported_json_schema_features(cur):
    """Documented limits: every object needs additionalProperties:false; no type arrays, numeric or string
    constraints. Captures the REAL planner and narrator schemas from the request bodies."""
    base = investigate("Why did mobile contact conversion decline in Riyadh?", cur)
    narrator = {"summary": "Fell.", "key_points": [{"text": "x", "evidence_ids": [base.evidence[0].id],
                                                      "hypothesis_id": None, "stance": "context"}], "caveats": []}
    sent: dict[str, dict] = {}

    def handler(request):
        body = json.loads(request.content)
        kind = "planner" if body["system"].startswith("You plan") else "narrator"
        sent[kind] = body
        return httpx2.Response(200, json=message(json.dumps(PLAN if kind == "planner" else narrator)))

    prov, _ = provider_for(handler)
    investigate("anything at all", cur, provider=prov)
    assert set(sent) == {"planner", "narrator"}
    for kind, body in sent.items():
        problems = list(_walk_schema(body["output_config"]["format"]["schema"]))
        assert problems == [], (kind, problems)
        assert body["max_tokens"] >= 6000            # headroom: reasoning tokens count toward max_tokens
    # The user's free-text question must not reach the narrator prompt (prompt-injection path).
    assert "anything at all" not in sent["narrator"]["messages"][0]["content"]
    assert "anything at all" in sent["planner"]["messages"][0]["content"]   # but the planner needs it
