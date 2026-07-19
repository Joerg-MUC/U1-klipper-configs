# OpenRFID Type Normalizer — DEPRECATED

> **This approach has been superseded.** The YAML-shim in this folder is no longer
> the recommended way to normalise filament types on the Snapmaker U1.

---

## Current approach

The normalisation logic is now part of OpenRFID itself via dedicated `.cfg` sections,
without patching `generic.py` or maintaining a separate YAML config file.

**Code:** [`Joerg-MUC/OpenRFID` — branch `paxx/fixes`](https://github.com/Joerg-MUC/OpenRFID/tree/paxx/fixes)  
**Upstream PR:** [`suchmememanyskill/OpenRFID#24`](https://github.com/suchmememanyskill/OpenRFID/pull/24)

### What changed

Instead of replacing `generic.py` with a YAML-reading shim, the new approach:

- Adds `[type_normalizer]` and `[type_map]` sections to `openrfid_user.cfg`
- Updates `main.py` to read those sections and call `init_type_normalizer()` at startup
- `generic.py` is clean — no shim, no YAML dependency

### Config (in `/oem/printer_data/config/extended/openrfid_user.cfg`)

```ini
[type_normalizer]
strip_plus   = true    # ABS+ → ABS, PLA+ → PLA
prefix_match = true    # PETG-RAPID → PETG
type_map     = false   # enable explicit remapping rules in [type_map]

#[type_map]
#ABS-PLUS : ABS
#SILK-PLA : PLA
```

### Deployment

Three files need to be deployed from the `paxx/fixes` branch:

```bash
scp src/filament/generic.py     root@10.0.20.215:/usr/local/share/openrfid/filament/generic.py
scp src/main.py                 root@10.0.20.215:/usr/local/share/openrfid/main.py
scp src/config/configuration.py root@10.0.20.215:/usr/local/share/openrfid/config/configuration.py
```

Cold start required. The `openrfid_user.cfg` change survives PAXX updates;
the three Python files need to be re-deployed after each PAXX firmware update
until PR #24 is merged and PAXX bumps the OpenRFID SHA.

---

## Why this folder still exists

Kept for historical reference and as documentation of the YAML-shim approach.
The files (`generic.py`, `openrfid_type_normalizer.cfg`) are no longer deployed
on the U1.
