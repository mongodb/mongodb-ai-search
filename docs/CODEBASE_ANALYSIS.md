# SearchaaS Codebase Analysis

## Project Overview

**SearchaaS** is a MongoDB Atlas-backed retrieval platform built around the **Factory pattern**. It's a Phase 1 implementation that provides a production-ready retrieval system with multiple search strategies and intelligent query planning.

**Key Features:**
- Multi-strategy retrieval (Vector / Full-text / Hybrid / Graph / Parent-doc)
- Query understanding & entity extraction
- AI-driven retrieval planning
- YAML-based configuration
- Multiple UI surfaces (FastAPI REST, FastMCP, React)
- TypeScript/React frontend with modern design system

---

## 1. Project Structure & Layout

### Root Directory
```
AI-Search/
├── searchaas/                    # Main Python backend package
├── requirements.txt              # Python dependencies
├── README.md                     # Project documentation
├── Instructions.md               # Detailed architecture guide
├── .env & .env.example          # Environment configuration
└── venv/                        # Python virtual environment
```

### Python Backend Structure (`searchaas/`)
```
searchaas/
├── config/                      # YAML config + loader (single source of truth)
│   ├── loader.py               # Load & parse searchaas.yaml
│   └── searchaas.yaml          # Configuration file
├── infrastructure/              # AtlasFactory for MongoDB connectivity
│   └── factory.py              # MongoDB client management
├── domain/                      # Pydantic models
│   ├── chunk.py                # Document/chunk models
│   └── source_ref.py           # Source reference models
├── embeddings/                  # EmbeddingFactory (multi-provider support)
│   └── factory.py              # Handles Gemini, Bedrock Titan, OpenAI, etc.
├── llm/                        # LLMFactory (multi-provider LLM support)
│   └── factory.py              # Gemini, Azure/OpenAI, Anthropic, Bedrock
├── query_understanding/         # Query processing layer
│   ├── rewriter.py            # Query rewriting & normalization
│   ├── extractor.py           # Entity & metadata extraction
│   └── intent.py              # Intent classification
├── planning/                    # RetrievalPlanner + PolicyStore
│   ├── engine.py              # Planner logic
│   └── policy.py              # Atlas-managed policy store
├── retrieval/                   # RetrieverFactory (strategy implementations)
│   ├── vector.py              # Vector search
│   ├── fulltext.py            # Full-text search
│   ├── hybrid.py              # Hybrid (vector + fulltext)
│   ├── graph.py               # Graph-based retrieval
│   └── parent_doc.py          # Parent document retrieval
├── app/                         # Application container & bootstrapping
│   └── bootstrap.py            # build_container() - wires all factories
├── api/                         # FastAPI REST surface
│   └── app.py                  # FastAPI application definition
├── mcp_server/                  # FastMCP surface (JSON-RPC over SSE)
│   └── server.py              # MCP protocol implementation
├── ui_react/                    # React + TypeScript frontend
│   └── (see section 3 below)   # Modern UI with Vite + TailwindCSS
├── observability/               # Logging & diagnostics
│   └── logging.py             # Structured logging configuration
└── diagnose.py                 # Diagnostic tools
```

---

## 2. Technology Stack

### Backend (Python)
- **Framework:** FastAPI (REST) + FastMCP (JSON-RPC over SSE)
- **Database:** MongoDB Atlas with PyMongo + Motor
- **Core Libraries:**
  - LangChain 0.3+ (core, community, mongodb integration)
  - Pydantic 2.6+ (data validation)
  - UVicorn (ASGI server)
  
- **LLM & Embedding Providers (pluggable via YAML):**
  - **Embeddings:** Voyage AI, Google Gemini, OpenAI, Azure OpenAI, Cohere, HuggingFace, Bedrock Titan
  - **LLMs:** Google Gemini, OpenAI, Azure OpenAI, Anthropic, Amazon Bedrock

### Frontend (TypeScript/React)
- **Framework:** React 18.3.1
- **Build Tool:** Vite 8.0.12
- **Language:** TypeScript 6.0.2
- **Styling:** Custom CSS with design tokens (no Tailwind)
- **Additional Libraries:**
  - js-yaml (for YAML parsing in config editor)
  
- **Design System:** MongoDB LeafyGreen UI principles (custom implementation)

### Development Tools
- **Package Manager:** npm with pnpm lockfile support
- **Linting:** ESLint with React-specific rules
- **Type Checking:** TypeScript with strict compiler options

---

## 3. React UI (`ui_react/`) - Detailed Structure

