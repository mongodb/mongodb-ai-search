#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Deploy a CLASSIC Bedrock Agent (bedrock-agent API, NOT AgentCore) that reaches
# the FastMCP server already running on ECS.
#
#   Bedrock Agent --action group--> Lambda (MCP client) --http--> ECS FastMCP
#
# Classic agents don't speak MCP, so one Lambda bridges the two. The action
# group's function schema is GENERATED at deploy time from the live server's
# tools/list, so it can never drift from the tools the server actually exposes.
#
# Idempotent: re-running updates in place. Safe to run repeatedly.
#
# Prereqs:
#   - AWS CLI v2, logged in:  aws sso login --profile anuj-ps
#   - python3 (schema generation; stdlib only)
#   - The FastMCP ECS service must be running.
# -----------------------------------------------------------------------------
set -euo pipefail

# ── Inputs ────────────────────────────────────────────────────────────────────
: "${AWS_PROFILE:=anuj-ps}"
: "${AWS_REGION:=us-east-1}"
export AWS_PROFILE AWS_REGION

# The already-deployed FastMCP ECS service (see ../ecs/). Resolved by name so no
# account-specific ARN is committed. Set MCP_URL to skip the lookup entirely.
: "${MCP_SERVICE_NAME:=searchaas-fastmcp}"
: "${MCP_URL:=}"

# Collection routing lives in lambda/routing.py (a port of the copilot BFF's
# classifier), so the agent auto-routes across collections the same way the
# Amplify UI does. Set MCP_FORCE_DOMAIN to a domain key — IT_helpdesk or
# employee_support — to pin every search to one collection instead.
: "${MCP_FORCE_DOMAIN:=}"
# Bedrock caps an action group response at 25KB; these keep the bridge under it.
: "${MCP_MAX_RESULTS:=6}"
: "${MCP_MAX_BODY:=20000}"

: "${AGENT_NAME:=searchaas-mcp-agent}"
: "${LAMBDA_NAME:=searchaas-mcp-bridge}"
: "${ALIAS_NAME:=prod}"
: "${AGENT_MODEL:=us.anthropic.claude-sonnet-4-5-20250929-v1:0}"

AGENT_ROLE=AmazonBedrockExecutionRoleForAgents_searchaas
LAMBDA_ROLE=searchaas-mcp-bridge-role
ACTION_GROUP=searchaas-mcp-tools

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/.build"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "==> Account: $ACCOUNT_ID   Region: $AWS_REGION   Profile: $AWS_PROFILE"

# ── 1. Resolve the live MCP endpoint from the ECS service ────────────────────
if [[ -z "$MCP_URL" ]]; then
  echo "==> Resolving MCP endpoint from ECS service '$MCP_SERVICE_NAME' ..."
  svc_arn=$(aws ecs list-services --cluster default \
              --query "serviceArns[?contains(@, ':service/default/${MCP_SERVICE_NAME}')]|[0]" \
              --output text 2>/dev/null || echo "None")
  [[ "$svc_arn" == "None" || -z "$svc_arn" ]] && {
    echo "ERROR: ECS service '$MCP_SERVICE_NAME' not found. Run deployment/aws/ecs/deploy.sh first," >&2
    echo "       or export MCP_URL to point at an MCP server directly." >&2
    exit 1
  }
  host=$(aws ecs describe-express-gateway-service \
           --service-arn "$svc_arn" \
           --query 'service.activeConfigurations[0].ingressPaths[0].endpoint' \
           --output text 2>/dev/null || echo "None")
  [[ "$host" == "None" || -z "$host" ]] && {
    echo "ERROR: service $svc_arn has no endpoint yet — is it still provisioning?" >&2
    exit 1
  }
  MCP_URL="https://${host}/mcp"
fi
echo "    MCP_URL = $MCP_URL"

# ── 2. Generate the action group schema from the LIVE server ─────────────────
# Single source of truth: whatever tools/list returns is what the agent gets.
echo "==> Generating action group schema from tools/list ..."
mkdir -p "$BUILD_DIR"
MCP_URL="$MCP_URL" python3 "$SCRIPT_DIR/lambda/handler.py" --schema \
  > "$BUILD_DIR/function-schema.json"
python3 -c "
import json,sys
fns=json.load(open(sys.argv[1]))['functions']
print('    %d tools: %s' % (len(fns), ', '.join(f['name'] for f in fns)))
" "$BUILD_DIR/function-schema.json"

