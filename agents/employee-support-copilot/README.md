# Employee Support Copilot

A production-style chat application that routes employee questions to the right MongoDB Atlas collection via AiSearch, dynamically passing collection name and vector search configuration on every request.

---

## Architecture

```
Browser (Next.js UI)
    │  POST /api/chat  { query, forceDomain? }
    ▼
BFF (Next.js API Route — /api/chat)
    ├── classifier.ts   → domain + retrieval bias
    ├── AiSearch-client.ts → build payload + call AiSearch
    └── assembler.ts    → format answer + citations
         │
         │  POST /retrieve  { query, atlas: { collection, ... }, retrieval: { ... } }
         ▼
    AiSearch (localhost:8000)
         │
         ▼
    MongoDB Atlas
         ├── IT_helpdesk          collection
         └── employee_support     collection
```

**Key design principle:** The frontend never touches MongoDB Atlas. All retrieval flows through AiSearch, which receives full atlas overrides (collection name, vector index, search index, field keys) on every request. Switching collections is a config change only.

---

## Folder structure

```
agents/employee-support-copilot/
├── src/
│   ├── lib/
│   │   ├── collections.ts        ← Collection registry + routing signals (edit here to add collections)
│   │   ├── AiSearch-client.ts   ← AiSearch HTTP client + payload builder
│   │   ├── classifier.ts         ← Keyword/pattern query classifier
│   │   └── assembler.ts          ← Answer formatter + citation builder
│   ├── app/
│   │   ├── api/chat/route.ts     ← BFF POST /api/chat
│   │   ├── chat/page.tsx         ← Chat page (client component)
│   │   ├── layout.tsx
│   │   ├── page.tsx              ← Redirects / → /chat
│   │   └── globals.css
│   ├── components/
│   │   ├── ChatMessage.tsx       ← Renders user + assistant messages
│   │   ├── CitationCard.tsx      ← Source citation chip
│   │   └── ChatInput.tsx         ← Textarea + domain selector + suggestion chips
│   └── types/
│       └── chat.ts               ← Shared request/response types
├── .env.local.example
├── next.config.ts
├── package.json
├── tailwind.config.ts
└── tsconfig.json
```

---

## Quick start

### Prerequisites

- Node.js 18+
- AiSearch running at `http://localhost:8000` (see parent repo)
- MongoDB Atlas collections: `IT_helpdesk` and `employee_support` with Atlas Search + Vector Search indexes

### Run AiSearch first

```bash
# From the repo root
uvicorn AiSearch.api.app:app --host 0.0.0.0 --port 8000 --reload
```

### Run the copilot

```bash
cd agents/employee-support-copilot
cp .env.local.example .env.local
# Edit .env.local if needed (default points to localhost:8000)

npm install
npm run dev
```

Open [http://localhost:3000/chat](http://localhost:3000/chat).

---

## AiSearch request shape

Every query is translated to:

```json
{
  "query": "My VPN is not connecting",
  "top_k": 8,
  "atlas": {
    "collection": "IT_helpdesk",
    "vector_index": "it_helpdesk_vector_index",
    "search_index": "it_helpdesk_search_index",
    "text_key": "text",
    "embedding_key": "embedding"
  },
  "retrieval": {
    "vector_weight": 0.55,
    "fulltext_weight": 0.45,
    "num_candidates": 150
  },
  "summarize": false,
  "understand": false
}
```

---

## Collection routing rules

| Signal | Routed to |
|--------|-----------|
| VPN, laptop, MFA, SSO, Outlook, printer, device, password reset, software | `IT_helpdesk` |
| Leave, payroll, salary, travel policy, benefits, HR, holiday, promotion | `employee_support` |
| Low-confidence / ambiguous | Both collections (parallel fan-out, best answer wins) |

Retrieval bias:
- **Vector-heavy** (0.75/0.25) — fuzzy/semantic questions: "my laptop is acting weird"
- **Fulltext-heavy** (0.3/0.7) — exact-lookup questions: "travel reimbursement cap"
- **Auto** — everything else (collection default weights)

---

## Adding a third collection

1. Open `src/lib/collections.ts`
2. Add a new key to `COLLECTION_CONFIGS` with the collection's MongoDB name, index names, field keys, and badge styling.
3. Add keyword patterns to `DOMAIN_SIGNALS` for the new key.
4. That's it — the classifier, client, assembler, and UI all pick it up automatically.

```ts
// Example: legal_compliance collection
legal_compliance: {
  label: "Legal & Compliance",
  collection: "legal_compliance",
  vectorIndex: "legal_vector_index",
  searchIndex: "legal_search_index",
  textKey: "text",
  embeddingKey: "embedding",
  // ...
}
```

---

## Sample user journeys

### Journey 1 — Clear IT issue
> "VPN is not connecting on my Mac"

- Classifier: `IT_helpdesk` (confidence ~90%), bias: `auto`
- AiSearch payload: `collection: IT_helpdesk, vector_index: it_helpdesk_vector_index`
- Response: top VPN troubleshooting chunks + source citations

### Journey 2 — Clear HR question
> "What is the leave policy for new joiners?"

- Classifier: `employee_support` (confidence ~85%), bias: `fulltext-heavy`
- AiSearch payload: `collection: employee_support, fulltext_weight: 0.7`
- Response: leave policy document chunks

### Journey 3 — Ambiguous / dual-domain
> "How do I get remote access while travelling internationally?"

- Classifier: ambiguous (~52% IT, ~48% HR), bias: `vector-heavy`
- Fan-out: both collections queried in parallel
- Response: sectioned answer — IT remote access steps + HR travel policy

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AISEARCH_BASE_URL` | `http://localhost:8000` | AiSearch base URL |
| `AISEARCH_API_KEY` | _(empty)_ | Bearer token if AiSearch requires auth |
