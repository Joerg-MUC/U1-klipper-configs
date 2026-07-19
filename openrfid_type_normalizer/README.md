# OpenRFID Type Normalizer

Drop-in patch for OpenRFID's `generic.py` that normalises non-standard filament
type strings (e.g. `PLA+`, `ABS+`, `PETG-RAPID`) so tags from Spoolman and
third-party apps no longer silently break the `success_exporter`.

---

## Problem

OpenRFID's `GenericFilament` constructor validates `type` against `VALID_BASE_MATERIALS`.
Many real-world tags — and Spoolman's predefined filament database — use types that are
not in that list (`PLA+`, `ABS+`, `PETG+`, …).

When validation fails:
1. `GenericFilament.__init__` raises `ValueError`
2. OpenRFID fires `tag_parse_error` instead of `tag_read`
3. `parse_error_exporter` posts only `CARD_UID` to `/printer/filament_detect/set`
4. **`success_exporter` never runs** → U1 GUI receives no filament data

---

## Solution

`generic.py` in this folder is a patched drop-in replacement. It adds
`_derive_material_type()` with a four-step resolution chain:

| Step | What | Active |
|------|------|--------|
| 1 | Exact match against `VALID_BASE_MATERIALS` | Always |
| 2 | Explicit user map (`type_map` in config file) | When config file exists |
| 3 | Strip trailing `+`: `ABS+` → `ABS` | `strip_plus: true` in config |
| 4 | Longest-prefix match: `PETG-RAPID` → `PETG` | `prefix_match: true` in config |

**If the config file does not exist:** only Steps 1 and 2 are active (Step 2 with
an empty map = no-op). Steps 3 and 4 default to OFF. Unknown types still raise
`ValueError` — no silent guessing without explicit opt-in.

All normalisation is logged at `WARNING` level so every substitution is traceable
in `/oem/printer_data/logs/openrfid.log`.

---

## Config file

`openrfid_type_normalizer.cfg` in this folder is the template. Deploy it to:

```
/oem/printer_data/config/extended/openrfid_type_normalizer.cfg
```

```yaml
# Strip trailing '+' from filament type before matching (Step 3)
# Options: true, false
strip_plus: true

# Match filament type against longest valid prefix (Step 4)
# Options: true, false
prefix_match: true

# Explicit type mapping, checked before algorithmic steps (Step 2)
# Values must be valid entries from VALID_BASE_MATERIALS
# type_map:
#   ABS-PLUS: ABS
#   SILK-PLA: PLA
type_map:
```

A cold start is required after any config change (OpenRFID loads the config once
at startup).

---

## Compatibility

- **PAXX v1.4.1** (OpenRFID SHA `1a6f605`)
- **PAXX v1.5.2** (OpenRFID SHA `ddd1609`)

Both ship identical `generic.py` — the patch applies to both without modification.

---

## Deployment

> **Prerequisite:** Data Persistence must be active (`/oem/.debug` must exist).

```bash
# 1. Verify persistence is active
ssh u1 'ls /oem/.debug && echo "persistence: OK"'

# 2. Back up original
ssh u1 'cp /usr/local/share/openrfid/filament/generic.py \
           /usr/local/share/openrfid/filament/generic.py.bak'

# 3. Deploy patched generic.py
scp openrfid_type_normalizer/generic.py \
    root@10.0.20.215:/usr/local/share/openrfid/filament/generic.py

# 4. Deploy config file
scp openrfid_type_normalizer/openrfid_type_normalizer.cfg \
    root@10.0.20.215:/oem/printer_data/config/extended/openrfid_type_normalizer.cfg

# 5. Syntax + logic check on U1
ssh u1 'python3 -c "
import sys
sys.path.insert(0, \"/usr/local/share/openrfid\")
from filament.generic import _derive_material_type, _STRIP_PLUS, _PREFIX_MATCH
print(f\"strip_plus={_STRIP_PLUS}, prefix_match={_PREFIX_MATCH}\")
tests = [(\"PLA+\", \"PLA\"), (\"ABS+\", \"ABS\"), (\"PETG+\", \"PETG\"),
         (\"PETG-RAPID\", \"PETG\"), (\"PLA-CF\", \"PLA-CF\"), (\"PLA\", \"PLA\")]
for raw, expected in tests:
    result = _derive_material_type(raw)
    status = \"OK\" if result == expected else f\"FAIL (got {result!r})\"
    print(f\"{raw:<15} -> {str(result):<10} {status}\")
"'
```

Expected output:
```
strip_plus=True, prefix_match=True
PLA+            -> PLA        OK
ABS+            -> ABS        OK
PETG+           -> PETG       OK
PETG-RAPID      -> PETG       OK
PLA-CF          -> PLA-CF     OK
PLA             -> PLA        OK
```

**Cold start required** — OpenRFID runs as root and cannot be restarted via
Moonraker or as the `lava` user. A full power cycle is needed after deployment.

---

## Verifying after cold start

```bash
# Normalisation warnings in log (one line per substituted type)
ssh u1 'grep "normalised\|type normaliser" /oem/printer_data/logs/openrfid.log | tail -10'

# Full filament data received (MAIN_TYPE, VENDOR, temps set)
ssh u1-lava "wget -qO- 'http://localhost:7125/printer/objects/query?filament_detect'" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for i, ch in enumerate(d['result']['status']['filament_detect']['info']):
    print(f'ch{i}: {ch.get(\"MAIN_TYPE\",\"-\")} / {ch.get(\"VENDOR\",\"-\")} / {ch.get(\"HOTEND_MAX_TEMP\",\"-\")}C')
"
```

---

## Rollback

```bash
ssh u1 'cp /usr/local/share/openrfid/filament/generic.py.bak \
           /usr/local/share/openrfid/filament/generic.py'
# then cold start
```

---

## Path to upstream

Once tested, the goal is a PR to [`suchmememanyskill/OpenRFID`](https://github.com/suchmememanyskill/OpenRFID).
The config file approach maps cleanly to OpenRFID's existing INI config convention.
