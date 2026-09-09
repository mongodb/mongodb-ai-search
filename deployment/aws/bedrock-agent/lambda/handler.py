"""
Bedrock classic Agent action group -> MCP bridge.

Classic Bedrock Agents don't speak MCP, so this Lambda is the MCP client:
the agent calls a function, we forward it to the FastMCP server on ECS over
streamable-http, and return the result in Bedrock's action-group envelope.

ponytail: hand-rolled JSON-RPC over urllib instead of the `mcp` SDK. MCP
streamable-http is three POSTs; vendoring the SDK + httpx + anyio into the zip
buys nothing here. Swap to the SDK if we ever need sampling, resources, or
elicitation.

Dual-purpose file: `python3 handler.py --schema` prints the Bedrock action
group function schema generated from the live server's tools/list, which is
what deploy.sh uploads. Keeps schema generation and schema consumption in one
place so they cannot drift apart.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import routing

MCP_URL = os.environ.get("MCP_URL", "")
TIMEOUT = int(os.environ.get("MCP_TIMEOUT", "60"))
# Bedrock rejects an action group response over 25KB, and ~1.3KB per result
# means the server's default top_k of 20 blows through that on its own.
MAX_RESULTS = int(os.environ.get("MCP_MAX_RESULTS", "6"))
MAX_BODY = int(os.environ.get("MCP_MAX_BODY", "20000"))

# Set to a domain key to pin every search to one collection and skip routing.
# Empty (the default) means auto-route, mirroring the Amplify UI.
FORCE_DOMAIN = os.environ.get("MCP_FORCE_DOMAIN") or ""
PROTOCOL_VERSION = "2025-06-18"

# Bedrock function schemas only allow string/number/integer/boolean/array.
# MCP tools here take dict params (filters, atlas, retrieval), so those cross
# the boundary as JSON strings and get parsed back below.
_JSON_TYPES = {"string", "number", "integer", "boolean", "array"}


def endpoint() -> str:
    """The MCP URL, with the path filled in if it was configured bare.

    A host with no path 404s on every call, and the failure surfaces to the
    agent as an unhelpful "HTTP Error 404". Normalising here covers every way
    MCP_URL can be set — deploy.sh, an exported override, a console edit.
    """
    if not MCP_URL:
        raise RuntimeError("MCP_URL is not set")
    url = MCP_URL.rstrip("/")
    return url if urlsplit(url).path else url + "/mcp"


def _post(payload: dict, session_id: str | None) -> tuple[dict | None, str | None]:
    """One JSON-RPC POST. Returns (result_message, session_id_from_headers)."""
    headers = {
        "Content-Type": "application/json",
        # Streamable-http servers reply with either, so accept both.
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id

    req = urllib.request.Request(
        endpoint(), data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        sid = resp.headers.get("mcp-session-id") or session_id
        body = resp.read().decode()

    if not body.strip():          # notifications return 202 with no body
        return None, sid
    if body.lstrip().startswith("{"):
        return json.loads(body), sid
    # SSE framing: pull the payload out of the `data:` lines.
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip()), sid
    raise RuntimeError(f"unparseable MCP response: {body[:200]}")


def mcp_request(method: str, params: dict | None = None) -> dict:
    """initialize -> initialized -> <method>, retrying a lost session once.

    ponytail: the retry exists because the ECS service autoscales to 3 tasks
    with no session affinity on the Express Mode ALB. `initialize` creates the
    session on one task; a follow-up landing on another gets 404. One extra
    attempt turns a hard failure into a near-certain success at 2 tasks, and
    the real fix is stateless_http=True on the FastMCP server (server-side, so
    out of scope here).
    """
    for attempt in (1, 2):
        try:
            return _session_call(method, params)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and attempt == 1:
                continue          # session landed on another task — start over
            raise
    raise RuntimeError("unreachable")


def _session_call(method: str, params: dict | None) -> dict:
    init, sid = _post({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "bedrock-agent-bridge", "version": "1.0"},
        },
    }, None)
    if init and "error" in init:
        raise RuntimeError(f"initialize failed: {init['error']}")

    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)

    resp, _ = _post({"jsonrpc": "2.0", "id": 2, "method": method,
                     "params": params or {}}, sid)
    if resp is None:
        raise RuntimeError(f"empty response for {method}")
    if "error" in resp:
        raise RuntimeError(f"{method} failed: {resp['error']}")
    return resp.get("result", {})


# ── Lambda entrypoint ────────────────────────────────────────────────────────

def _coerce(raw: str, kind: str) -> object:
    """Bedrock hands every parameter back as a string; restore the MCP type.

    `kind` comes from the event itself — Bedrock echoes the type declared in the
    function schema — so there is nothing to keep in sync on this side.
    """
    if raw is None or raw == "":
        return None
    try:
        if kind == "integer":
            return int(float(raw))
        if kind == "number":
            return float(raw)
        if kind == "boolean":
            return str(raw).lower() in ("true", "1", "yes")
    except ValueError:
        return None                   # bad input -> omit, let MCP use its default
    # dict-valued MCP params (filters/atlas/retrieval) cross as JSON strings,
    # because Bedrock function schemas have no object type.
    if isinstance(raw, str) and raw[:1] in ("{", "["):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw


def _payload(result: dict) -> dict | str:
    """Unwrap the MCP text block; the search tools return a JSON document."""
    text = "\n".join(
        c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _search(fn: str, args: dict, domain: str, bias: str) -> dict | str:
    """One tool call against one collection's indexes."""
    scoped = dict(args)
    overrides = routing.overrides_for(domain, bias)
    # setdefault: anything the agent sent explicitly still wins.
    scoped.setdefault("atlas", overrides["atlas"])
    scoped.setdefault("retrieval", overrides["retrieval"])
    return _payload(mcp_request("tools/call", {"name": fn, "arguments": scoped}))


