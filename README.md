# U1 klipper configs

This is a general repo with configs to expand the Snapmaker U1's capabilities

## Disclaimer:

I'm not a Snapmaker's developer nor your tech support.
I just have a U1 and I'm happy to share what I did to mine, YMMV but always: [RTFM](https://www.klipper3d.org/) and follow the links for fuher documentation and informations.

# Things I did

- [Spoolman](spoolman/README.md) configuration for U1 multitool
- [SpoolLink Bridge](spoollink_bridge/README.md) — automatic RFID → Spoolman integration (tag scanned = spool activated)
- [OpenRFID Type Normalizer](openrfid_type_normalizer/README.md) — patch for OpenRFID that normalises non-standard filament types (PLA+, ABS+, …) so `success_exporter` doesn't silently fail