### Directory Layout
```
ui_react/
├── src/
│   ├── App.tsx                 # Main app component (container)
│   ├── main.tsx                # React DOM entry point
│   ├── styles.css              # Global styles + design tokens
│   ├── components/
│   │   ├── Header.tsx          # Top bar with project title & backend info
│   │   ├── ConfigPane.tsx      # Collapsible side pane for YAML config editor
│   │   ├── QueryPanel.tsx      # Query input & retrieval strategy selection
│   │   ├── IntentPanel.tsx     # Query understanding output display
│   │   ├── SummaryPanel.tsx    # LLM-generated summary of results
│   │   ├── ResultsList.tsx     # Individual result cards with metadata
│   │   ├── PipelinePanel.tsx   # MongoDB aggregation pipeline visualization
│   │   └── UI.tsx              # Reusable UI component library
│   └── lib/
│       ├── types.ts            # TypeScript type definitions & constants
│       ├── api.ts              # FastAPI + MCP client implementation
│       ├── defaults.ts         # Default configuration values
│       └── pipeline.ts         # Pipeline visualization logic
├── index.html                  # HTML entry point
├── vite.config.ts             # Vite build configuration
├── tsconfig.json              # TypeScript configuration
├── package.json               # Dependencies & scripts
└── eslint.config.js           # Linting rules
```

### Component Architecture

#### Layout Structure (App.tsx)
The main application uses a **2-column grid layout**:
```
┌─────────────────────────────────────────┐
│         Header                          │
├──────────────┬──────────────────────────┤
│              │                          │
│ ConfigPane   │     Main Area            │
│ (Sidebar)    │  ┌──────────┬─────────┐ │
│              │  │  Query   │ Results │ │
│              │  │  Panel   │ Panels  │ │
│              │  └──────────┴─────────┘ │
│              │                          │
└──────────────┴──────────────────────────┘
```

**Grid CSS:**
- `grid-template-columns: 360px 1fr` (expanded)
- `grid-template-columns: 52px 1fr` (collapsed)
- Sidebar collapses to a vertical rail on smaller screens

#### Key Components

##### 1. **Header.tsx**
- Displays project title: "SearchaaS — Retrieval Tester"
- Shows MongoDB Atlas info & current backend (FastAPI or FastMCP)
- Minimal, informational component

##### 2. **ConfigPane.tsx** (Collapsible Sidebar)
**Responsibilities:**
- YAML configuration editor with tabs for different config sections
- Live validation of required `atlas.*` fields per retrieval strategy
- Visual indicators (chips) for missing required fields
- Download `searchaas.yaml` functionality

**Tabs:**
- **Atlas:** Connection URI, database, collection, indices, embedding configuration
- **Embeddings:** Provider selection (Voyage AI, Gemini, OpenAI, etc.) + provider config
- **Planner:** LLM provider selection + LLM config + default top_k
- **Retrieval:** Default strategy, hybrid weights, vector candidates
- **Server:** Host, port, MCP settings, logging level

**Features:**
- Collapsible to a "rail" showing just icons
- Real-time YAML validation
- Chip-based required field indicators
- Preview YAML in a disclosure component

##### 3. **QueryPanel.tsx**
**Responsibilities:**
- Backend selection toggle (FastAPI REST ↔ FastMCP)
- Endpoint URL inputs for both backends
- Retrieval strategy dropdown + visual chips
- Top-K parameter slider
- Query text input (4-row textarea)
- Optional metadata filters (JSON) with validation
- Run search button

**Key States:**
- `backend`: "fastapi" or "mcp"
- `strategy`: "auto", "vector", "fulltext", "hybrid", "graph", "parent-doc"
- `topK`: 1-50
- `filtersText`: JSON string (with error state)
- `loading`: Shows spinner during execution

##### 4. **IntentPanel.tsx**
**Displays query understanding output:**
- **Intent Classification:** Shows detected intent (exact_lookup, semantic_search, etc.)
- **Strategy Mapping:** Compares planner-chosen strategy with intent-expected strategy
- **Raw vs. Rewritten:** Shows original query and LLM-rewritten version
- **Entities:** Extracted named entities (max 14 shown)
- **Inferred Filters:** Metadata filters extracted by LLM

**Color-coded feedback:**
- Green chip if chosen strategy matches expected
- Yellow/red warning if mismatch

##### 5. **SummaryPanel.tsx**
**Displays LLM-generated summary:**
- Shows server-provided summary (if available)
- Falls back to top result preview if no summary available
- Gracefully handles missing results

