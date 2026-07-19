"""
OpenRFID — filament/generic.py (patched)
=========================================
Drop-in replacement for /usr/local/share/openrfid/filament/generic.py on the
Snapmaker U1 with PAXX CFW.

Patch: adds _derive_material_type() to normalise non-standard filament type
strings (e.g. "PLA+", "ABS+", "PETG-RAPID") to a valid VALID_BASE_MATERIALS
entry before GenericFilament.__init__ raises ValueError.

Without this patch, any tag whose type field is not in VALID_BASE_MATERIALS
causes OpenRFID to fire tag_parse_error instead of tag_read — so
success_exporter never runs and the U1 GUI receives no filament data.

Config file (optional):
  /oem/printer_data/config/extended/openrfid_type_normalizer.cfg
  If the file does not exist, only the user map (Step 0) and exact match
  (Step 1) are active. Steps 2 and 3 default to OFF.

Upstream: https://github.com/suchmememanyskill/OpenRFID  (src/filament/generic.py)
Base SHA:  ddd1609e9abe9cd37c4b8fa1a0e4307b976d5fd4  (PAXX v1.4.1 + v1.5.2 identical)
"""
import configparser
import hashlib
import logging
import os
from .valid_materials import VALID_BASE_MATERIALS


_CONFIG_PATH = "/oem/printer_data/config/extended/openrfid_type_normalizer.cfg"


def _load_normalizer_config():
    """
    Load type normaliser config from _CONFIG_PATH.
    Returns (user_map, strip_plus_enabled, prefix_match_enabled).
    If the file does not exist all features default to OFF / empty.
    """
    if not os.path.exists(_CONFIG_PATH):
        return {}, False, False

    cfg = configparser.ConfigParser()
    cfg.read(_CONFIG_PATH)

    opts = cfg["material_type_normalizer"] if "material_type_normalizer" in cfg else {}
    strip_plus    = cfg.getboolean("material_type_normalizer", "strip_plus",    fallback=False)
    prefix_match  = cfg.getboolean("material_type_normalizer", "prefix_match",  fallback=False)

    user_map = dict(cfg["material_type_map"]) if "material_type_map" in cfg else {}
    # Normalise keys to uppercase to match the .upper() applied by tag processors
    user_map = {k.upper(): v for k, v in user_map.items()}

    logging.info(
        f"OpenRFID type normaliser: loaded {len(user_map)} map entries, "
        f"strip_plus={strip_plus}, prefix_match={prefix_match}"
    )
    return user_map, strip_plus, prefix_match


# Loaded once at module import (= OpenRFID start). Cold start required after config changes.
_USER_MAP, _STRIP_PLUS, _PREFIX_MATCH = _load_normalizer_config()


def _derive_material_type(raw: str):
    """
    Attempt to derive a valid VALID_BASE_MATERIALS entry from a non-standard type string.

    Resolution order:
      Step 1  Exact match against VALID_BASE_MATERIALS          (always active)
      Step 0  User map from openrfid_type_normalizer.cfg        (active when file exists)
      Step 2  Strip trailing '+': ABS+ → ABS                   (config: strip_plus = on)
      Step 3  Longest-prefix match: PETG-RAPID → PETG          (config: prefix_match = on)

    Returns the resolved type string, or None if nothing matches.
    """
    # Step 1: exact match — no normalisation needed
    if raw in VALID_BASE_MATERIALS:
        return raw

    # Step 0: explicit user map — takes priority over algorithmic steps
    if raw in _USER_MAP:
        return _USER_MAP[raw]

    # Precompute stripped form used by steps 2 and 3
    stripped = raw.rstrip('+')

    # Step 2: strip trailing '+' (ABS+ → ABS, PLA+ → PLA, PETG+ → PETG)
    if _STRIP_PLUS and stripped in VALID_BASE_MATERIALS:
        return stripped

    # Step 3: longest-prefix match (PETG-RAPID → PETG, PLA-SUPER → PLA)
    #         Sort descending by length so PLA-CF beats PLA.
    if _PREFIX_MATCH:
        for valid in sorted(VALID_BASE_MATERIALS, key=len, reverse=True):
            if raw.startswith(valid) or stripped.startswith(valid):
                return valid

    return None