# ── 3. IAM roles (idempotent) ────────────────────────────────────────────────
echo "==> Ensuring IAM roles exist..."
create_role_if_missing() {
  local name=$1 trust=$2
  if aws iam get-role --role-name "$name" >/dev/null 2>&1; then
    echo "    role $name already exists"
  else
    aws iam create-role --role-name "$name" --assume-role-policy-document "$trust" >/dev/null
    echo "    created role $name"
  fi
}

create_role_if_missing "$LAMBDA_ROLE" \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name "$LAMBDA_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null || true

# Bedrock only assumes an agent role whose trust is scoped to this account.
create_role_if_missing "$AGENT_ROLE" "$(cat <<JSON
{"Version":"2012-10-17","Statement":[{
  "Effect":"Allow",
  "Principal":{"Service":"bedrock.amazonaws.com"},
  "Action":"sts:AssumeRole",
  "Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT_ID}"}}
}]}
JSON
)"
aws iam put-role-policy --role-name "$AGENT_ROLE" --policy-name InvokeModel --policy-document "$(cat <<JSON
{"Version":"2012-10-17","Statement":[{
  "Effect":"Allow",
  "Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
  "Resource":[
    "arn:aws:bedrock:*::foundation-model/*",
    "arn:aws:bedrock:*:${ACCOUNT_ID}:inference-profile/*"
  ]}]}
JSON
)" >/dev/null
echo "    attached InvokeModel policy to $AGENT_ROLE"

LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${LAMBDA_ROLE}"
AGENT_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${AGENT_ROLE}"

# ── 4. Package + deploy the bridge Lambda ────────────────────────────────────
# Stdlib only -> a plain zip, no layers, no container build.
echo "==> Packaging Lambda..."
rm -f "$BUILD_DIR/function.zip"
(cd "$SCRIPT_DIR/lambda" && zip -q "$BUILD_DIR/function.zip" handler.py routing.py)

# The Lambda needs no tool metadata: Bedrock echoes each parameter's declared
# type in the event, so the bridge stays correct when the schema changes.
#
# Written as a JSON file, NOT --environment shorthand: MCP_DEFAULTS is a JSON
# blob, and the shorthand parser splits values on commas and braces.
python3 - "$BUILD_DIR/env.json" <<PYENV
import json, sys
json.dump({"Variables": {
    "MCP_URL": "${MCP_URL}",
    "MCP_TIMEOUT": "60",
    "MCP_FORCE_DOMAIN": "${MCP_FORCE_DOMAIN}",
    "MCP_MAX_RESULTS": "${MCP_MAX_RESULTS}",
    "MCP_MAX_BODY": "${MCP_MAX_BODY}",
}}, open(sys.argv[1], "w"))
PYENV
LAMBDA_ENV="file://${BUILD_DIR}/env.json"
if [[ -n "$MCP_FORCE_DOMAIN" ]]; then
  echo "    pinned to collection '${MCP_FORCE_DOMAIN}'"
else
  echo "    auto-routing across: $(python3 -c "
import sys; sys.path.insert(0,'$SCRIPT_DIR/lambda'); import routing
print(', '.join(routing.ALL_DOMAINS))")"
fi

if aws lambda get-function --function-name "$LAMBDA_NAME" >/dev/null 2>&1; then
  echo "==> Updating Lambda $LAMBDA_NAME ..."
  aws lambda update-function-code --function-name "$LAMBDA_NAME" \
    --zip-file "fileb://$BUILD_DIR/function.zip" >/dev/null
  aws lambda wait function-updated --function-name "$LAMBDA_NAME"
  aws lambda update-function-configuration --function-name "$LAMBDA_NAME" \
    --environment "$LAMBDA_ENV" --timeout 120 --memory-size 512 >/dev/null
  aws lambda wait function-updated --function-name "$LAMBDA_NAME"
else
  echo "==> Creating Lambda $LAMBDA_NAME ..."
  # IAM role creation is eventually consistent; Lambda rejects it until it lands.
  for i in $(seq 1 10); do
    if aws lambda create-function --function-name "$LAMBDA_NAME" \
        --runtime python3.12 --handler handler.lambda_handler \
        --role "$LAMBDA_ROLE_ARN" --zip-file "fileb://$BUILD_DIR/function.zip" \
        --environment "$LAMBDA_ENV" --timeout 120 --memory-size 512 \
        >/dev/null 2>"$BUILD_DIR/lambda-err.txt"; then
      break
    fi
    # Only role propagation is worth retrying; anything else fails loudly.
    if ! grep -qi "cannot be assumed\|InvalidParameterValue" "$BUILD_DIR/lambda-err.txt"; then
      cat "$BUILD_DIR/lambda-err.txt" >&2; exit 1
    fi
    echo "    waiting for IAM role to propagate ($i/10)..."
    sleep 6
  done
  aws lambda wait function-active --function-name "$LAMBDA_NAME"