##### 6. **ResultsList.tsx**
**Renders individual result cards:**
- Ranked list (Result 1, Result 2, etc.)
- Truncated content preview (800 chars by default)
- "Show full" / "Show less" toggle for long results
- Metadata display with "Show metadata" toggle
- JSON formatting for metadata

##### 7. **PipelinePanel.tsx**
**Displays MongoDB aggregation pipeline:**
- JSON visualization of the full pipeline
- Download as `{strategy}-pipeline.json`
- Copy-to-clipboard button for the code block
- Shows the reconstructed pipeline for the resolved strategy

##### 8. **UI.tsx** (Component Library)
Reusable UI primitives styled with custom CSS:

**Form Controls:**
- `Button` - with variants (default, primary, ghost), sizes (default, sm), loading state
- `TextInput`, `TextArea`, `NumberInput` - form inputs
- `Select` - dropdown with flexible option format
- `Slider` - range input with min/max/step
- `Field` - wrapper with label, required indicator, hint text

**Display Components:**
- `Chip` - inline badges with color variants (gray, accent, green, blue, amber, red, purple)
- `Banner` - alert box with severity (info, success, warn, danger)
- `CodeBlock` - syntax-highlighted code with copy button
- `Latency` - performance badge (fast/mid/slow)

**Interaction Components:**
- `Segmented` - button group for mutually exclusive options
- `Tabs` - tab navigation
- `Disclosure` - collapsible content panel

---

## 4. Styling Approach

### Design System (Custom CSS)
**No Tailwind CSS** — uses custom CSS variables for a cohesive design token system.

#### Color Palette
```css
:root {
  /* Canvas */
  --bg:           #F2F4F9;      /* Light background */
  --surface:      #FFFFFF;      /* Card/panel backgrounds */
  --surface-2:    #F8F9FC;      /* Secondary surface */
  
  /* Borders */
  --border:       #E4E7EE;      /* Primary border */
  --border-soft:  #EDF0F7;      /* Soft border */
  
  /* Text */
  --text:         #11182B;      /* Primary text */
  --text-2:       #49546A;      /* Secondary text */
  --text-3:       #8892A4;      /* Tertiary text (muted) */
  
  /* Accent: Violet-Indigo Gradient */
  --accent:       #5C47F5;      /* Primary action */
  --accent-2:     #7C3AED;      /* Accent hover/active */
  --accent-soft:  #EEF0FF;      /* Soft accent background */
  --accent-glow:  rgba(92, 71, 245, 0.18); /* Accent shadow */
  
  /* Status Colors */
  --green:        #059669;  --green-bg: #ECFDF5;    --green-border: #A7F3D0;
  --blue:         #2563EB;  --blue-bg:  #EFF6FF;    --blue-border:  #BFDBFE;
  --amber:        #D97706;  --amber-bg: #FFFBEB;    --amber-border: #FDE68A;
  --red:          #DC2626;  --red-bg:   #FEF2F2;    --red-border:   #FECACA;
  --purple:       #7C3AED;  --purple-bg:#F5F3FF;    --purple-border:#DDD6FE;
  
  /* Sizing & Spacing */
  --radius:       12px;     /* Button border radius */
  --radius-sm:    8px;      /* Input border radius */
  --radius-xs:    6px;      /* Small elements */
  
  /* Shadows */
  --shadow-xs:    0 1px 2px rgba(17,24,43,0.05);
  --shadow-sm:    0 2px 6px rgba(17,24,43,0.07);
  --shadow-md:    0 6px 18px rgba(17,24,43,0.09);
  --shadow-float: 0 12px 40px rgba(17,24,43,0.13);
}
```

#### Typography
**Fonts:**
- **UI:** Inter (400, 500, 600, 700)
- **Monospace:** JetBrains Mono (400, 500)

**Base Font Size:** 13.5px with 1.6 line-height

#### Responsive Patterns
- **Sidebar:** 360px wide (collapsible to 52px rail)
- **Main area:** 1fr flexible width
- **Cards:** 16px padding, 8px gap between sections
- **Form fields:** 100% width in flex containers with `grid-template-columns: 1fr 1fr` for side-by-side pairs

#### Key Style Classes
```css
/* Layout */
.app-shell               /* Main grid container */
.sidebar                 /* Left navigation pane */
.main-area              /* Right content area */
.two-col                /* 2-column layout within main */

/* Cards & Containers */
.card                   /* Main content panel */
.card.empty-state       /* Empty state placeholder */
.flex                   /* Flexbox container */
.flex-between           /* Flex with space-between */

/* Forms */
.field                  /* Field container */
.field-label            /* Field label styling */
.input, .textarea, .select /* Form inputs */
.slider                 /* Range input */

/* Status & Info */
.banner                 /* Alert boxes */
.chip                   /* Inline badges */
.latency               /* Performance indicator */

/* Panels */
.intent-panel           /* Intent display section */
.summary-panel          /* Summary display section */
.result                 /* Individual result card */
.code-block            /* Code formatting */
```