def to_rgba(argb: int) -> int:
    a = (argb >> 24) & 0xFF
    r = (argb >> 16) & 0xFF
    g = (argb >> 8) & 0xFF
    b = argb & 0xFF

    rgba = (r << 24) | (g << 16) | (b << 8) | a
    return rgba

class GenericFilament:
    def __init__(self,
                 source_processor: str,
                 unique_id: str,
                 manufacturer: str,
                 type: str, # TODO: Should probably be an enum?
                 modifiers: list[str],
                 colors : list[int], # Format 0xAARRGGBB
                 diameter_mm: float,
                 weight_grams: float,
                 hotend_min_temp_c: float,
                 hotend_max_temp_c: float,
                 bed_temp_c: float,
                 drying_temp_c: float,
                 drying_time_hours: float,
                 manufacturing_date: str, # ISO 8601 date string
                 td: float = 0.0 # Transmission Distance in mm for HueForge/OrcaSlicer-FullSpectrum
                 ):
        self.source_processor = source_processor
        self.unique_id = unique_id
        self.manufacturer = manufacturer
        self.type = type
        self.modifiers = modifiers
        self.colors = colors
        self.diameter_mm = diameter_mm
        self.weight_grams = weight_grams
        self.hotend_min_temp_c = hotend_min_temp_c
        self.hotend_max_temp_c = hotend_max_temp_c
        self.bed_temp_c = bed_temp_c
        self.drying_temp_c = drying_temp_c
        self.drying_time_hours = drying_time_hours
        self.manufacturing_date = manufacturing_date
        self.td = td

        if "CF" in self.modifiers:
            self.type += "-CF"
            self.modifiers.remove("CF")

        if "GF" in self.modifiers:
            self.type += "-GF"
            self.modifiers.remove("GF")

        if self.type not in VALID_BASE_MATERIALS:
            derived = _derive_material_type(self.type)
            if derived:
                logging.warning(
                    f"OpenRFID: non-standard filament type '{self.type}' "
                    f"normalised to '{derived}'"
                )
                self.type = derived
            else:
                raise ValueError(f"Invalid filament type: {self.type}")

    def pretty_text(self) -> str:
        modifiers = ' '.join(self.modifiers)

        if modifiers:
            modifiers += " "

        return "\n".join([
            f"{self.manufacturer} {self.type} {modifiers}Filament (processed by {self.source_processor}):",
            f"- Color (ARGB): {' '.join([f'#{color:06X}' for color in self.colors])}",
            f"- Diameter: {self.diameter_mm:.2f} mm",
            f"- Weight: {self.weight_grams} grams",
            f"- Hotend Temp: {self.hotend_min_temp_c:.1f}C - {self.hotend_max_temp_c:.1f}C",
            f"- Bed Temp: {self.bed_temp_c:.1f}C",
            f"- Drying: {self.drying_temp_c:.1f}C for {self.drying_time_hours:.1f} hours",
            f"- Manufactured on: {self.manufacturing_date}",
            f"- TD: {self.td:.1f} mm"
        ])

    @property
    def rgba(self) -> int:
        if not self.colors or len(self.colors) == 0:
            return 0x00000000  # Transparent if no color available

        argb = self.colors[0]
        return to_rgba(argb)

    def to_dict(self) -> dict:
        return {
            "source_processor": self.source_processor,
            "unique_id": self.unique_id,
            "manufacturer": self.manufacturer,
            "type": self.type,
            "modifiers": self.modifiers,
            "colors": self.colors,
            "rgba": self.rgba,
            "rgb": (self.rgba >> 8) & 0xFFFFFF,
            "alpha": self.rgba & 0xFF,
            "colors_rgba": [to_rgba(color) for color in self.colors],
            "colors_rgba_hex": [f"{to_rgba(color):08X}" for color in self.colors],
            "diameter_mm": self.diameter_mm,
            "weight_grams": self.weight_grams,
            "hotend_min_temp_c": self.hotend_min_temp_c,
            "hotend_max_temp_c": self.hotend_max_temp_c,
            "bed_temp_c": self.bed_temp_c,
            "drying_temp_c": self.drying_temp_c,
            "drying_time_hours": self.drying_time_hours,
            "manufacturing_date": self.manufacturing_date,
            "td": self.td
        }

    @staticmethod
    def generate_unique_id(*args) -> str:
        strings = "|".join([str(arg) for arg in args])
        hash = hashlib.sha256(strings.encode('utf-8')).hexdigest()
        return hash
