# OpenRFID Type Normalizer

Drop-in patch for OpenRFID's `generic.py` that normalises non-standard filament
type strings to the closest valid entry — so tags with types like `PLA+`, `ABS+`,
or `PETG-RAPID` no longer silently break the `success_exporter`.

---

## Problem

OpenRFID's `GenericFilament` constructor validates `type` against `VALID_BASE_MATERIALS`.
Many real-world tags — and Spoolman's predefined filament database — use types that are
not in that list (`PLA+`, `ABS+`, `PETG+`, …).

When validation fails:
1. `GenericFilament.__init__` raises `ValueError`
2. OpenRFID fires `tag_parse_error` instead of `tag_read`
3. `parse_error_exporter` posts only `CARD_UID` to `/printer/filament_detect/set`
4. **`success_exporter` never runs** → U1 GUI receives no filament data (no type, no temps, no vendor)

The root cause is not the tag content — it is the strict allowlist in OpenRFID.

---

## Solution

`generic.py` in this folder is a patched drop-in replacement. It adds
`_derive_material_type()`, a three-step algorithm that maps any non-standard
type to the closest `VALID_BASE_MATERIALS` entry **without a hardcoded lookup table**:

```
Step 1 — exact match        PLA-CF      → PLA-CF   (no change, already valid)
Step 2 — strip trailing +   PLA+        → PLA
                             ABS+        → ABS
                             PETG+       → PETG
Step 3 — longest prefix     PETG-RAPID  → PETG
                             PLA-SUPER   → PLA
```

If no match is found, the original `ValueError` still raises — unknown garbage is
still rejected, only real variants of known materials pass through.

The derivation is logged at `WARNING` level so you can verify it in the OpenRFID
log (`/tmp/openrfid.log` or the journal).

---

## Compatibility

- **PAXX v1.4.1** (OpenRFID SHA `1a6f605`)
- **PAXX v1.5.2** (OpenRFID SHA `ddd1609`)

Both ship identical `generic.py` — the patch applies to both without modification.

---

## Deployment

> **Prerequisite:** Data Persistence must be active (`/oem/.debug` must exist).
> Without it, changes are lost on the next cold start.

```bash
# 1. Verify persistence is active
ssh u1 'ls /oem/.debug && echo "persistence OK"'

# 2. Back up the original
ssh u1 'cp /usr/local/share/openrfid/filament/generic.py \
           /usr/local/share/openrfid/filament/generic.py.bak'

# 3. Deploy the patched file
scp openrfid_type_normalizer/generic.py \
    root@10.0.20.215:/usr/local/share/openrfid/filament/generic.py

# 4. Quick syntax check on the U1
ssh u1 'python3 -c "
import sys
sys.path.insert(0, \"/usr/local/share/openrfid\")
from filament.generic import _derive_material_type
tests = [(\"PLA+\", \"PLA\"), (\"ABS+\", \"ABS\"), (\"PETG+\", \"PETG\"),
         (\"PETG-RAPID\", \"PETG\"), (\"PLA-CF\", \"PLA-CF\")]
for raw, expected in tests:
    result = _derive_material_type(raw)
    status = \"OK\" if result == expected else f\"FAIL (got {result!r})\"
    print(f\"{raw:15} → {result:10} {status}\")
"'
```

Expected output:
```
PLA+            → PLA        OK
ABS+            → ABS        OK
PETG+           → PETG       OK
PETG-RAPID      → PETG       OK
PLA-CF          → PLA-CF     OK
```

**5. Cold start required** — OpenRFID runs as root and cannot be restarted via
Moonraker or as the `lava` user. A full power cycle is needed for the patch to
take effect.

---

## Verifying after cold start

Scan a tag with a non-standard type and check the OpenRFID log:

```bash
# Should show WARNING line with the normalisation, then success_exporter firing
ssh u1 'grep -i "normalised\|success_exporter\|tag_read" /tmp/openrfid.log | tail -10'

# Confirm filament_detect received full data (MAIN_TYPE, VENDOR, temps)
ssh lava@10.0.20.215 "wget -qO- 'http://localhost:7125/printer/objects/query?filament_detect'" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for i, ch in enumerate(d['result']['status']['filament_detect']['info']):
    print(f'ch{i}: {ch.get(\"MAIN_TYPE\",\"-\")} / {ch.get(\"VENDOR\",\"-\")} / {ch.get(\"HOTEND_MAX_TEMP\",\"-\")}°C')
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

Once tested, the `_derive_material_type()` function and the modified validation
block are the basis for:

1. A PR to [`suchmememanyskill/OpenRFID`](https://github.com/suchmememanyskill/OpenRFID) — the canonical fix
2. Once merged, PAXX bumps the OpenRFID SHA → fix ships in firmware

The patch is intentionally minimal (one function + four lines changed in `__init__`)
to keep the upstream PR diff small and reviewable.
