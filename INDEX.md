# PulleyWebApp Documentation Index

Quick reference guide to all markdown files in the project. Read this first to navigate the docs.

## 🚀 Getting Started

### [CLAUDE.md](CLAUDE.md)
**Claude Code project instructions and constraints**
- Python 3.12 requirement (cadquery limitation)
- STEP export via subprocess (`.venv312\Scripts\python.exe`)
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
- Python 3.12 for STEP/cadquery
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

### [QUEUE_SYSTEM.md](QUEUE_SYSTEM.md)
**Queue system design and behavior**
- Single-user access enforcement (prevent crashes)
- FIFO queueing with automatic promotion
- Session timeouts: 5min active, 1min idle, 30sec stale
- Countdown timer, heartbeat mechanism
- Trial download tracking (2/week per machine_id)
- *Complete queue system specification*

### [QUEUE_DEPLOYMENT.md](QUEUE_DEPLOYMENT.md)
**Queue system deployment and environment detection**
- Online vs. standalone mode auto-detection
- Environment variable configuration
- WooCommerce integration notes
- *How the queue system adapts to different deployments*

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

### [3D_SpokeHeight.md](3D_SpokeHeight.md)
**Research notes on 3D spoke height calculation**
- Geometry and rendering for spoked pulleys
- Angular offset mathematics
- *Technical reference for spoke feature*

### [flange_feature.md](flange_feature.md)
**Flange design specification**
- 3D printing vs. metal flange approaches
- Design constraints (h_min = ht + a)
- Nub geometry for press-fit assembly
- *Specification for flange exports*

### [hub.md](hub.md)
**Hub design reference**
- Shaft retention methods (D-flat, keyway, tapered)
- Engineering standards and bore calculations
- *Reference for hub geometry*

## 📋 Project Management

### [TODO.md](TODO.md)
**Backlog and roadmap**
- Before public launch checklist
- STEP geometry improvements (small_step Rust project)
- Design metadata embedding (CCT schema versioning)
- Load testing dashboard
- Agent/headless API access
- CAD plugin roadmap (SolidWorks, FreeCAD, OnShape)
- *Living document of what's left to do*

### [CAD_Market.md](CAD_Market.md)
**CAD plugin market research**
- Feature comparison across platforms
- Plugin distribution channels
- Positioning and pricing strategy
- *Business/product research notes*

---

## 📂 File Organization Map

```
PulleyWebApp/
├── 📄 INDEX.md                    ← You are here
│
├── 🎯 Queue System
│   ├── QUEUE_SYSTEM.md            (Design & behavior)
│   └── QUEUE_DEPLOYMENT.md        (Environment setup)
│
├── 🚀 Deployment & Ops
│   ├── CLAUDE.md                  (Claude Code instructions)
│   ├── web_provisioning.md        (Deploy checklist & architecture)
│   └── cheapcadtools.md           (CheapCADTools.com hosting)
│
├── 🏗️ Architecture & Design
│   ├── CCT_Architecture.md        (Platform architecture)
│   ├── DECISIONS.md               (Architectural decision records)
│   └── TODO.md                    (Backlog & roadmap)
│
├── 📡 Integration
│   └── ADDIN_INTEGRATION.md       (CAD addin guide)
│
├── ⚙️ Features
│   ├── 3D_SpokeHeight.md          (Spoke height research)
│   ├── flange_feature.md          (Flange design spec)
│   └── hub.md                     (Hub design reference)
│
└── 📊 Research
    ├── CAD_Market.md             (Market research)
    └── (temp files: hub.md, CAD_Market.md, etc.)
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
2. [TODO.md](TODO.md) — Backlog and requirements
3. Feature-specific file (e.g., [flange_feature.md](flange_feature.md))

### I'm understanding the architecture
1. [CCT_Architecture.md](CCT_Architecture.md) — Big picture
2. [QUEUE_SYSTEM.md](QUEUE_SYSTEM.md) — Single-user access
3. [ADDIN_INTEGRATION.md](ADDIN_INTEGRATION.md) — Multi-platform support

### I'm debugging the queue
1. [QUEUE_SYSTEM.md](QUEUE_SYSTEM.md) — Behavior specification
2. [QUEUE_DEPLOYMENT.md](QUEUE_DEPLOYMENT.md) — Environment detection
3. [web_provisioning.md](web_provisioning.md) — API endpoints

---

## 📝 Notes

- Most docs are **specifications** (what should happen), not tutorials (how to do it)
- For **how-to guides**, see code comments and docstrings
- For **operations**, see [cheapcadtools.md](cheapcadtools.md) (server) and [CLAUDE.md](CLAUDE.md) (dev environment)
- For **why decisions**, see [DECISIONS.md](DECISIONS.md)
- For **what's left**, see [TODO.md](TODO.md)

Last updated: 2026-06-06
