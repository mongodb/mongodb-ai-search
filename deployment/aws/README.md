# AWS Deployment

Deploy SearchaaS on AWS. Three independent pieces:

| # | What                         | Where it runs                          | Script                                | Default? |
| - | ---------------------------- | -------------------------------------- | ------------------------------------- | -------- |
| 1 | React UI (`ui_react`)        | S3 static website (existing bucket)    | `s3-ui/deploy.sh`                     | yes      |
| 2 | FastAPI + FastMCP backends   | Amazon ECS **Express Mode**            | `ecs/deploy.sh`                       | **yes**  |
| 3 | FastMCP backend (alternative)| Amazon Bedrock **AgentCore Runtime**   | `agentcore/deploy.sh`                 | **no — opt-in only** |

The **default backend** is ECS Express Mode (option 2). AgentCore (option 3) is
a separate, explicitly-confirmed alternative for the FastMCP surface — it is
never deployed unless you run its script and confirm.

## Layout

```
deployment/aws/
├── README.md                      ← this file
├── Dockerfile                     ← shared FastAPI/FastMCP image (used by ECS)
├── trust-ecs.json                 ← ECS task trust policy (reference)
├── s3-ui/                         ← (1) UI on S3
│   ├── deploy.sh
│   └── README.md
├── ecs/                           ← (2) DEFAULT backend: ECS Express Mode
│   ├── deploy.sh
│   ├── primary-container-fastapi.json
│   ├── primary-container-fastmcp.json
│   └── README.md
└── agentcore/                     ← (3) OPT-IN: FastMCP on Bedrock AgentCore
    ├── Dockerfile                 ← ARM64, MCP at :8000/mcp
    ├── deploy.sh
    └── README.md
```

## Prerequisites

- AWS CLI v2, configured (`aws configure`)
- Docker with `buildx` (for backend images)
- Node.js + npm (for the UI build)
- A MongoDB Atlas cluster: export `ATLAS_URI` and `ATLAS_DB`

## Typical order

```bash
export AWS_REGION=us-east-1
export ATLAS_URI='mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true'
export ATLAS_DB='your_database_name'

# 1. Deploy the backends (default: ECS Express Mode). Prints two HTTPS URLs.
./deployment/aws/ecs/deploy.sh

# 2. Deploy the UI to your existing S3 bucket, pointing at those URLs.
./deployment/aws/s3-ui/deploy.sh \
  --bucket my-existing-ui-bucket \
  --api-url "https://searchaas-fastapi.ecs.${AWS_REGION}.on.aws" \
  --mcp-url "https://searchaas-fastmcp.ecs.${AWS_REGION}.on.aws/mcp"
```

### Optional: FastMCP on Bedrock AgentCore instead of / in addition to ECS

```bash
./deployment/aws/agentcore/deploy.sh   # requires typing 'deploy-agentcore' to confirm
```

See each subfolder's `README.md` for full details, config knobs, and teardown.
