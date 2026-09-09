# Classic Bedrock Agent → MCP on ECS

A **classic Bedrock Agent** (the `bedrock-agent` API — *not* AgentCore) that
queries the FastMCP server already running on ECS.

```
Bedrock Agent ──action group──▶ Lambda ──▶ classify query ──▶ pick collection(s)
                                  │                                │
                                  └── streamable-http ─────────────┴──▶ ECS FastMCP ──▶ Atlas
```

Classic agents don't speak MCP, so one Lambda bridges the two. It does two jobs:

1. **MCP client** — `initialize` → `notifications/initialized` → `tools/call`
   over streamable-http, hand-rolled on `urllib` so the zip has no dependencies.
2. **Collection router** — classifies the query and injects the right `atlas` /
   `retrieval` overrides, mirroring what the Amplify UI's BFF does.

The action group's function schema is **generated at deploy time from the live
server's `tools/list`**, so it can't drift from the tools the server exposes.

> Deploying FastMCP on **AgentCore Runtime** is a different, opt-in path — see
> [`../agentcore/`](../agentcore/). This directory does not touch it.

## Files

| File                  | Purpose |
| --------------------- | ------- |
| `deploy.sh`           | End-to-end: resolve MCP URL → generate schema → IAM → Lambda → agent → action group → prepare → alias |
| `teardown.sh`         | Deletes the agent + Lambda; `--purge` also drops the IAM roles and log group |
| `lambda/handler.py`   | The bridge: MCP client, routing, response trimming. Also generates the action group schema (`--schema`) |
| `lambda/routing.py`   | Collection registry + query classifier, ported from the copilot BFF |

## Prerequisites

- AWS CLI v2, logged in: `aws sso login --profile anuj-ps`
- `python3` (stdlib only — the Lambda has no third-party dependencies)
- The FastMCP ECS service running (`deployment/aws/ecs/deploy.sh`)
- Bedrock model access enabled for the agent's foundation model

## Run it

```bash
./deployment/aws/bedrock-agent/deploy.sh
```

Idempotent — re-running updates the agent, Lambda, action group and alias in
place rather than creating duplicates.

### Configuration

Everything is an environment variable with a working default; nothing
account-specific is committed.

| Variable            | Default | Notes |
| ------------------- | ------- | ----- |
| `AWS_PROFILE`       | `anuj-ps` | |
| `AWS_REGION`        | `us-east-1` | |
| `MCP_SERVICE_NAME`  | `searchaas-fastmcp` | ECS service to resolve the endpoint from |
| `MCP_URL`           | *(resolved from the ECS service)* | Set it to bypass the ECS lookup. A bare host is fine — the bridge appends `/mcp` |
| `AGENT_NAME`        | `searchaas-mcp-agent` | |
| `LAMBDA_NAME`       | `searchaas-mcp-bridge` | |
| `ALIAS_NAME`        | `prod` | |
| `AGENT_MODEL`       | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Any model/inference profile enabled in the account |
| `MCP_FORCE_DOMAIN`  | *(empty — auto-route)* | `IT_helpdesk` or `employee_support` to pin every search to one collection |
| `MCP_MAX_RESULTS`   | `6` | Results handed to the agent per call |
| `MCP_MAX_BODY`      | `20000` | Hard byte ceiling on the action group response |

## Collection routing

The MCP server's built-in default collection is **not** the one holding the
data. Callers are expected to send `atlas` overrides on every request — the
React UI and the copilot BFF both do, and so does this bridge.

`lambda/routing.py` is a port of the copilot's
`src/lib/{collections,classifier}.ts` plus the `buildPayload` half of
`searchaas-client.ts`. Same rules, same numbers:

- each domain scored against its signal lists — **3 points** per regex pattern,
  **1 point** per whole-word keyword
- `confidence = topScore / totalScore`; no signal at all → `employee_support` at
  `0.5`
- **`confidence < 0.55` → ambiguous** → both collections queried in parallel and
  the results merged, best domain first (ranked by top-result score)
- retrieval bias from the query text — `vector-heavy` `0.75/0.25`,
  `fulltext-heavy` `0.3/0.7`, otherwise the collection's own hybrid weights.
  The vector list is tested first, so it wins when both would match.

| Query | Domain | Confidence | Route |
| ----- | ------ | ---------- | ----- |
| "My VPN is not connecting on my Mac" | `IT_helpdesk` | 1.0 | single |
| "What is the reimbursement cap for travel?" | `employee_support` | 1.0 | single |
| "What is the access policy?" | tie, 4–4 | 0.5 | **fan-out** |
| "I need help today" | no signal | 0.5 | **fan-out** |

