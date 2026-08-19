# AiSearch Documentation Index

## Overview

This directory contains comprehensive documentation for the AiSearch project - a MongoDB Atlas-backed retrieval platform with AI-driven query planning and multiple search strategies.

**Generated:** June 8, 2026  
**Project:** AiSearch Phase 1  
**Focus:** React UI + TypeScript Frontend Analysis

---

## Documentation Files

### 1. **QUICK_REFERENCE.md** ⭐ START HERE
**Length:** ~450 lines | **Read Time:** 10-15 minutes

A condensed guide covering:
- Technology stack overview
- Main entry points and file locations
- Component architecture
- Retrieval strategies
- Configuration system
- Common development tasks
- Useful tips and tricks

**Best for:** Quick lookups, new developers, common questions

---

### 2. **CODEBASE_ANALYSIS.md** 
**Length:** ~637 lines | **Read Time:** 30-40 minutes

The most comprehensive analysis covering:
- **Project Structure:** Complete directory layout
- **Technology Stack:** Backend (Python/FastAPI/MCP) + Frontend (React/TypeScript/Vite)
- **React UI Details:** 
  - Component architecture and hierarchy
  - File organization and responsibilities
  - Data flow and state management
  - API integration patterns
- **Styling Approach:** Design tokens, color system, typography
- **Build Configuration:** Vite setup, TypeScript, scripts
- **Key Features & Workflows:** Query execution, configuration, strategies
- **File Inventory:** Complete listing with line counts

**Best for:** Deep understanding, architecture review, component details

---

### 3. **ARCHITECTURE_DIAGRAMS.md**
**Length:** ~464 lines | **Read Time:** 20-30 minutes

Visual diagrams and ASCII charts showing:
1. **Application Layout** - UI spatial structure (sidebar, main area, panels)
2. **Component Hierarchy** - Tree structure from App down
3. **Data Flow Diagram** - Frontend to backend communication
4. **Retrieval Strategy Decision Tree** - How strategies are chosen
5. **Configuration System** - YAML flow to runtime
6. **Component Communication Pattern** - Props and state passing
7. **File Organization** - Directory tree with descriptions
8. **Design Token System** - Colors, spacing, typography hierarchy

**Best for:** Visual learners, presentations, understanding connections

---

### 4. **README.md** (Original Project)
**Length:** ~250 lines

Original project documentation including:
- Project overview
- Setup instructions
- How to run (API, MCP, React)
- MCP testing guide (cURL/Postman)
- End-to-end flow diagram
- Technology choices

**Best for:** Initial setup, running the application, MCP testing

---

### 5. **Instructions.md** (Original Project)
**Length:** ~1,018 lines

Detailed architecture documentation covering:
- Full system architecture
- Factory pattern explanation
- Configuration system details
- Each module's purpose
- Database/API/MCP integration
- Deployment considerations

**Best for:** Understanding design decisions, system design, deep dives

---

## Reading Paths

### For New Developers
1. **QUICK_REFERENCE.md** (10 min) - Get oriented
2. **CODEBASE_ANALYSIS.md** sections 1-4 (15 min) - Understand structure
3. **README.md** (10 min) - Setup & run locally
4. **ARCHITECTURE_DIAGRAMS.md** (10 min) - Visualize components

### For UI/Frontend Development
1. **QUICK_REFERENCE.md** section "Component Architecture" (5 min)
2. **CODEBASE_ANALYSIS.md** sections 3-4 (25 min) - React details
3. **ARCHITECTURE_DIAGRAMS.md** sections 1-2, 6 (15 min) - Layout & patterns
4. **CODEBASE_ANALYSIS.md** section 4 (10 min) - Styling system

### For Backend Integration
1. **QUICK_REFERENCE.md** "API Integration" (5 min)
2. **CODEBASE_ANALYSIS.md** section 5 (15 min) - Data flow
3. **ARCHITECTURE_DIAGRAMS.md** section 3 (10 min) - Flow diagram
4. **README.md** (10 min) - MCP testing

