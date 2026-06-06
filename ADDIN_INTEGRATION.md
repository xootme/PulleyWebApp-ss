# CAD Addin Integration Guide

This document explains how to integrate PulleyWebApp with CAD addins across multiple platforms (FreeCAD, Fusion 360, SolidWorks, Onshape, etc.).

## Architecture

Two parallel access patterns:

### 1. Web UI (Browser-based)
- User visits https://cheapcadtools.com/tools/pulleys
- Designs pulleys in browser
- Downloads via browser (respects session queue/concurrency)
- Files auto-imported by addin file watcher

**Pros:** Full UI, interactive design, visual feedback
**Cons:** Requires browser interaction

### 2. API Downloads (Direct, Addin-to-Server)
- Addin calls REST endpoint directly
- No browser needed
- Trial downloads tracked per machine_id
- Works offline (if design params already known)

**Pros:** Seamless, no browser needed, programmatic
**Cons:** Requires design params as input (no interactive UI)

## API Endpoints

All endpoints require machine_id for trial tracking (2/week limit).

### Download STEP
```http
POST /api/download/step
Content-Type: application/json

{
  "machine_id": "e4b78822ad5a5694b16d803865fe3ad8",
  "params": {
    "family": "HTD",
    "pitch": "5M",
    "teeth": "20",
    "bore": "8",
    "belt_height": "10",
    "clearance_preset": "STANDARD",
    "backlash_preset": "STANDARD",
    "hub_od": "20",
    "hub_height": "10",
    ... (all design parameters)
  }
}
```

Response: Binary STEP file OR JSON error

**Error 429 (Too Many Requests):**
```json
{
  "error": "Download limit reached: 2/2 per week",
  "code": "DOWNLOAD_LIMIT_EXCEEDED",
  "count": 2,
  "limit": 2
}
```

### Download DXF
```http
POST /api/download/dxf
Content-Type: application/json

{
  "machine_id": "...",
  "params": { ... design params ... }
}
```

### Download STL
```http
POST /api/download/stl
Content-Type: application/json

{
  "machine_id": "...",
  "params": { ... design params ... }
}
```

## Implementation: FreeCAD Addin (Reference)

The FreeCAD addin now uses both approaches:

### Browser Mode (Current)
1. Addin opens browser via `session.open_designer_with_session(url)`
2. User designs in web UI
3. User clicks download
4. File saved to watch directory
5. Addin auto-imports

### Future: Direct API Mode
Could be implemented using `AddinDownloader`:

```python
from exporters.addin_helpers import AddinDownloader

downloader = AddinDownloader(BASE_URL, machine_id())
params = get_design_from_dialog()  # User input or stored params
step_data = downloader.download_step(params)

# Save and import
with open(f'{WATCH_DIR}/pulley.step', 'wb') as f:
    f.write(step_data)
# Trigger import...
```

## Implementation: Fusion 360 Addin

The Fusion 360 addin currently opens the browser (same as FreeCAD).

### Option A: Extend Current Flow
- Keep browser-based design UI
- Add "API download" option to sidebar
- Captures design params from sidebar
- Calls API endpoint for download
- Auto-imports result

### Option B: Full Embedded API
- Design params captured from Fusion 360 document
- Call API directly (no browser)
- Auto-import STEP/DXF
- Seamless workflow

For now, Fusion 360 addin will continue with the browser approach. Direct API integration can be added later when needed.

## Helper Module: `exporters/addin_helpers.py`

Provides `AddinDownloader` class for any addin:

```python
from exporters.addin_helpers import AddinDownloader, DownloadLimitExceeded

downloader = AddinDownloader(
    base_url='https://cheapcadtools.com',
    machine_id='my-unique-id',
    timeout=30
)

try:
    step_data = downloader.download_step(params)
except DownloadLimitExceeded as e:
    print(f'Limit: {e.count}/{e.limit} per week')
except Exception as e:
    print(f'Error: {e}')
```

## Design Parameters

All `/api/download/*` endpoints accept the same `params` dict:

### Required Parameters
- `family`: "HTD", "GT", "STD", "T", "AT", "RPP", "Imperial"
- `pitch`: "3M", "5M", "8M", etc. (depends on family)
- `teeth`: integer, 12-100+
- `bore`: float, ≥1.0 mm
- `clearance_preset`: "TIGHT", "STANDARD", "LOOSE"
- `backlash_preset`: "NONE", "TIGHT", "STANDARD", "LOOSE"

### Hub Parameters (optional)
- `hub_od`: float, hub outer diameter (mm)
- `hub_height`: float (mm)
- `hub_screw_dia`: float (mm)
- `hub_screw_count`: integer
- `hub_captured_nut`: "0" or "1"

### Spokes (optional)
- `p2_spokes_enabled`: "0" or "1"
- `p2_spokes_hub_od`, `_rim_depth`, `_width`, etc.

### Flanges (optional)
- `flange_enabled`: "0" or "1"
- `flange_3dprint`, `_angle`, `_rim_radius`, etc.

Full parameter list: See `templates/index.html` form fields or `geometry/pulley_geometry.py`

## Trial Download Limits

- **2 downloads per week** per machine_id
- **7-day rolling window** (not calendar week)
- Resets every 7 days
- Applies to STEP, DXF, STL equally (total 2/week across all formats)

## Machine ID Generation

Each addin must generate a unique, stable machine_id:

### FreeCAD
```python
# cct_pulley/paths.py
def machine_id():
    """Unique ID for this FreeCAD installation."""
    import hashlib
    key = f"{platform.node()}:{os.path.expanduser('~')}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

### Fusion 360 (Future)
```python
def machine_id():
    """Unique ID for this Fusion 360 installation."""
    import platform, hashlib
    # Use Fusion project path or username
    key = platform.node()  # or os.getenv('USERNAME')
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

### General Pattern
```
machine_id = SHA256(hostname:homedir or username)[:16]
```

Goal: **Same ID across app restarts, different for different machines**

## Error Handling

All endpoints return:
- **200**: File data (binary)
- **400**: Missing/invalid parameters (check params dict)
- **429**: Trial limit exceeded (human-friendly message)
- **500**: Server error (check logs)

```python
try:
    data = downloader.download_step(params)
except DownloadLimitExceeded:
    ui.show_dialog('Download limit reached. Try again next week.')
except Exception as e:
    ui.show_dialog(f'Download failed: {e}')
```

## Testing

Test API endpoints with curl:

```bash
curl -X POST https://cheapcadtools.com/api/download/step \
  -H "Content-Type: application/json" \
  -d '{
    "machine_id": "test-123",
    "params": {
      "family": "HTD",
      "pitch": "5M",
      "teeth": "20",
      "bore": "8",
      "belt_height": "10",
      "clearance_preset": "STANDARD",
      "backlash_preset": "STANDARD",
      "hub_od": "20",
      "hub_height": "10"
    }
  }' -o pulley.step
```

## Status: FreeCAD ✓ | Fusion 360 (Browser mode)

- **FreeCAD**: Updated to use API for machine_id registration
- **Fusion 360**: Currently uses browser mode (open WEB_URL or LOCAL_URL)
  - API endpoints available for future direct integration
  - No changes needed now
- **SolidWorks**: Listener app pattern, to be implemented
- **Onshape**: Tab/API pattern, to be implemented
