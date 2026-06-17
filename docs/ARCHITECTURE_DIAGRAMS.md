# SearchaaS Architecture Diagrams

## 1. Application Layout (UI Structure)

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Header                                      │
│          SearchaaS — Retrieval Tester  |  Backend: FastAPI (REST)  │
├─────────────────────┬─────────────────────────────────────────────────┤
│                     │                                                 │
│   ConfigPane        │                  Main Area                      │
│   (Sidebar)         │  ┌────────────────────────────────────────────┐ │
│                     │  │ Header Info & Strategy Selection           │ │
│   ⚙️ Configuration  │  ├────────────────────────────────────────────┤ │
│                     │  │ 🔍 Query Panel                             │ │
│   ┌─────────────┐   │  │  • Backend toggle (FastAPI/MCP)           │ │
│   │ Atlas       │   │  │  • Strategy select (auto/vector/...)      │ │
│   │ Embeddings  │   │  │  • Query input & filters                  │ │
│   │ Planner     │   │  │  • Run button                             │ │
│   │ Retrieval   │   │  │                                            │ │
│   │ Server      │   │  ├────────────────────────────────────────────┤ │
│   └─────────────┘   │  │ 🧠 Intent Panel                            │ │
│                     │  │  • Intent classification                   │ │
│   [Download YAML]   │  │  • Query rewriting & entities             │ │
│                     │  │  • Inferred metadata filters              │ │
│   [Collapse]        │  │                                            │ │
│                     │  ├────────────────────────────────────────────┤ │
│                     │  │ 📝 Summary Panel                           │ │
│                     │  │  • LLM summary of results                 │ │
│                     │  │                                            │ │
│                     │  ├────────────────────────────────────────────┤ │
│                     │  │ 📚 Results                                 │ │
│                     │  │  • Ranked result cards                    │ │
│                     │  │  • Content preview (truncated)            │ │
│                     │  │  • Metadata toggles                       │ │
│                     │  │                                            │ │
│                     │  ├────────────────────────────────────────────┤ │
│                     │  │ 📜 MongoDB Pipeline                        │ │
│                     │  │  • Reconstructed aggregation pipeline     │ │
│                     │  │  • Download JSON                          │ │
│                     │  └────────────────────────────────────────────┘ │
│                     │                                                 │
└─────────────────────┴─────────────────────────────────────────────────┘
```

## 2. Component Hierarchy

```
App
├── Header
│   └── Backend label display
├── ConfigPane (Sidebar)
│   ├── Tabs (Atlas, Embeddings, Planner, Retrieval, Server)
│   ├── Field × multiple (TextInput, NumberInput, Select, Slider)
│   ├── Banner (required field validation)
│   └── Disclosure (YAML preview)
└── Main Area
    ├── Tabs view (Query on left, Results on right)
    │
    ├── Left Column (Query)
    │   └── QueryPanel
    │       ├── Segmented (FastAPI / MCP toggle)
    │       ├── Field × multiple (backend URLs, strategy, topK)
    │       ├── Disclosure (filters JSON)
    │       ├── Chips (strategy options)
    │       └── Button (Run search)
    │
    └── Right Column (Results)
        ├── Empty State (before first search)
        │   └── Instructional text
        │
        ├── Search Results (after running)
            ├── Chip group (AUTO, resolved strategy, result count)
            ├── IntentPanel
            │   └── Chips + query understanding info
            ├── SummaryPanel
            │   └── LLM summary text
            ├── ResultsList
            │   └── ResultTile × N
            │       ├── Content preview
            │       ├── Buttons (show full, show metadata)
            │       └── CodeBlock (metadata JSON)
            └── PipelinePanel
                ├── CodeBlock (JSON pipeline)
                └── Button (Download)