fi
LAMBDA_ARN=$(aws lambda get-function --function-name "$LAMBDA_NAME" \
               --query 'Configuration.FunctionArn' --output text)
echo "    $LAMBDA_ARN"

# ── 5. Create / update the classic agent ─────────────────────────────────────
INSTRUCTION=$(cat <<'TXT'
You are a retrieval assistant for a MongoDB Atlas-backed search service. Answer
questions strictly from the search tools available to you; never answer from
your own knowledge.

Pick the tool that fits the request:
- auto_search: default. Use it when unsure which strategy fits.
- vector_search: semantic or conceptual similarity.
- fulltext_search: exact keywords, names, codes, or literal phrases.
- hybrid_search: a mix of meaning and specific keywords.
- graph_search: questions about how entities relate to one another.
- parent_doc_search: when the user needs full surrounding context, not snippets.
- metadata_search: filtering or browsing by attributes rather than by text.

Pass the user's information need as `query`. Only set `top_k` when the user asks
for a specific number of results. ALWAYS omit the `filters`, `atlas` and
`retrieval` parameters — the backend fills them in, and supplying them yourself
will search the wrong data.

Tools return JSON with a `results` array. Summarise the results for the user and
cite the concrete fields you used.

If `results` is empty, reply only that the search returned nothing for that
query, and suggest rephrasing. Do NOT fall back to general knowledge, do NOT
offer generic troubleshooting advice, and do NOT suggest contacting a vendor or
support team. An empty result means you have no information to give.
TXT
)

AGENT_ID=$(aws bedrock-agent list-agents \
             --query "agentSummaries[?agentName=='${AGENT_NAME}'].agentId | [0]" \
             --output text 2>/dev/null || echo "None")

if [[ "$AGENT_ID" == "None" || -z "$AGENT_ID" ]]; then
  echo "==> Creating agent $AGENT_NAME ..."
  for i in $(seq 1 10); do
    AGENT_ID=$(aws bedrock-agent create-agent \
      --agent-name "$AGENT_NAME" \
      --agent-resource-role-arn "$AGENT_ROLE_ARN" \
      --foundation-model "$AGENT_MODEL" \
      --instruction "$INSTRUCTION" \
      --idle-session-ttl-in-seconds 600 \
      --description "Classic Bedrock Agent bridged to the FastMCP server on ECS." \
      --query 'agent.agentId' --output text 2>/dev/null) && [[ -n "$AGENT_ID" ]] && break
    echo "    waiting for agent role to propagate ($i/10)..."
    sleep 6
  done
  [[ -z "$AGENT_ID" || "$AGENT_ID" == "None" ]] && { echo "ERROR: create-agent failed" >&2; exit 1; }
else
  echo "==> Updating existing agent $AGENT_NAME ($AGENT_ID) ..."
  aws bedrock-agent update-agent \
    --agent-id "$AGENT_ID" \
    --agent-name "$AGENT_NAME" \
    --agent-resource-role-arn "$AGENT_ROLE_ARN" \
    --foundation-model "$AGENT_MODEL" \
    --instruction "$INSTRUCTION" \
    --idle-session-ttl-in-seconds 600 \
    --description "Classic Bedrock Agent bridged to the FastMCP server on ECS." >/dev/null
fi

# create/update-agent leave the agent in CREATING/UPDATING briefly.
wait_for_agent() {
  for _ in $(seq 1 30); do
    s=$(aws bedrock-agent get-agent --agent-id "$AGENT_ID" --query 'agent.agentStatus' --output text)
    [[ "$s" == "CREATING" || "$s" == "UPDATING" || "$s" == "PREPARING" ]] || { echo "$s"; return; }
    sleep 4
  done
  echo "TIMEOUT"
}
echo "    agent status: $(wait_for_agent)"

# ── 6. Let Bedrock invoke the bridge Lambda ──────────────────────────────────
AGENT_ARN="arn:aws:bedrock:${AWS_REGION}:${ACCOUNT_ID}:agent/${AGENT_ID}"
aws lambda remove-permission --function-name "$LAMBDA_NAME" \
  --statement-id bedrock-agent-invoke >/dev/null 2>&1 || true
aws lambda add-permission --function-name "$LAMBDA_NAME" \
  --statement-id bedrock-agent-invoke \
  --action lambda:InvokeFunction \
  --principal bedrock.amazonaws.com \
  --source-arn "$AGENT_ARN" >/dev/null
echo "    Bedrock may invoke $LAMBDA_NAME"

