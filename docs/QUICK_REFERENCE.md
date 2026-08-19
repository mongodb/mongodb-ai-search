# AiSearch - Quick Reference Guide

## Project at a Glance

**AiSearch** = MongoDB Atlas-backed retrieval platform with:
- 6 retrieval strategies (vector, fulltext, hybrid, graph, parent-doc, auto)
- Query understanding & AI-driven planning
- Python backend (FastAPI + MCP) + React frontend
- YAML-based configuration system
- Production-ready with pluggable providers

---

## Technology Stack

### Backend
- **FastAPI** (REST API, port 8000)
- **FastMCP** (JSON-RPC 2.0 over SSE, port 8001)
- **MongoDB Atlas** (primary datastore)
- **LangChain 0.3+** (orchestration)
- **Python 3.x**

### Frontend
- **React 18.3** (component framework)
- **TypeScript 6.0** (type safety)
- **Vite 8.0** (build tool)
- **Custom CSS** (design tokens, no Tailwind)
- **js-yaml** (YAML parsing)

---

## Main Entry Points

### React App
```
Entry: index.html
  └─→ main.tsx
      └─→ App.tsx (main component)
          ├─→ Header.tsx
          ├─→ ConfigPane.tsx (sidebar)
          ├─→ QueryPanel.tsx (left)
          ├─→ IntentPanel.tsx (right)
          ├─→ SummaryPanel.tsx (right)
          ├─→ ResultsList.tsx (right)
          └─→ PipelinePanel.tsx (right)
```

### Python Backend
```
API: api/app.py (FastAPI)
  └─→ /retrieve/* endpoints
  
MCP: mcp_server/server.py (FastMCP)
  └─→ tools/call (JSON-RPC)
  
Config: config/loader.py
  └─→ AiSearch.yaml
  
Container: app/bootstrap.py
  └─→ Wires all factories
```

---

## File Locations

### Core React Components
| Component | Path | Lines | Purpose |
|-----------|------|-------|---------|
| App | `src/App.tsx` | 162 | Main container, state management |
| Header | `src/components/Header.tsx` | 15 | Top bar info |
| ConfigPane | `src/components/ConfigPane.tsx` | 355 | YAML editor sidebar |
| QueryPanel | `src/components/QueryPanel.tsx` | 169 | Query input form |
| IntentPanel | `src/components/IntentPanel.tsx` | 60 | Query understanding display |
| SummaryPanel | `src/components/SummaryPanel.tsx` | 30 | LLM summary |
| ResultsList | `src/components/ResultsList.tsx` | 46 | Result cards |
| PipelinePanel | `src/components/PipelinePanel.tsx` | 33 | Pipeline display |
| UI Library | `src/components/UI.tsx` | 155 | Reusable components |

### Core Libraries
| Library | Path | Purpose |
|---------|------|---------|
| Types | `src/lib/types.ts` | Type definitions & constants |
| API Client | `src/lib/api.ts` | FastAPI + MCP integration |
| Defaults | `src/lib/defaults.ts` | Default config values |
| Pipeline | `src/lib/pipeline.ts` | Pipeline visualization |

### Styling
| File | Lines | Purpose |
|------|-------|---------|
| styles.css | 639 | Design tokens + components |

---

## Component Architecture

### Layout (Grid-based)
```
App Shell: grid-template-columns: 360px 1fr
├── Sidebar (360px, collapsible to 52px)
└── Main (flexible)
    ├── Header
    └── Two-column grid
        ├── Left: QueryPanel
        └── Right: Result panels (conditional)
```

### State Management (App.tsx)
```typescript
// Configuration
config, setConfig
collapsed, setCollapsed

// Backend selection
backend, setBackend
fastapiUrl, setFastapiUrl
mcpUrl, setMcpUrl

// Query parameters
strategy, setStrategy
topK, setTopK
query, setQuery
filtersText, setFiltersText

// Response states
loading, setLoading
error, setError
response, setResponse

// Key function
onRun() → runSearch() → setResponse()
```

---

## Retrieval Strategies

