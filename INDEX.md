# PulleyWebApp Documentation Index

Quick reference guide to all markdown files in the project. Read this first to navigate the docs.

## 🚀 Getting Started

### [CLAUDE.md](CLAUDE.md)
**Claude Code project instructions and constraints**
- Python 3.14 (cadquery removed; small_step Rust binary handles STEP)
- STEP export via subprocess (`.venv314\Scripts\python.exe`)
- Key file locations and deployment procedure
- *Read this first if working with Claude Code*

### [web_provisioning.md](web_provisioning.md)
**Web app deployment checklist and architecture**
- Render.com hosting (Flask + gunicorn)
- Cloudflare Worker routing
- Pre-deployment steps (docs, schema, tests, benchmarks)
- Local release build procedure
- *Read before deploying to production*

## 🏗️ Architecture & Design

### [CCT_Architecture.md](CCT_Architecture.md)
**Platform-wide architecture and business structure**
- CheapCAD Tools company/product overview
- Multi-tool platform strategy
- Release/licensing model
- *Context for why the project exists*

### [DECISIONS.md](DECISIONS.md)
**Architectural Decision Records (ADRs)**
- Key technical decisions and rationale
- ADR-001: Python 3.12 for STEP/cadquery (superseded by ADR-002: small_step Rust binary)
- Trade-offs and alternatives considered
- *Reference for "why did we choose X?"*

### [cheapcadtools.md](cheapcadtools.md)
**CheapCADTools.com hosting and operations**
- GreenGeeks FTP/SSH credentials and paths
- WordPress/Blockbase theme setup
- Render.com service IDs and env vars
- Contact form, WooCommerce, LiteSpeed cache
- *Ops manual for the main website*

## 🎯 Queue System (Single-User Access)

Queue design is documented in `CCT_Architecture.md` (Section 3 — Queue System).
A dedicated `QUEUE_SYSTEM.md` spec has not yet been written.

## 📡 API & Integration

### [ADDIN_INTEGRATION.md](ADDIN_INTEGRATION.md)
**CAD addin integration for multiple platforms**
- REST API endpoints for downloads (`/api/download/{step,dxf,stl}`)
- Machine_id based trial tracking
- Helper module usage (`AddinDownloader` class)
- FreeCAD, Fusion 360, SolidWorks, Onshape patterns
- Error handling and machine_id generation
- *Reference for building addins in any CAD package*

## ⚙️ Features & Implementation

### [flange_feature.md](flange_feature.md)
**Flange design specification**
- 3D printing vs. metal flange approaches
- Design constraints (h_min = ht + a)
- Nub geometry for press-fit assembly
- *Specification for flange exports*

## 📋 Project Management

### [ToDo.md](ToDo.md)
**Backlog and roadmap**
- Before public launch checklist
- STEP geometry improvements (small_step Rust project)
- Known bug: partial-height spokes generate OCCT-invalid STEP (guarded in `_run_ss_worker`)
- Design metadata embedding (CCT schema versioning)
- Load testing dashboard
- Agent/headless API access
- CAD plugin roadmap (SolidWorks, FreeCAD, OnShape)
- *Living document of what's left to do*

---

## 📂 File Organization Map

```
PulleyWebApp/
├── 📄 INDEX.md                    ← You are here
│
├── 🚀 Deployment & Ops
│   ├── CLAUDE.md                  (Claude Code instructions)
│   ├── web_provisioning.md        (Deploy checklist & architecture)
│   └── cheapcadtools.md           (CheapCADTools.com hosting & ops)
│
├── 🏗️ Architecture & Design
│   ├── CCT_Architecture.md        (Platform architecture + queue system)
│   ├── DECISIONS.md               (Architectural decision records)
│   └── ToDo.md                    (Backlog & roadmap)
│
├── 📡 Integration
│   └── ADDIN_INTEGRATION.md       (CAD addin guide)
│
└── ⚙️ Features
    └── flange_feature.md          (Flange design spec)
```

## 🔍 Quick Links by Role

### I'm deploying to production
1. [CLAUDE.md](CLAUDE.md) — Constraints & setup
2. [web_provisioning.md](web_provisioning.md) — Deployment checklist
3. [cheapcadtools.md](cheapcadtools.md) — Server credentials

### I'm building a CAD addin
1. [ADDIN_INTEGRATION.md](ADDIN_INTEGRATION.md) — Full integration guide
2. [QUEUE_SYSTEM.md](QUEUE_SYSTEM.md) — Queue behavior (if web-based)

### I'm adding a feature
1. [DECISIONS.md](DECISIONS.md) — Why existing choices were made
2. [ToDo.md](ToDo.md) — Backlog and requirements
3. Feature-specific file (e.g., [flange_feature.md](flange_feature.md))

### I'm understanding the architecture
1. [CCT_Architecture.md](CCT_Architecture.md) — Big picture
2. [QUEUE_SYSTEM.md](QUEUE_SYSTEM.md) — Single-user access
3. [ADDIN_INTEGRATION.md](ADDIN_INTEGRATION.md) — Multi-platform support

### I'm debugging the queue
1. [CCT_Architecture.md](CCT_Architecture.md) — Queue system design (Section 3)
2. [web_provisioning.md](web_provisioning.md) — API endpoints and env vars

---

## 📝 Notes

- Most docs are **specifications** (what should happen), not tutorials (how to do it)
- For **how-to guides**, see code comments and docstrings
- For **operations**, see [cheapcadtools.md](cheapcadtools.md) (server) and [CLAUDE.md](CLAUDE.md) (dev environment)
- For **why decisions**, see [DECISIONS.md](DECISIONS.md)
- For **what's left**, see [TODO.md](TODO.md)

Last updated: 2026-06-16