### For Configuration & Deployment
1. **QUICK_REFERENCE.md** "Configuration System" (5 min)
2. **CODEBASE_ANALYSIS.md** section 5 (8 min) - Config details
3. **ARCHITECTURE_DIAGRAMS.md** section 5 (5 min) - Config flow
4. **Instructions.md** (full) - Deep architecture details

---

## Key Topics Quick Links

### Frontend (React/TypeScript)
- **Component Overview:** CODEBASE_ANALYSIS.md § 3
- **Layout Structure:** CODEBASE_ANALYSIS.md § 3 "Component Architecture"
- **Component Details:** CODEBASE_ANALYSIS.md § 3 "Key Components"
- **UI Library:** CODEBASE_ANALYSIS.md § 3 "UI.tsx"
- **Styling:** CODEBASE_ANALYSIS.md § 4
- **Entry Point:** QUICK_REFERENCE.md "Main Entry Points"

### Data & State Management
- **State Management:** CODEBASE_ANALYSIS.md § 5
- **API Integration:** CODEBASE_ANALYSIS.md § 5 "API Client"
- **Types:** CODEBASE_ANALYSIS.md § 5 "Types"
- **Data Flow Diagram:** ARCHITECTURE_DIAGRAMS.md § 3

### Configuration
- **Default Config:** CODEBASE_ANALYSIS.md § 5 "Default Configuration"
- **YAML Structure:** QUICK_REFERENCE.md "Configuration System"
- **Config System:** ARCHITECTURE_DIAGRAMS.md § 5

### Retrieval & Strategies
- **Strategies Table:** QUICK_REFERENCE.md "Retrieval Strategies"
- **Strategy Decision Tree:** ARCHITECTURE_DIAGRAMS.md § 4

### Design System
- **Colors & Tokens:** CODEBASE_ANALYSIS.md § 4 "Color Palette"
- **Typography:** CODEBASE_ANALYSIS.md § 4 "Typography"
- **CSS Classes:** QUICK_REFERENCE.md "CSS Classes"
- **Design Tokens:** ARCHITECTURE_DIAGRAMS.md § 8

---

## File Organization (This Project)

```
AI-Search/
├── DOCUMENTATION_INDEX.md        (this file)
├── QUICK_REFERENCE.md            (condensed guide)
├── CODEBASE_ANALYSIS.md          (comprehensive analysis)
├── ARCHITECTURE_DIAGRAMS.md      (visual diagrams)
├── README.md                      (original project)
├── Instructions.md                (original architecture)
│
├── AiSearch/                     (main project)
│   ├── ui_react/                 (React frontend - focus of analysis)
│   │   └── src/
│   │       ├── App.tsx           (main component)
│   │       ├── components/       (UI components)
│   │       ├── lib/              (types, api, defaults)
│   │       └── styles.css        (design system)
│   │
│   ├── api/                      (FastAPI backend)
│   ├── mcp_server/               (MCP backend)
│   ├── config/                   (configuration system)
│   └── [other modules]
│
├── requirements.txt              (Python dependencies)
└── .env.example                  (environment template)
```

---

## Technology Stack Summary

### Frontend (React UI - Primary Focus)
- **Framework:** React 18.3
- **Language:** TypeScript 6.0
- **Build Tool:** Vite 8.0
- **Styling:** Custom CSS (design tokens, no Tailwind)
- **Key Libraries:** js-yaml (YAML parsing in UI)

### Backend
- **REST API:** FastAPI
- **MCP Protocol:** FastMCP (JSON-RPC 2.0 over SSE)
- **Language:** Python 3.x
- **Database:** MongoDB Atlas
- **Orchestration:** LangChain 0.3+

---

## Component Overview