| Strategy | Use Case | Key Features |
|----------|----------|--------------|
| **vector** | Semantic similarity | Uses embeddings, fast |
| **fulltext** | Lexical search | Atlas Search (Lucene), exact matching |
| **hybrid** | Combined search | Vector + fulltext with weights |
| **graph** | Entity relationships | Graph-based traversal |
| **parent-doc** | Document chunks | Retrieve parent docs from chunks |
| **auto** | Intelligent routing | Planner decides based on intent |

**Intent → Strategy Mapping (Auto Mode):**
- `exact_lookup`, `policy_lookup` → fulltext
- `semantic_search`, `summarization` → vector
- `analytical`, `troubleshooting` → hybrid

---

## API Integration

### FastAPI (REST)
```typescript
POST /retrieve (auto mode)
POST /retrieve/vector
POST /retrieve/fulltext
POST /retrieve/hybrid
POST /retrieve/graph
POST /retrieve/parent-doc

Body: { query, top_k, filters }
Response: RetrieveResponse
```

### MCP (JSON-RPC 2.0 over SSE)
```
1. initialize()
   └─→ Get mcp-session-id header
2. notifications/initialized()
3. tools/call(tool_name, arguments)

Tools:
- auto_search
- vector_search
- fulltext_search
- hybrid_search
- graph_search
- parent_doc_search
```

---

## Design System

### Colors
```
Neutral:     --bg (#F2F4F9), --surface (#FFFFFF), --text (#11182B)
Accent:      --accent (#5C47F5) → --accent-2 (#7C3AED)
Status:      --green, --blue, --amber, --red, --purple
Borders:     --border (#E4E7EE), --border-soft (#EDF0F7)
```

### Spacing
```
Radius:   --radius (12px), --radius-sm (8px), --radius-xs (6px)
Shadows:  --shadow-xs, --shadow-sm, --shadow-md, --shadow-float
Font:     Inter (UI), JetBrains Mono (code)
Base:     13.5px, 1.6 line-height
```

### CSS Classes
```
Layout:    .app-shell, .sidebar, .main-area, .two-col
Cards:     .card, .flex, .flex-between
Forms:     .field, .input, .textarea, .select, .slider
Status:    .banner, .chip, .latency
Panels:    .intent-panel, .summary-panel, .result, .code-block
```

---

## Configuration System

### YAML Structure
```yaml
atlas:
  uri: mongodb+srv://...
  database: amazon
  collection: products-updated
  vector_index: voyage_vector_index
  search_index: default
  text_key: text
  embedding_key: embedding-vectors
  dimensions: 512

embeddings:
  provider: voyageai  # voyageai, gemini, openai, azure_openai, cohere, huggingface, bedrock_titan
  config:
    model: voyage-4
    voyage_api_key: ${VOYAGE_API_KEY}

planner:
  llm_provider: gemini  # gemini, openai, azure_openai, anthropic, bedrock
  config:
    model: gemini-2.5-flash
    google_api_key: ${GOOGLE_API_KEY}
  default_top_k: 20

retrieval:
  default_strategy: hybrid
  hybrid:
    vector_weight: 0.6
    fulltext_weight: 0.4
  vector:
    num_candidates: 200

server:
  host: 0.0.0.0
  port: 8000
  mcp_host: 0.0.0.0
  mcp_port: 8001
  mcp_transport: streamable-http
  log_level: info
```

---

## Type Definitions

### Strategy
```typescript
type Strategy = "auto" | "vector" | "fulltext" | "hybrid" | "graph" | "parent-doc"
```

### RetrieveResponse
```typescript
interface RetrieveResponse {
  strategy: string;           // Resolved strategy
  plan: Record<string, any>;  // MongoDB aggregation pipeline
  results: RetrieveResult[];  // Search results
  understood_query?: UnderstoodQuery | null;  // Query understanding
  summary?: string | null;    // LLM summary
}
```

### UnderstoodQuery
```typescript
interface UnderstoodQuery {
  raw: string;                          // Original query
  corrected?: string;                   // Corrected version
  rewritten: string;                    // Rewritten for search
  entities: string[];                   // Extracted entities
  metadata_filters: Record<string, any>; // Inferred filters
  intent: string;                       // Intent classification
}
```

---

## Development Workflow