---

## 5. Data Flow & API Integration

### State Management (App.tsx)
The main App component manages all application state using React hooks:

```typescript
// Configuration
const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG);
const [collapsed, setCollapsed] = useState(false);

// Backend selection
const [backend, setBackend] = useState<Backend>("fastapi");
const [fastapiUrl, setFastapiUrl] = useState("http://localhost:8000");
const [mcpUrl, setMcpUrl] = useState("http://localhost:8001/mcp");

// Query parameters
const [strategy, setStrategy] = useState<Strategy>("auto");
const [topK, setTopK] = useState(20);
const [query, setQuery] = useState("");
const [filtersText, setFiltersText] = useState("{}");

// Response & loading states
const [loading, setLoading] = useState(false);
const [error, setError] = useState<string | null>(null);
const [response, setResponse] = useState<RetrieveResponse | null>(null);
```

### API Client (lib/api.ts)
Two backend implementations:

#### FastAPI (REST)
```typescript
async function runFastAPI(
  apiBase: string,
  strategy: Strategy,
  payload: { query: string; top_k: number; filters: Record<string, unknown> }
): Promise<RetrieveResponse>
```
- POST to `/retrieve/{strategy}` or `/retrieve` for auto
- Direct JSON request/response

#### MCP (Model Context Protocol)
```typescript
async function runMcp(
  mcpUrl: string,
  strategy: Strategy,
  payload: { query: string; top_k: number; filters: Record<string, unknown> }
): Promise<RetrieveResponse>
```
- JSON-RPC 2.0 over Server-Sent Events (SSE)
- Session-based: `initialize` → `notifications/initialized` → `tools/call`
- Stateful session management with `_sessions` Map

### Types (lib/types.ts)
Core TypeScript definitions:

```typescript
export type Strategy = "auto" | "vector" | "fulltext" | "hybrid" | "graph" | "parent-doc";

export interface AppConfig {
  atlas: AtlasConfig;           // MongoDB connection & indices
  embeddings: { provider: string; config: Record<string, unknown> };
  planner: { llm_provider: string; config: Record<string, unknown>; default_top_k: number };
  retrieval: { ... };           // Strategy-specific settings
  server: { ... };              // Host, port, MCP config
}

export interface RetrieveResponse {
  strategy: string;             // Resolved strategy
  plan: Record<string, unknown>;// MongoDB aggregation pipeline
  results: RetrieveResult[];    // Search results
  understood_query?: UnderstoodQuery | null;
  summary?: string | null;      // LLM-generated summary
}

export interface UnderstoodQuery {
  raw: string;
  corrected?: string;
  rewritten: string;
  entities: string[];
  metadata_filters: Record<string, unknown>;
  intent: string;
}
```

### Default Configuration (lib/defaults.ts)
```typescript
export const DEFAULT_CONFIG: AppConfig = {
  atlas: {
    uri: "${ATLAS_URI}",
    database: "${ATLAS_DB:-amazon}",
    collection: "products-updated",
    vector_index: "voyage_vector_index",
    search_index: "default",
    text_key: "text",
    embedding_key: "embedding-vectors",
    relevance_score_fn: "cosine",
    dimensions: 512,
  },
  embeddings: { provider: "voyageai", config: {...} },
  planner: { llm_provider: "gemini", config: {...}, default_top_k: 20 },
  retrieval: { default_strategy: "hybrid", hybrid: {...}, vector: {...} },
  server: { host: "0.0.0.0", port: 8000, ... }
};
```

---

## 6. Build & Development Setup

### Build Configuration (vite.config.ts)
```typescript
export default defineConfig({
  plugins: [react()],
})
```
- Standard Vite React setup with Hot Module Replacement (HMR)
- TypeScript support via Vite's native loader
- Fast development server with instant refresh

### Scripts (package.json)
```json
{
  "scripts": {
    "dev": "vite",                    // Start dev server (localhost:5173)
    "build": "tsc -b && vite build",  // TypeScript + Vite build
    "lint": "eslint .",               // ESLint check
    "preview": "vite preview"         // Preview production build
  }
}
```

### TypeScript Configuration (tsconfig.json)
- Multi-project setup with references:
  - `tsconfig.app.json` - Application code
  - `tsconfig.node.json` - Build tool code