Overrides are injected **in the Lambda, not in the agent's prompt**, on purpose:
making an LLM reproduce exact index names inside a JSON-encoded string is a
needless failure mode, and getting it wrong silently searches the wrong
collection and returns nothing. The agent is instructed to always omit `atlas`,
`filters` and `retrieval`; anything it does send still wins, since the bridge
merges with `setdefault`.

Each result carries a `domain` field, and the response includes a `routing`
block (domain, confidence, bias, `collections_used`) plus `result_count` — the
pre-trim total.

## Invoke the agent

The AWS CLI does **not** expose `InvokeAgent` (it's an event-stream operation),
so use boto3:

```python
import boto3

c = boto3.Session(profile_name="anuj-ps", region_name="us-east-1") \
         .client("bedrock-agent-runtime")
r = c.invoke_agent(agentId="<AGENT_ID>", agentAliasId="<ALIAS_ID>",
                   sessionId="demo-1", inputText="What is the VPN setup on Mac?")
print("".join(e["chunk"]["bytes"].decode() for e in r["completion"] if "chunk" in e))
```

Pass `enableTrace=True` and read `orchestrationTrace` to see which MCP tool the
agent chose, which collections were searched, and what the server returned.

## Teardown

```bash
./deployment/aws/bedrock-agent/teardown.sh           # agent + Lambda
./deployment/aws/bedrock-agent/teardown.sh --purge   # also IAM roles + log group
```

The ECS FastMCP service is never touched.

## Limits worth knowing

**The router is a copy, not a shared library.** `routing.py` duplicates the
signal lists from `collections.ts` in another language. Edit the TypeScript and
this silently diverges. Parity was verified against the live BFF on 10 queries
(domain, confidence and bias all matching); re-check it after any change to the
TS. Generating `routing.py` from the TS at build time is the fix if this drifts
in practice.

**The agent decides *whether* to search; the UI always searches.** Given a vague
prompt ("I need help today") the Amplify UI fans out to both collections, while
the agent may ask a clarifying question and call no tool at all. Routing parity
does not give behavioural parity — an LLM sits in front of it.

**Bedrock caps an action group response at 25KB.** At roughly 1.3KB per result,
the server's default `top_k` of 20 produces ~27KB on a single collection and
~36KB on a fan-out — over the limit. The bridge returns `MCP_MAX_RESULTS` (6)
and trims further if the serialised body still exceeds `MCP_MAX_BODY`. Raise
those only with the 25KB ceiling in mind.

**The action group schema is a snapshot; MCP tools are live.** `deploy.sh`
reads `tools/list` and freezes it into the action group. Add, rename, or
re-signature a tool on the MCP server and the agent won't see the change until
you **re-run `deploy.sh`**. There is no runtime discovery — classic action
groups require a static schema.

**Bedrock function schemas have no object type.** They allow only
string/number/integer/boolean/array, but `filters`, `atlas` and `retrieval` are
MCP dict parameters. They cross the boundary as JSON strings and the Lambda
parses them back (any value starting with `{` or `[`). A plain string argument
that legitimately begins with `{` would be misread — no such parameter exists
today, but it's the sharp edge if the tool signatures change.

**The bridge is stateless, and the MCP session is per-invocation.** Each call
opens a fresh session and drops it. The ECS service autoscales to 3 tasks with
no session affinity on the Express Mode ALB, so a `tools/call` can land on a
different task than its `initialize` and get a **404**; the bridge retries the
whole session once. The real fix is `stateless_http=True` on the FastMCP server,
which is server-side and out of scope here.

**A misconfigured endpoint looks like an empty corpus.** Querying the wrong
collection returns `{"results": []}`, not an error — indistinguishable from a
genuine miss. If the agent finds nothing for everything, check `MCP_FORCE_DOMAIN`
and the `routing` block in the tool response before suspecting the data.

**Cold-start latency stacks.** Every tool call pays Lambda cold start plus three
round trips to ECS on top of the search itself, and a fan-out runs two searches
(in parallel, so ~one search of wall time). The Lambda timeout is 120s.

**The MCP endpoint is public.** ECS Express Mode always provisions an
internet-facing ALB, so the bridge reaches the server over the public internet
with no authentication. If that matters, move FastMCP to plain Fargate behind
an internal ALB and put the Lambda in the same VPC.