# ── 7. The single action group, schema generated in step 2 ───────────────────
AG_ID=$(aws bedrock-agent list-agent-action-groups \
          --agent-id "$AGENT_ID" --agent-version DRAFT \
          --query "actionGroupSummaries[?actionGroupName=='${ACTION_GROUP}'].actionGroupId | [0]" \
          --output text 2>/dev/null || echo "None")

if [[ "$AG_ID" == "None" || -z "$AG_ID" ]]; then
  echo "==> Creating action group $ACTION_GROUP ..."
  AG_ID=$(aws bedrock-agent create-agent-action-group \
    --agent-id "$AGENT_ID" --agent-version DRAFT \
    --action-group-name "$ACTION_GROUP" \
    --action-group-executor "lambda=${LAMBDA_ARN}" \
    --function-schema "file://${BUILD_DIR}/function-schema.json" \
    --action-group-state ENABLED \
    --description "MCP tools proxied from the FastMCP server on ECS." \
    --query 'agentActionGroup.actionGroupId' --output text)
else
  echo "==> Updating action group $ACTION_GROUP ($AG_ID) ..."
  aws bedrock-agent update-agent-action-group \
    --agent-id "$AGENT_ID" --agent-version DRAFT \
    --action-group-id "$AG_ID" \
    --action-group-name "$ACTION_GROUP" \
    --action-group-executor "lambda=${LAMBDA_ARN}" \
    --function-schema "file://${BUILD_DIR}/function-schema.json" \
    --action-group-state ENABLED \
    --description "MCP tools proxied from the FastMCP server on ECS." >/dev/null
fi

# ── 8. Prepare (compiles DRAFT) + alias ──────────────────────────────────────
echo "==> Preparing agent..."
aws bedrock-agent prepare-agent --agent-id "$AGENT_ID" >/dev/null
for _ in $(seq 1 40); do
  st=$(aws bedrock-agent get-agent --agent-id "$AGENT_ID" --query 'agent.agentStatus' --output text)
  [[ "$st" == "PREPARING" ]] || break
  sleep 4
done
echo "    agent status: $st"
[[ "$st" == "PREPARED" ]] || { echo "ERROR: agent did not reach PREPARED (got $st)" >&2; exit 1; }

ALIAS_ID=$(aws bedrock-agent list-agent-aliases --agent-id "$AGENT_ID" \
             --query "agentAliasSummaries[?agentAliasName=='${ALIAS_NAME}'].agentAliasId | [0]" \
             --output text 2>/dev/null || echo "None")
if [[ "$ALIAS_ID" == "None" || -z "$ALIAS_ID" ]]; then
  echo "==> Creating alias $ALIAS_NAME ..."
  ALIAS_ID=$(aws bedrock-agent create-agent-alias --agent-id "$AGENT_ID" \
               --agent-alias-name "$ALIAS_NAME" \
               --query 'agentAlias.agentAliasId' --output text)
else
  # No routingConfiguration => Bedrock snapshots a new version and repoints.
  echo "==> Updating alias $ALIAS_NAME ($ALIAS_ID) to the current version ..."
  aws bedrock-agent update-agent-alias --agent-id "$AGENT_ID" \
    --agent-alias-id "$ALIAS_ID" --agent-alias-name "$ALIAS_NAME" >/dev/null
fi
for _ in $(seq 1 40); do
  ast=$(aws bedrock-agent get-agent-alias --agent-id "$AGENT_ID" \
          --agent-alias-id "$ALIAS_ID" --query 'agentAlias.agentAliasStatus' --output text)
  [[ "$ast" == "CREATING" || "$ast" == "UPDATING" ]] || break
  sleep 4
done

rm -rf "$BUILD_DIR"

cat <<SUMMARY

==> Done.
    Agent name     : $AGENT_NAME
    Agent ID       : $AGENT_ID
    Alias          : $ALIAS_NAME ($ALIAS_ID, status $ast)
    Action group   : $ACTION_GROUP ($AG_ID)
    Bridge Lambda  : $LAMBDA_ARN
    MCP endpoint   : $MCP_URL

    Try it (the AWS CLI does not expose InvokeAgent — it is an event stream,
    so use boto3):
      python3 -c "
      import boto3
      c = boto3.Session(profile_name='$AWS_PROFILE', region_name='$AWS_REGION').client('bedrock-agent-runtime')
      r = c.invoke_agent(agentId='$AGENT_ID', agentAliasId='$ALIAS_ID',
                         sessionId='cli-test', inputText='Find running shoes')
      print(''.join(e['chunk']['bytes'].decode() for e in r['completion'] if 'chunk' in e))
      "
SUMMARY