- Strict mode enabled
- JSX set to `react-jsx` (React 17+ automatic runtime)

---

## 7. Key Features & Workflows

### Query Execution Flow

```
User Input (QueryPanel)
    ↓
Parse & validate filters (JSON)
    ↓
Select backend & strategy
    ↓
API Call (FastAPI or MCP)
    ↓
Backend Processing:
  1. Query Understanding (entity extraction, intent classification)
  2. Retrieval Planning (if auto mode)
  3. Retrieve documents (vector/fulltext/hybrid/graph/parent-doc)
  4. Summarize results (LLM)
  ↓
Response Object:
  - Strategy chosen
  - Query understanding output
  - Results list
  - Summary
  - MongoDB aggregation pipeline
  ↓
Display Results:
  - IntentPanel (query understanding)
  - SummaryPanel (LLM summary)
  - ResultsList (individual cards)
  - PipelinePanel (aggregation pipeline)
```

### Configuration Management
1. **Live Editing:** ConfigPane allows editing all config sections with live YAML preview
2. **Required Fields:** Dynamically validated per strategy
3. **YAML Export:** Download current state as `searchaas.yaml`
4. **Provider Selection:** Dropdown for embeddings & LLM providers with nested config

### Retrieval Strategies
- **Vector:** Semantic similarity using embeddings
- **Full-text:** Lexical search with Atlas Search index
- **Hybrid:** Combination of vector + full-text with configurable weights
- **Graph:** Entity-based retrieval (requires special indexing)
- **Parent-doc:** Retrieve parent documents based on chunk similarity
- **Auto:** Planner-driven selection based on query intent

---

## 8. Current Limitations & Design Notes

### CSS-First Approach
- No UI framework (no LeafyGreen npm package used)
- All styles in single `styles.css` file
- Design tokens as CSS variables for consistency
- Custom component implementations for full control

### State Management
- Flat component hierarchy with props drilling
- Suitable for current complexity
- Could benefit from Context API or state management library if features expand

### Styling Cohesion
- Intentional design system with clear token hierarchy
- Consistent spacing, colors, and typography
- Ready for scaling to larger applications

### Testing Infrastructure
- ESLint configured but no unit/integration tests visible
- Suitable for adding Jest/Vitest in future

---

## 9. File Inventory Summary

### React/TypeScript Files (15 total)
```
Components (8):
  - App.tsx                  (162 lines)
  - Header.tsx              (15 lines)
  - ConfigPane.tsx          (355 lines)
  - QueryPanel.tsx          (169 lines)
  - IntentPanel.tsx         (60 lines)
  - SummaryPanel.tsx        (30 lines)
  - ResultsList.tsx         (46 lines)
  - PipelinePanel.tsx       (33 lines)
  - UI.tsx                  (155 lines)

Library (4):
  - main.tsx                (10 lines)
  - api.ts                  (134 lines)
  - types.ts                (103 lines)
  - defaults.ts             (45 lines)
  - pipeline.ts             (not yet examined)

Configuration (3):
  - tsconfig.json
  - vite.config.ts
  - index.html
```

### Styling
- **styles.css** (639 lines)
  - Design tokens
  - Component classes
  - Layout utilities
  - Responsive patterns

---

## 10. Key Takeaways

### Architecture Strengths
✅ Clear separation of concerns (config, API, components)
✅ Type-safe with full TypeScript coverage
✅ Design system via CSS variables for consistency
✅ Pluggable backends (FastAPI vs MCP)
✅ Factory pattern in Python allows component swapping via YAML
✅ React UI (LeafyGreen / MongoDB Design System)

### UI/UX Highlights
✅ Modern, clean interface inspired by MongoDB LeafyGreen
✅ Collapsible sidebar for configuration
✅ Visual feedback for required fields and validation
✅ Multi-step result visualization (intent → summary → results → pipeline)
✅ JSON/YAML code blocks with copy functionality
✅ Color-coded status indicators

### Technology Choices
✅ React 18 for component framework
✅ Vite for fast development experience
✅ TypeScript for type safety
✅ Custom CSS for design system control
✅ FastAPI + MCP for flexible backend connectivity
✅ MongoDB Atlas as primary data store

### Development Notes
- **Entry Point:** `main.tsx` → React DOM root → `App.tsx`
- **Build:** Vite + TypeScript compilation
- **Dev Server:** `npm run dev` (typically port 5173)
- **Backend URLs:** Configurable (FastAPI: 8000, MCP: 8001)
- **CORS:** Enabled for localhost development