### Setup
```bash
cd AiSearch/ui_react
npm install
npm run dev  # Starts on http://localhost:5173
```

### Build
```bash
npm run build  # Produces dist/
```

### Scripts
```bash
npm run dev      # Development server
npm run build    # Production build
npm run lint     # ESLint
npm run preview  # Preview production build
```

### Python Backend
```bash
# FastAPI
uvicorn AiSearch.api.app:app --host 0.0.0.0 --port 8000

# MCP
python -m AiSearch.mcp_server.server

```

---

## Key Features & Workflows

### Query Execution Flow
```
Input (QueryPanel)
  ↓ Validate filters (JSON)
  ↓ Select backend & strategy
  ↓ Call API (FastAPI or MCP)
  ↓ Backend processes:
    • Query understanding
    • Retrieval planning (if auto)
    • Execute retrieval
    • Generate summary
  ↓ RetrieveResponse
  ↓ Display results:
    • IntentPanel (understanding)
    • SummaryPanel (summary)
    • ResultsList (results)
    • PipelinePanel (pipeline)
```

### Configuration Editing
```
ConfigPane (Sidebar)
  ↓ Select tab (Atlas, Embeddings, etc.)
  ↓ Edit fields (live validation)
  ↓ Required field indicators (chips)
  ↓ Download AiSearch.yaml button
  ↓ Preview YAML (disclosure)
```

### Result Visualization
```
For each RetrieveResponse:
  1. Show strategy chip + result count
  2. Show query understanding (if available)
  3. Show LLM summary (or top result preview)
  4. List individual result cards
     - Content (truncated)
     - Show full/less toggle
     - Metadata toggle with JSON display
  5. Show reconstructed MongoDB pipeline
     - Download as JSON
```

---

## Current Design Principles

✅ **Type-Safe:** Full TypeScript coverage
✅ **CSS-First:** Design tokens, no UI frameworks
✅ **Modular:** Reusable components in UI.tsx
✅ **Responsive:** Grid-based layout, collapsible sidebar
✅ **Accessible:** Semantic HTML, ARIA labels
✅ **Pluggable:** Backend swappable (FastAPI/MCP)
✅ **Configured:** YAML-driven, no hardcoded values

---

## Common Tasks

### Add a new form field to ConfigPane
1. Find the tab in ConfigPane.tsx
2. Add a `<Field>` component with appropriate input (TextInput, Select, Slider, etc.)
3. Update `update` or `updateAtlas` call
4. Sync types in `lib/types.ts` (AppConfig interface)

### Add a new UI component
1. Create in `src/components/UI.tsx`
2. Export from UI.tsx
3. Import in relevant component
4. Style with class names in `styles.css`

### Connect to backend
1. Add endpoint/tool in Python backend
2. Add mapping to `lib/types.ts` (STRATEGY_MAP)
3. Update `lib/api.ts` to handle new endpoint
4. Update UI components to call the API

### Change color scheme
1. Update CSS variables in `styles.css` `:root` block
2. All components automatically inherit new colors
3. Specific color overrides via `--green`, `--blue`, etc.

---

## CORS Configuration

Frontend (localhost:5173) can reach:
- FastAPI backend (localhost:8000)
- MCP server (localhost:8001)

CORS enabled for `http://localhost:*` on both backends.

---

## Useful Tips

### Trailing Slash Issue (MCP)
Always use `/mcp` (no trailing slash) to avoid 307 redirects that lose POST body.

### JSON Filters
Metadata filters must be valid JSON objects. Blank input = `{}`.

### Strategy-Specific Fields
ConfigPane dynamically shows only required `atlas.*` fields for the selected strategy.

### Auto Mode
Planner chooses from: hybrid, vector, or fulltext (even if broader strategies exist).

### Pipeline Download
Each result's pipeline can be downloaded as `{strategy}-pipeline.json` for debugging in mongosh.

---

## Documentation Files

- **CODEBASE_ANALYSIS.md** — Detailed analysis of all components
- **ARCHITECTURE_DIAGRAMS.md** — Visual diagrams and ASCII charts
- **QUICK_REFERENCE.md** — This file
- **README.md** — Project overview & setup
- **Instructions.md** — Full architecture documentation