```

## 3. Data Flow Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                    React Frontend (ui_react/)                    │
│                                                                  │
│  [User Input] → QueryPanel                                      │
│                    ↓                                             │
│            Parse filters (JSON)                                 │
│                    ↓                                             │
│        Select backend & strategy                                │
│                    ↓                                             │
│         ┌─────────────────────┐                                │
│         │  api.ts              │                                │
│         │ ┌──────────────────┐ │                                │
│         │ │ runFastAPI()     │ │                                │
│         │ │ POST /retrieve/* │ │                                │
│         │ │  or /retrieve    │ │                                │
│         │ └──────────────────┘ │                                │
│         │ ┌──────────────────┐ │                                │
│         │ │ runMcp()         │ │                                │
│         │ │ JSON-RPC 2.0     │ │                                │
│         │ │ over SSE         │ │                                │
│         │ └──────────────────┘ │                                │
│         └─────────────────────┘                                │
│                    ↓                                             │
│            [Network Request]                                    │
└──────────────────────────────────────────────────────────────────┘
                      ↓ HTTP/SSE
┌──────────────────────────────────────────────────────────────────┐
│                   Python Backend (searchaas/)                    │
│                                                                  │
│  FastAPI (api/app.py)              FastMCP (mcp_server/server)  │
│  Routes:                           Tools:                        │
│  • /retrieve (auto)                • auto_search                │
│  • /retrieve/vector                • vector_search              │
│  • /retrieve/fulltext              • fulltext_search            │
│  • /retrieve/hybrid                • hybrid_search              │
│  • /retrieve/graph                 • graph_search               │
│  • /retrieve/parent-doc            • parent_doc_search          │
│                                                                  │
│         ↓                          ↓                             │
│  ┌──────────────────────────────────────────────┐               │
│  │         Container (app/bootstrap.py)         │               │
│  │  Wires all factories from AppConfig          │               │
│  └──────────────────────────────────────────────┘               │
│         ↓         ↓         ↓         ↓         ↓                │
│    ┌───────┐┌──────────┐┌───────┐┌────────┐┌────────┐          │
│    │Atlas  ││Embeddings││Query  ││Planning││Retrieval│         │
│    │Factory││Factory   ││Under- ││Engine  ││Factory  │         │
│    │       ││          ││standing││       ││         │         │
│    └───────┘└──────────┘└───────┘└────────┘└────────┘         │
│         ↓                    ↓                                   │
│    MongoDB                  LLM                                  │
│    Atlas                 (Gemini/OpenAI/                        │
│    (Vector Search,       Anthropic/Bedrock)                    │
│    Full-text Search,                                            │
│    Aggregation Pipeline)                                        │
│                                                                  │
│              ↓                                                   │
│    ┌──────────────────────────────┐                            │
│    │  RetrieveResponse            │                            │
│    │  • strategy: str             │                            │
│    │  • plan: dict (pipeline)     │                            │
│    │  • results: list[dict]       │                            │
│    │  • understood_query: dict    │                            │
│    │  • summary: str              │                            │
│    └──────────────────────────────┘                            │
└──────────────────────────────────────────────────────────────────┘
                      ↓ JSON Response
┌──────────────────────────────────────────────────────────────────┐
│                  React Frontend (continued)                      │
│                                                                  │
│  [Response Processing]                                          │
│         ↓                                                        │
│  ┌──────────────────────┐                                       │
│  │ IntentPanel          │                                       │
│  │ Display query        │                                       │
│  │ understanding        │                                       │
│  └──────────────────────┘                                       │
│         ↓                                                        │
│  ┌──────────────────────┐                                       │
│  │ SummaryPanel         │                                       │
│  │ Show LLM summary     │                                       │
│  └──────────────────────┘                                       │
│         ↓                                                        │
│  ┌──────────────────────┐                                       │
│  │ ResultsList          │                                       │
│  │ Render result cards  │                                       │
│  └──────────────────────┘                                       │
│         ↓                                                        │
│  ┌──────────────────────┐                                       │
│  │ PipelinePanel        │                                       │
│  │ Show aggregation     │                                       │
│  │ pipeline JSON        │                                       │
│  └──────────────────────┘                                       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## 4. Retrieval Strategy Decision Tree

```
Query Input
    ↓
┌─────────────────────────────────────────────────┐
│ Strategy Selection                              │
└─────────────────────────────────────────────────┘
    ↓
    ├─→ Manual Mode (user selects)
    │       ├─→ "vector"      → EmbeddingFactory → Vector Search
    │       ├─→ "fulltext"    → FulltextSearch (Lucene)
    │       ├─→ "hybrid"      → Vector + Fulltext (weighted)
    │       ├─→ "graph"       → Entity-based retrieval
    │       └─→ "parent-doc"  → Parent document chunks
    │
    └─→ Auto Mode (Planner decides)
            ↓
        ┌──────────────────────────────────────┐
        │ QueryUnderstandingLayer (LLM)        │
        │ • Extract entities                   │
        │ • Classify intent                    │
        │ • Infer metadata filters             │
        └──────────────────────────────────────┘
            ↓
        ┌──────────────────────────────────────┐
        │ RetrievalPlanner (LLM + PolicyStore) │
        │ Routes based on intent:              │
        │                                      │
        │ exact_lookup / policy_lookup         │
        │     → fulltext                       │
        │                                      │
        │ semantic_search / summarization      │
        │     → vector                         │
        │                                      │
        │ analytical / troubleshooting         │
        │     → hybrid                         │
        │                                      │
        │ (constrained to hybrid/vector/      │
        │  fulltext for auto mode)            │
        └──────────────────────────────────────┘
            ↓
        Strategy → Retriever → MongoDB Pipeline