def _top_score(payload: dict | str) -> float:
    """qualityScore() from the TS assembler: the top result's score."""
    if not isinstance(payload, dict) or not payload.get("results"):
        return 0.0
    return payload["results"][0].get("score") or 0.0


def route_and_search(fn: str, args: dict) -> dict:
    """Classify the query, then search one collection or fan out to both."""
    decision = routing.classify(str(args.get("query", "")))
    bias = decision["bias"]

    if FORCE_DOMAIN:
        decision = {**decision, "domain": FORCE_DOMAIN, "ambiguous": False}

    if not decision["ambiguous"]:
        domain = decision["domain"]
        payload = _search(fn, args, domain, bias)
        out = payload if isinstance(payload, dict) else {"results": [], "raw": payload}
        for r in out.get("results", []):
            r["domain"] = domain
        out["routing"] = {**decision, "collections_used": [domain]}
        return out

    # Ambiguous: query both collections in parallel and merge, best domain first.
    domains = routing.ranked_domains(decision["scores"])
    with ThreadPoolExecutor(max_workers=len(domains)) as pool:
        futures = {d: pool.submit(_search, fn, args, d, bias) for d in domains}
    landed = []
    for domain, fut in futures.items():
        try:
            payload = fut.result()
        except Exception as exc:                  # one collection failing is not fatal
            print(f"[route] {domain} failed: {exc}")
            continue
        if isinstance(payload, dict):
            landed.append((domain, payload))

    scored = sorted(
        (x for x in landed if x[1].get("results")),
        key=lambda x: _top_score(x[1]),
        reverse=True,
    )
    if not scored:
        base = landed[0][1] if landed else {"results": []}
        base["results"] = []
        base["routing"] = {**decision, "collections_used": [d for d, _ in landed]}
        return base

    base = dict(scored[0][1])
    merged = []
    for domain, payload in scored:
        for r in payload.get("results", []):
            merged.append({**r, "domain": domain})
    base["results"] = merged
    base["strategy"] = " + ".join(p.get("strategy", "?") for _, p in scored)
    base["routing"] = {**decision, "collections_used": [d for d, _ in scored]}
    return base


def _fit(payload: dict) -> str:
    """Serialise the payload, trimming results until Bedrock will accept it."""
    results = payload.get("results", [])
    payload["result_count"] = len(results)
    payload["results"] = results[:MAX_RESULTS]
    body = json.dumps(payload)
    while len(body) > MAX_BODY and payload["results"]:
        payload["results"] = payload["results"][:-1]
        body = json.dumps(payload)
    return body


def lambda_handler(event, _context):
    fn = event.get("function") or event.get("apiPath", "").lstrip("/")

    args = {}
    for p in event.get("parameters", []):
        val = _coerce(p.get("value"), p.get("type", "string"))
        if val is not None:
            args[p["name"]] = val

    try:
        body = _fit(route_and_search(fn, args))
    except Exception as exc:                      # surface it to the agent, don't 500
        body = f"Failed to call MCP tool '{fn}': {exc}"

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", ""),
            "function": fn,
            "functionResponse": {"responseBody": {"TEXT": {"body": body}}},
        },
        "sessionAttributes": event.get("sessionAttributes", {}),
        "promptSessionAttributes": event.get("promptSessionAttributes", {}),
    }


# ── Deploy-time: generate the action group schema from the live server ───────

def _bedrock_type(prop: dict) -> tuple[str, bool]:
    """Map a JSON Schema property to (bedrock type, is_json_encoded)."""
    types = prop.get("type")
    if isinstance(types, list):                   # e.g. ["object","null"]
        types = next((t for t in types if t != "null"), "string")
    if types is None and ("anyOf" in prop or "$ref" in prop):
        types = next((t.get("type") for t in prop.get("anyOf", [])
                      if t.get("type") not in (None, "null")), "string")
    if types in _JSON_TYPES:
        return types, False
    return "string", True                         # object / unknown -> JSON string


def build_schema() -> list:
    """Bedrock action group function schema, generated from tools/list."""
    tools = mcp_request("tools/list").get("tools", [])
    functions = []

    for tool in tools:
        schema = tool.get("inputSchema", {}) or {}
        props = schema.get("properties", {}) or {}
        required = set(schema.get("required", []))
        params = {}

        for pname, prop in props.items():
            btype, is_json = _bedrock_type(prop)
            desc = prop.get("description") or f"{pname} parameter"
            if is_json:
                desc = f"{desc}. Pass as a JSON object encoded in a string."
            params[pname] = {
                "type": btype,
                "description": desc[:500],
                "required": pname in required,
            }

        functions.append({
            "name": tool["name"],
            "description": (tool.get("description") or tool["name"])[:1200],
            "parameters": params,
            "requireConfirmation": "DISABLED",
        })

    return functions


if __name__ == "__main__":
    if "--schema" not in sys.argv:
        print(__doc__)
        sys.exit(1)
    print(json.dumps({"functions": build_schema()}, indent=2))
