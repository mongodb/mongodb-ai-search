# Deploy FastMCP on Amazon Bedrock AgentCore Runtime (opt-in)

> **This is NOT the default deployment.** The default backend target is
> **ECS Express Mode** (`deployment/aws/ecs/`). Deploy to AgentCore only when
> you explicitly want to — the script refuses to run without confirmation.

Amazon Bedrock AgentCore Runtime can host MCP servers directly. This packages
the existing AiSearch FastMCP surface (`AiSearch/mcp_server/server.py`) as an
ARM64 container and registers it as an AgentCore runtime with the **MCP**
protocol.

Docs: <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html>

## AgentCore contract this deployment satisfies

| Requirement                     | How it's met                                                        |
| ------------------------------- | ------------------------------------------------------------------- |
| Platform must be `linux/arm64`  | `Dockerfile` uses `--platform=linux/arm64`; build uses `buildx`     |
| MCP served at `0.0.0.0:8000/mcp`| `MCP_HOST=0.0.0.0`, `MCP_PORT=8000`, transport `streamable-http`    |
| Image in ECR                    | Script creates `AiSearch-agentcore` repo and pushes the image      |
| Execution role                  | Script creates `AiSearch-agentcore-runtime-role` (ECR + logs + Bedrock) |
| Protocol = MCP                  | `create-agent-runtime --protocol-configuration '{"serverProtocol":"MCP"}'` |

## Files

| File          | Purpose                                                               |
| ------------- | --------------------------------------------------------------------- |
| `Dockerfile`  | ARM64 image, FastMCP on `:8000/mcp` (build context = repo root)       |
| `deploy.sh`   | Opt-in end-to-end: confirm → IAM role → ECR push → create runtime (create-only) |

## Run it

```bash
export AWS_REGION=us-east-1
export ATLAS_URI='mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true'
export ATLAS_DB='your_database_name'

# Interactive confirmation (type 'deploy-agentcore' when prompted):
./deployment/aws/agentcore/deploy.sh

# Or skip the prompt in CI:
YES=yes ./deployment/aws/agentcore/deploy.sh --yes
```

The script prints the **agent runtime ARN** and the **invocation URL**:

```
https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<ENCODED_ARN>/invocations?qualifier=DEFAULT
```