```

## 5. Configuration System

```
┌─────────────────────────────────────┐
│     searchaas.yaml (YAML file)      │
│                                     │
│  atlas:                             │
│    uri: mongodb+srv://...           │
│    database: amazon                 │
│    collection: products-updated     │
│    vector_index: voyage_vector...   │
│    ...                              │
│                                     │
│  embeddings:                        │
│    provider: voyageai               │
│    config:                          │
│      model: voyage-4                │
│      ...                            │
│                                     │
│  planner:                           │
│    llm_provider: gemini             │
│    config:                          │
│      model: gemini-2.5-flash        │
│      ...                            │
│                                     │
│  retrieval:                         │
│    default_strategy: hybrid         │
│    hybrid:                          │
│      vector_weight: 0.6             │
│      fulltext_weight: 0.4           │
│                                     │
│  server:                            │
│    host: 0.0.0.0                    │
│    port: 8000                       │
│    ...                              │
│                                     │
└─────────────────────────────────────┘
            ↓
┌─────────────────────────────────────┐
│   ConfigLoader (config/loader.py)   │
│   • Parse YAML                      │
│   • Validate structure              │
│   • Return AppConfig object         │
└─────────────────────────────────────┘
            ↓
┌─────────────────────────────────────┐
│    Container (app/bootstrap.py)     │
│    • Instantiate factories          │
│    • Wire dependencies              │
│    • Create retrieval services      │
└─────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────┐
│  Ready to use:                                       │
│  • AtlasFactory → MongoDB client                    │
│  • EmbeddingFactory → embeddings model              │
│  • LLMFactory → chat model                          │
│  • RetrieverFactory → retrieval strategies          │
│  • QueryUnderstandingLayer → entity extraction      │
│  • RetrievalPlanner → strategy selection            │
└──────────────────────────────────────────────────────┘
```

## 6. Component Communication Pattern

```
┌──────────────────────────────────────┐
│           App (State)                │
│                                      │
│  [config] ──────────────────────┐    │
│  [collapsed]          ┌─────────┼────┤
│  [backend]            │         │    │
│  [strategy]           │         │    │
│  [topK]               │         │    │
│  [query]              │         │    │
│  [filtersText]        │         │    │
│  [loading]            │         │    │
│  [error]              │         │    │
│  [response]           │         │    │
│                       │         │    │
│  ┌─────────────────┐  │         │    │
│  │ setConfig       │  │         │    │
│  │ setCollapsed    │  │         │    │
│  │ setBackend      │  │         │    │
│  │ ... (setters)   │  │         │    │
│  │ onRun() {       │  │         │    │
│  │  → runSearch()  │  │         │    │
│  │ }               │  │         │    │
│  └─────────────────┘  │         │    │
└──────────────────────────────────────┘
    │                   │         │    │
    │ Props            │         │    │
    ↓                   ↓         ↓    ↓