### Main Components
| Component | Type | Responsibility |
|-----------|------|-----------------|
| App.tsx | Container | State management, layout |
| Header.tsx | Presentational | Top bar info |
| ConfigPane.tsx | Form | YAML editor sidebar |
| QueryPanel.tsx | Form | Query input |
| IntentPanel.tsx | Display | Query understanding |
| SummaryPanel.tsx | Display | LLM summary |
| ResultsList.tsx | Display | Result cards |
| PipelinePanel.tsx | Display | MongoDB pipeline |
| UI.tsx | Library | 8+ reusable components |

### Layout
- **Grid-based:** 360px sidebar + flexible main
- **Collapsible:** Sidebar collapses to 52px rail
- **Two-column main:** Query (left) + Results (right)

---

## Development Commands

```bash
# Frontend (React)
cd AiSearch/ui_react
npm install
npm run dev           # Development server (localhost:5173)
npm run build         # Production build
npm run lint          # ESLint check

# Backend (Python)
uvicorn AiSearch.api.app:app --host 0.0.0.0 --port 8000
python -m AiSearch.mcp_server.server
```

---

## Common Questions

### Q: Where's the React entry point?
**A:** `main.tsx` → React DOM root → `App.tsx`
See: QUICK_REFERENCE.md "Main Entry Points" or CODEBASE_ANALYSIS.md § 3

### Q: How do I add a new form field?
**A:** Edit `ConfigPane.tsx`, update `lib/types.ts`, add CSS if needed
See: QUICK_REFERENCE.md "Common Tasks"

### Q: What retrieval strategies are supported?
**A:** vector, fulltext, hybrid, graph, parent-doc, auto
See: QUICK_REFERENCE.md "Retrieval Strategies"

### Q: How does the query execution flow work?
**A:** QueryPanel → API call → Backend processing → RetrieveResponse → Display panels
See: ARCHITECTURE_DIAGRAMS.md § 3 or CODEBASE_ANALYSIS.md § 7

### Q: What colors are in the design system?
**A:** Accent (violet-indigo), status (green/blue/amber/red/purple), neutral (light gray)
See: CODEBASE_ANALYSIS.md § 4 or QUICK_REFERENCE.md "Design System"

### Q: How do I change the styling?
**A:** Edit `styles.css`. Uses CSS variables for all design tokens
See: CODEBASE_ANALYSIS.md § 4 or ARCHITECTURE_DIAGRAMS.md § 8

---

## Key Files & Line Counts

### React Components
- App.tsx: 162 lines
- ConfigPane.tsx: 355 lines (largest)
- QueryPanel.tsx: 169 lines
- UI.tsx: 155 lines
- IntentPanel.tsx: 60 lines
- ResultsList.tsx: 46 lines
- PipelinePanel.tsx: 33 lines
- Header.tsx: 15 lines

### Support Files
- styles.css: 639 lines
- types.ts: 103 lines
- api.ts: 134 lines
- defaults.ts: 45 lines

---

## Design Principles

✅ **Type-Safe** - Full TypeScript coverage  
✅ **CSS-First** - Design tokens, no frameworks  
✅ **Modular** - Reusable UI components  
✅ **Responsive** - Grid-based, collapsible layout  
✅ **Accessible** - Semantic HTML, ARIA labels  
✅ **Pluggable** - Backend swappable (FastAPI/MCP)  
✅ **Configured** - YAML-driven, no hardcoded values  

---

## Next Steps

1. **Understand the basics:** Read QUICK_REFERENCE.md
2. **Deep dive:** Read CODEBASE_ANALYSIS.md
3. **Visualize:** Review ARCHITECTURE_DIAGRAMS.md
4. **Set up locally:** Follow README.md
5. **Start developing:** Pick a component and explore

---

## Contact & Questions

For detailed architecture questions, refer to:
- **System design:** Instructions.md
- **Implementation details:** CODEBASE_ANALYSIS.md
- **Visual explanations:** ARCHITECTURE_DIAGRAMS.md
- **Quick lookups:** QUICK_REFERENCE.md

---

**Documentation Generated:** June 8, 2026  
**Project:** AiSearch Phase 1  
**Focus Area:** React UI + TypeScript Frontend  
**Total Documentation:** ~2,800 lines across 5 files

