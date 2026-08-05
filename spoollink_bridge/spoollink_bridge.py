"""
SpoolLink Bridge — Moonraker Component
=======================================
Bridges OpenRFID webhook events to Spoolman filament tracking on the Snapmaker U1.

When an NFC/RFID tag is scanned by OpenRFID, this component:
  1. Receives the webhook (channel + card UID)
  2. Looks up the matching spool in Spoolman (via extra.card_uids or lot_nr)
  3. Calls SET_ACTIVE_SPOOL to update Klipper + Spoolman tracking

Clearing a channel's spool assignment is handled separately from RFID: a
background poll watches Klipper's `filament_feed` object (the U1's own
mechanical filament-presence sensor) and clears the assignment only on a
live True -> False transition, i.e. an observed physical unload. RFID
events never clear an assignment, only ever set one on a successful read.

This split exists because "no tag" is fundamentally ambiguous at the RFID
layer alone: it cannot tell a genuinely untagged/unregistered spool apart
from a transient read failure on a spool that is still physically loaded
(e.g. a misaligned tag during a boot rescan). Clearing on tag_not_present
(an earlier version of this component did exactly that) caused spurious
un-assignments whenever a boot-time rescan happened to miss a read — see
the project history for the incidents that led to this design. The feed
sensor has no such ambiguity: it only reports physical presence.

UID Registration (two supported formats):
  - extra.card_uids  : preferred (SpoolLink-native, comma-separated UIDs)
  - lot_nr           : SpoolKid app format ("card_uid:UID[,card_uid:UID2]")

Forward-compatible with PAXX SpoolLink (PR #491 / v1.5.x):
  Once the native SpoolLink firmware lands, migrate UIDs from lot_nr → extra.card_uids
  and retire this component.

Installation: see README.md
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..common import RequestType

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .http_client import HttpClient
    from .klippy_apis import KlippyAPI as APIComp
    from ..common import WebRequest

log = logging.getLogger(__name__)

# How often the physical feed state is polled to detect a genuine filament
# unload (independent of RFID). A spool change takes several seconds in
# practice (remove, load new spool, feed filament) — no need to poll faster.
FEED_POLL_INTERVAL_SEC = 4.0

# Channel -> (feed object name, extruder key), matches the U1's 4 toolheads.
_FEED_OBJECTS = {
    0: ("filament_feed left", "extruder0"),
    1: ("filament_feed left", "extruder1"),
    2: ("filament_feed right", "extruder2"),
    3: ("filament_feed right", "extruder3"),
}


class SpoolinkBridge:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self._spoolman_url = config.get(
            "spoolman_url", "http://127.0.0.1:7912"
        ).rstrip("/")
        self.http_client: HttpClient = self.server.lookup_component("http_client")
        self.klippy_apis: APIComp = self.server.lookup_component("klippy_apis")

        self.server.register_endpoint(
            "/server/spoollink_bridge",
            RequestType.POST,
            self._handle_webhook,
        )

        self._last_detected: Dict[int, bool] = {}
        eventloop = self.server.get_event_loop()
        self._feed_poll_timer = eventloop.register_timer(self._check_feed_state)
        self.server.register_event_handler(
            "server:klippy_ready", self._handle_klippy_ready
        )

        log.info("SpoolLink Bridge: endpoint /server/spoollink_bridge registered")

    async def component_init(self) -> None:
        await self._ensure_card_uids_field()

    async def _handle_klippy_ready(self) -> None:
        # Start with an empty baseline on every (re)connect so a channel
        # that is already empty at startup is never mistaken for one that
        # was "just" unloaded. Only a transition observed while the poller
        # is running triggers a clear.
        self._last_detected.clear()
        self._feed_poll_timer.start(delay=FEED_POLL_INTERVAL_SEC)
        log.info(
            "SpoolLink Bridge: feed-state polling started (every %.1fs)",
            FEED_POLL_INTERVAL_SEC,
        )

    async def _check_feed_state(self, eventtime: float) -> float:
        try:
            status = await self.klippy_apis.query_objects(
                {"filament_feed left": None, "filament_feed right": None}
            )
        except Exception as e:
            log.error("SpoolLink Bridge: feed poll failed: %s", e)
            return eventtime + FEED_POLL_INTERVAL_SEC

        for channel, (feed_obj, extruder_key) in _FEED_OBJECTS.items():
            e_status = status.get(feed_obj, {}).get(extruder_key, {})
            detected = bool(e_status.get("filament_detected", False))
            prev = self._last_detected.get(channel)
            if prev is True and detected is False:
                log.info(
                    "ch%d: filament physically removed (feed module), "
                    "clearing spool assignment", channel
                )
                await self._clear_spool(channel)
            self._last_detected[channel] = detected

        return eventtime + FEED_POLL_INTERVAL_SEC

    async def _handle_webhook(self, web_request: WebRequest) -> Dict[str, Any]:
        """Receive OpenRFID webhook: {channel: int, card_uid: str}

        Only fires on a successful tag_read (see openrfid_webhook_addition.cfg
        — `event = tag_read`, not `tag_not_present`). A missing or unmatched
        UID here means nothing: it is never used to clear an assignment,
        because a failed/missing read can't be told apart from genuinely
        untagged filament at this layer. See module docstring.
        """
        channel: int = web_request.get_int("channel")
        card_uid: str = web_request.get_str("card_uid").strip().upper()
        if not card_uid:
            raise self.server.error("Missing card_uid", 400)
        log.info("ch%d: card UID %s", channel, card_uid)
        spool = await self._find_spool(card_uid)
        if spool:
            await self._activate_spool(channel, spool)
        else:
            log.warning(
                "ch%d: no spool found for UID %s — register via Spoolman "
                "extra.card_uids or SpoolKid app", channel, card_uid
            )
        return {"spool_id": spool["id"] if spool else None}

    async def _clear_spool(self, channel: int) -> None:
        """Clear the channel's persisted spool assignment."""
        script = (
            f"SET_GCODE_VARIABLE MACRO=T{channel} VARIABLE=spool_id VALUE=None\n"
            f"SAVE_CURRENT_SPOOLS"
        )
        try:
            await self.klippy_apis.run_gcode(script)
            log.info("ch%d: spool assignment cleared", channel)
        except Exception as e:
            log.error("ch%d: gcode failed: %s", channel, e)

    async def _find_spool(self, card_uid: str) -> Optional[dict]:
        """Look up spool by UID. Checks extra.card_uids first, then lot_nr."""
        uid = card_uid.upper()
        try:
            resp = await self.http_client.get(
                f"{self._spoolman_url}/api/v1/spool?limit=1000",
                enable_cache=False,
            )
            if resp.status_code != 200:
                log.error("Spoolman HTTP %d", resp.status_code)
                return None
            spools = resp.json()
        except Exception as e:
            log.error("Spoolman unreachable: %s", e)
            return None

        # 1. extra.card_uids (SpoolLink-native, preferred)
        #    Format: "UID1" or "UID1,UID2" (stored with surrounding quotes by Spoolman)
        for spool in spools:
            raw = (spool.get("extra") or {}).get("card_uids") or ""
            uids = [
                u.strip().upper()
                for u in raw.strip().strip('"').split(",")
                if u.strip()
            ]
            if uid in uids:
                log.info("Match via extra.card_uids -> spool #%s", spool["id"])
                return spool

        # 2. lot_nr (SpoolKid app format: "card_uid:UID[,card_uid:UID2]")
        for spool in spools:
            for part in (spool.get("lot_nr") or "").split(","):
                part = part.strip()
                if (
                    part.startswith("card_uid:")
                    and part[len("card_uid:"):].strip().upper() == uid
                ):
                    log.info("Match via lot_nr -> spool #%s", spool["id"])
                    return spool

        return None

    async def _activate_spool(self, channel: int, spool: dict) -> None:
        """Update Klipper variable + Spoolman tracking for the matched spool."""
        spool_id = spool["id"]
        script = (
            f"SET_GCODE_VARIABLE MACRO=T{channel} VARIABLE=spool_id VALUE={spool_id}\n"
            f"SAVE_CURRENT_SPOOLS\n"
            f"SET_ACTIVE_SPOOL ID={spool_id}"
        )
        try:
            await self.klippy_apis.run_gcode(script)
            log.info("ch%d: spool #%d activated", channel, spool_id)
        except Exception as e:
            log.error("ch%d: gcode failed: %s", channel, e)

    async def _ensure_card_uids_field(self) -> None:
        """Create the extra.card_uids custom field in Spoolman if it doesn't exist."""
        try:
            resp = await self.http_client.get(
                f"{self._spoolman_url}/api/v1/field/spool",
                enable_cache=False,
            )
            if resp.status_code != 200:
                return
            if any(f.get("key") == "card_uids" for f in resp.json()):
                log.info("SpoolLink Bridge: extra.card_uids field already exists")
                return
            body = {
                "name": "Card UIDs",
                "field_type": "text",
                "order": 1,
                "default_value": json.dumps(""),
            }
            await self.http_client.post(
                f"{self._spoolman_url}/api/v1/field/spool/card_uids", body=body
            )
            log.info("SpoolLink Bridge: created extra.card_uids field in Spoolman")
        except Exception as e:
            log.warning("Could not ensure card_uids field: %s", e)


def load_component(config: ConfigHelper) -> SpoolinkBridge:
    return SpoolinkBridge(config)