┌───────────────────────────────────────────────────┐
│          Child Components (Presentational)        │
│                                                   │
│  ┌──────────────┐  ┌─────────────┐               │
│  │ Header       │  │ ConfigPane  │ (receives    │
│  │ • backend    │  │ • config    │  setters as  │
│  │   (read)     │  │ • collapsed │  callbacks)  │
│  └──────────────┘  │ • setConfig │               │
│                    │ • setCollaps │               │
│                    └─────────────┘               │
│                                                   │
│  ┌──────────────┐  ┌─────────────┐               │
│  │ QueryPanel   │  │ IntentPanel │               │
│  │ • backend    │  │ • understood│               │
│  │ • strategy   │  │ • autoMode  │               │
│  │ • topK       │  │             │               │
│  │ • query      │  │ (display    │               │
│  │ • loading    │  │  only)      │               │
│  │ • onRun()    │  └─────────────┘               │
│  │ (setters)    │                                │
│  └──────────────┘  ┌─────────────┐               │
│                    │ SummaryPanel│               │
│  ┌──────────────┐  │ • summary   │               │
│  │ ResultsList  │  │             │               │
│  │ • results    │  │ (display    │               │
│  │             │  │  only)      │               │
│  │ (display    │  └─────────────┘               │
│  │  only)      │                                │
│  └──────────────┘  ┌─────────────┐               │
│                    │ PipelinePanel               │
│                    │ • pipeline  │               │
│                    │             │               │
│                    │ (display    │               │
│                    │  + download) │             │
│                    └─────────────┘               │
│                                                   │
└───────────────────────────────────────────────────┘
```

## 7. File Organization

```
ui_react/
├── src/
│   │
│   ├── main.tsx ........................... React entry point
│   │   └─→ ReactDOM.createRoot(App)
│   │
│   ├── App.tsx ............................ Main container component
│   │   └─→ Manages all app state
│   │   └─→ Routes to sub-components
│   │
│   ├── styles.css ......................... Global styles + design tokens
│   │   └─→ 639 lines of carefully organized CSS
│   │   └─→ Design variables, components, utilities
│   │
│   ├── components/
│   │   │
│   │   ├── Header.tsx ..................... Top bar info
│   │   ├── ConfigPane.tsx ................. Sidebar + YAML editor (355 lines)
│   │   ├── QueryPanel.tsx ................. Query input form (169 lines)
│   │   ├── IntentPanel.tsx ................ Query understanding display (60 lines)
│   │   ├── SummaryPanel.tsx ............... LLM summary (30 lines)
│   │   ├── ResultsList.tsx ................ Result cards (46 lines)
│   │   ├── PipelinePanel.tsx .............. Pipeline display (33 lines)
│   │   │
│   │   └── UI.tsx ......................... Component library (155 lines)
│   │       ├─→ Button, TextInput, Select, Slider, etc.
│   │       ├─→ Field, Chip, Banner, CodeBlock
│   │       ├─→ Segmented, Tabs, Disclosure, Latency
│   │       └─→ All styled with utility classes
│   │
│   └── lib/
│       ├── types.ts ....................... TypeScript types & constants
│       │   └─→ Strategy, AppConfig, RetrieveResponse, etc.
│       ├── api.ts ......................... Backend client
│       │   ├─→ runFastAPI()
│       │   ├─→ runMcp()
│       │   ├─→ MCP session management
│       │   └─→ JSON-RPC protocol handling
│       ├── defaults.ts .................... Default config values
│       └── pipeline.ts .................... Pipeline visualization
│
├── index.html ............................ HTML entry point
├── vite.config.ts ........................ Vite build config
├── tsconfig.json ......................... TS config (references)
├── tsconfig.app.json ..................... TS config (app)
├── tsconfig.node.json .................... TS config (build tools)
├── package.json .......................... Dependencies & scripts
└── eslint.config.js ...................... ESLint rules
```

## 8. Design Token System

```
Color Hierarchy
├── Neutral (Canvas & Text)
│   ├── --bg:        #F2F4F9  (page background)
│   ├── --surface:   #FFFFFF  (cards/panels)
│   ├── --surface-2: #F8F9FC  (secondary areas)
│   ├── --text:      #11182B  (primary text)
│   ├── --text-2:    #49546A  (secondary text)
│   └── --text-3:    #8892A4  (muted text)
│
├── Accent (Primary Action - Violet to Indigo Gradient)
│   ├── --accent:      #5C47F5 (buttons, links)
│   ├── --accent-2:    #7C3AED (hover/active)
│   ├── --accent-soft: #EEF0FF (background)
│   └── --accent-glow: rgba(...) (shadow)
│
├── Border & Dividers
│   ├── --border:      #E4E7EE (primary borders)
│   └── --border-soft: #EDF0F7 (soft borders)
│
└── Status Colors (each with bg + border variants)
    ├── Green  #059669  (success, active)
    ├── Blue   #2563EB  (info, primary)
    ├── Amber  #D97706  (warning)
    ├── Red    #DC2626  (danger, error)
    └── Purple #7C3AED  (alternate)

Spacing & Sizing
├── Radius
│   ├── --radius:    12px (buttons, cards)
│   ├── --radius-sm: 8px  (inputs)
│   └── --radius-xs: 6px  (small elements)
│
└── Shadows
    ├── --shadow-xs:    0 1px 2px
    ├── --shadow-sm:    0 2px 6px
    ├── --shadow-md:    0 6px 18px
    └── --shadow-float: 0 12px 40px

Typography
├── Font: Inter (400, 500, 600, 700)
├── Monospace: JetBrains Mono
└── Base: 13.5px, 1.6 line-height
```