> **Create-only.** `deploy.sh` calls `create-agent-runtime`. If a runtime with
> the same name (`AGENTCORE_RUNTIME_NAME`, default `AiSearch_fastmcp`) already
> exists, AWS rejects the create. Delete the old runtime first (see
> [Manage / tear down](#manage--tear-down)) or deploy under a new name.

## Configuration options

Everything in `AiSearch/config/AiSearch.yaml` uses `${VAR:-default}` syntax, so
you configure the runtime purely with **environment variables** — no image
rebuild. `deploy.sh` forwards any of the variables below that you export into the
runtime's `environmentVariables`; anything you leave unset falls back to the YAML
default. Just export the vars and run the script:

```bash
export EMBEDDINGS_PROVIDER=auto
export ATLAS_VECTOR_INDEX=autoembed_index
export ATLAS_DIMENSIONS=-1
./deployment/aws/agentcore/deploy.sh --yes
```

The script prints the exact set of vars it forwarded (`==> Forwarding runtime env vars: ...`).

> **Fixed by the AgentCore contract (not overridable here):** `MCP_HOST=0.0.0.0`,
> `MCP_PORT=8000`, `MCP_TRANSPORT=streamable-http`. AgentCore requires MCP on
> `0.0.0.0:8000`, so the script pins these.

### Atlas connection & index

| Env var | Default | Purpose |
| --- | --- | --- |
| `ATLAS_URI` | *(required, secret)* | `mongodb+srv://...` connection string |
| `ATLAS_DB` | `amazon` | Database name |
| `ATLAS_COLLECTION` | `pdf_multimodal_chunks` | Collection to search |
| `ATLAS_VECTOR_INDEX` | `aisearch_vector_index` | Atlas Vector Search index name |
| `ATLAS_SEARCH_INDEX` | `default` | Atlas Search (Lucene) index for fulltext/hybrid |
| `ATLAS_TEXT_KEY` | `raw_text` | Field holding chunk text |
| `ATLAS_EMBEDDING_KEY` | `embedding` | Field holding the vector (empty ⇒ null, for auto mode) |
| `ATLAS_RELEVANCE_FN` | `cosine` | Similarity function (empty ⇒ null, for auto mode) |
| `ATLAS_DIMENSIONS` | `1024` | Vector dimensions — must match the index and the embedder (`-1` for auto mode) |

### Embedding provider (query embeddings)

Set `EMBEDDINGS_PROVIDER`. This deployment documents the AWS-native and
MongoDB/Voyage options; other providers exist in code but are omitted here.

| Provider | Required / key env vars |
| --- | --- |
| `voyageai` *(default)* | `VOYAGE_API_KEY`, `EMBEDDINGS_MODEL` (`voyage-4`), `EMBEDDINGS_OUTPUT_DIMENSION` (`1024`) |
| `auto` (server-side) | `EMBEDDINGS_MODEL` only — Atlas embeds internally; no API key |
| `bedrock_titan` | `EMBEDDINGS_MODEL`/model id (e.g. `amazon.titan-embed-text-v2:0`), `BEDROCK_REGION_NAME` — uses the runtime role's Bedrock access |

`EMBEDDINGS_OUTPUT_DIMENSION` **must equal** `ATLAS_DIMENSIONS`.

The `deploy.sh` guard only requires `VOYAGE_API_KEY` when
`EMBEDDINGS_PROVIDER` is `voyageai` or `voyage_multimodal`; switch the provider
and the key is no longer needed.

#### Two embedding modes (must match your Atlas index type)

The container **hard-fails at startup** if the provider doesn't match the live
index type (`ProviderIndexMismatch`). Pick one:

- **Mode A — client-side embeddings** (default): the app embeds queries
  (Voyage / Bedrock Titan). Requires a `vector`-type index. Keep
  `ATLAS_EMBEDDING_KEY`, a real `ATLAS_DIMENSIONS`, and `ATLAS_RELEVANCE_FN` set.
- **Mode B — server-side AutoEmbeddings**: Atlas embeds using the model declared
  in an `autoEmbed`-type index. Set `EMBEDDINGS_PROVIDER=auto`,
  `ATLAS_EMBEDDING_KEY=` (empty), `ATLAS_DIMENSIONS=-1`, `ATLAS_RELEVANCE_FN=`
  (empty), and point `ATLAS_VECTOR_INDEX` at the autoEmbed index whose declared
  `model` equals `EMBEDDINGS_MODEL`.

Dev-only escape hatch: `AISEARCH_SKIP_PROVIDER_INDEX_CHECK=1` bypasses this
validation. Do not use in production.

### Planner LLM (query understanding + retrieval planning)

Set `PLANNER_LLM_PROVIDER`. This deployment uses AWS Bedrock; other providers
exist in code but are omitted here.

| Provider | Key env vars |
| --- | --- |
| `bedrock` *(default)* | `BEDROCK_MODEL` (default Claude Haiku), `BEDROCK_REGION_NAME` (`us-east-1`), `BEDROCK_TEMPERATURE` (`0.1`) — no key needed; the runtime role grants `bedrock:InvokeModel` |

`PLANNER_DEFAULT_TOP_K` (default `20`) controls default candidate count.

### Retrieval strategy

| Env var | Default | Options / notes |
| --- | --- | --- |
| `RETRIEVAL_DEFAULT_STRATEGY` | `hybrid` | `vector`, `fulltext`, `hybrid`, `graph`, `parent_doc` |
| `RETRIEVAL_HYBRID_VECTOR_WEIGHT` | `0.6` | hybrid vector weight |
| `RETRIEVAL_HYBRID_FULLTEXT_WEIGHT` | `0.4` | hybrid fulltext weight |
| `RETRIEVAL_VECTOR_NUM_CANDIDATES` | `200` | vector oversampling |

> `hybrid` uses native `$rankFusion`, which requires **MongoDB 8.0+**.

### Logging

| Env var | Default | Purpose |
| --- | --- | --- |
| `LOG_LEVEL` | `info` | Application log level |

### Example: switch to server-side AutoEmbeddings

```bash
export ATLAS_URI='mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true'
export ATLAS_DB='your_database_name'
export EMBEDDINGS_PROVIDER=auto
export EMBEDDINGS_MODEL=voyage-4          # must equal the index's autoEmbed model
export ATLAS_VECTOR_INDEX=autoembed_index
export ATLAS_EMBEDDING_KEY=               # empty ⇒ null
export ATLAS_RELEVANCE_FN=                # empty ⇒ null
export ATLAS_DIMENSIONS=-1
./deployment/aws/agentcore/deploy.sh --yes   # no VOYAGE_API_KEY needed
```

## Invoke the deployed MCP server

AgentCore fronts the runtime with OAuth-style bearer auth. A minimal Python
client using the MCP SDK:

```python
import asyncio, os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    arn = os.environ["AGENT_ARN"]
    token = os.environ["BEARER_TOKEN"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    encoded = arn.replace(":", "%3A").replace("/", "%2F")
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded}/invocations?qualifier=DEFAULT"
    headers = {"authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with streamablehttp_client(url, headers, timeout=120, terminate_on_close=False) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print(await s.list_tools())          # vector_search, hybrid_search, ...
            print(await s.call_tool("hybrid_search", {"query": "best rated hotels", "top_k": 5}))

asyncio.run(main())
```

Set up a Cognito user pool (or another OAuth IdP) to mint `BEARER_TOKEN`; see
the "Set up Cognito user pool" appendix in the AWS AgentCore MCP docs.

## Tools exposed

Same tools as the ECS FastMCP service (they share `AiSearch/mcp_server/server.py`):
`vector_search`, `fulltext_search`, `hybrid_search`, `graph_search`,
`parent_doc_search`, `metadata_search`, `auto_search`.

## Manage / tear down

```bash
RUNTIME_ID=<from the ARN, the part after runtime/>

# Status
aws bedrock-agentcore-control get-agent-runtime \
  --region $AWS_REGION --agent-runtime-id "$RUNTIME_ID"

# Delete
aws bedrock-agentcore-control delete-agent-runtime \
  --region $AWS_REGION --agent-runtime-id "$RUNTIME_ID"
```

## Notes / caveats

- **CLI version.** `bedrock-agentcore-control` is a recent service. If you get
  `Invalid choice: 'bedrock-agentcore-control'`, update AWS CLI v2.
- **ARM64 is mandatory.** A native `docker build` on an x86 host produces an
  amd64 image that AgentCore rejects. The script always uses
  `buildx --platform linux/arm64 --push`.
- **Secrets.** `ATLAS_URI` and any provider keys you forward (`VOYAGE_API_KEY`,
  `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `COHERE_API_KEY`,
  `ANTHROPIC_API_KEY`) are passed as plaintext environment variables to the
  runtime. For production, prefer wiring secrets through AgentCore's supported
  secret mechanisms (or AWS Secrets Manager) rather than plaintext env vars.
- **Alternative: `agentcore` CLI.** AWS also ships an `agentcore` toolkit
  (`npm install -g @aws/agentcore`; `agentcore create --protocol MCP` →
  `agentcore deploy`). This script uses the raw control-plane API instead so the
  whole flow is scriptable and idempotent with no extra global tooling.
