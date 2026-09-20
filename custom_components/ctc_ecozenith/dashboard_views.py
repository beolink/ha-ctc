"""Layout of the CTC EcoZenith page, built from a plain description of the heat pumps.

Free of Home Assistant imports on purpose, like stats_extra.py, so the layout can be
checked without an installation (tests/test_dashboard.py). dashboard.py looks the
entities up every time the page is opened and hands this module one dict per pump:

    {"name": str,                       # the device name as the user sees it
     "entities": {key: entity_id},      # enabled and visible entities only
     "names": {key: str},               # the registry name, or the original one
     "pages": [{"title": str,
                "values": [{"key": str, "label": str, "unit": str | None}]}],
     "energy_out": key | None,          # the display's lifetime counters, as found
     "energy_in": key | None,           # by cop.find_energy_totals
     "unused": [key],                   # readings this installation only ever gave as 0
     "control_enabled": bool,
     "display_interval": int | None,    # seconds between display harvests
     "language": "sv" | "en"}

A key is what follows "<domain>_<host>_" in the entity's unique_id: the Modbus keys in
const.py, the derived binary sensors, the control registers, the COP spans, the
identity rows and the display values ("p22_avgiven_varme"). A key that is missing
simply leaves its card out, so an i550 Pro without control gets no controls and a
unit without a history page gets no coefficient of performance.

Each pump gets four tabs, in the order the questions come:

  Overview     What the pump is doing right now: its status, the handful of controls
               worth reaching for, the key readings, and the circuit over a day.
  Controls     Everything writable, grouped by what it does to the house rather than
               by entity domain: heating, hot water, operation and power.
  Performance  How the pump has run: the graphs, and then every reading in its own
               section, which is what the page carried before the tabs.
  All values   The full list, every value the installation offers, with a filter and
               an explanation on each row.

What a page shows is decided by the installation, not by this module. The lists below
only say where a value goes if the installation has it: an entity exists or not, is
enabled or not, has a value or not, and a reading the installation has only ever
reported as zero is left out (seen.py), because CTC answers with a clean zero for
hardware that is not fitted and registers a model or software revision does not use.
Status, controls and stored settings are shown whatever they read, and the full list
shows everything whatever it reads, since that is what it is for.

Two kinds of card carry the values, both from the integration's own card script
(www/ctc-ecozenith-card.js) so every name can be explained, see explanations.py. The
few values that matter at a glance, and every control, are Home Assistant's own tiles
across the whole section, where a Swedish name has room to be read. The rest are rows,
full name on the left and the value on the right, which is what a long list of readings
needs. Both carry the blue "i" that opens the explanation.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .explanations import display_explanation, explain, source

URL_PATH = "ctc-ecozenith"
TITLE = "CTC EcoZenith"
ICON = "mdi:heat-pump"

#: The trend graph under a tile arrived in Home Assistant 2025.9.
TREND_GRAPH_SINCE = (2025, 9)
TREND_HOURS = 24
#: What the graphs cover: a day of readings, a month of daily figures.
GRAPH_HOURS = 24
GRAPH_DAYS = 30

#: A value in one of these states has nothing to show, and its card is hidden
#: until it has: a register that holds a sentinel, a display page that has not
#: been reached yet, a coefficient of performance with too little history.
HIDDEN_STATES = ["unavailable", "unknown"]

#: For an entity this layout does not know yet, from a newer integration.
UNKNOWN_EXPLANATION = "Ett värde från integrationen som sidan ännu inte har någon egen förklaring för."
UNKNOWN_SOURCE = "CTC EcoZenith"

TILE_CARD = "custom:ctc-ecozenith-tile"
ROWS_CARD = "custom:ctc-ecozenith-rows"

HEAT_POWER = "@heat_power"
SUPPLIED_POWER = "@supplied_power"
ENERGY_OUT = "@energy_out"
ENERGY_IN = "@energy_in"

#: Rows of the display matched on the label it printed, in English and in Swedish,
#: as the text catalogue has them (ids 1807 and 1810). The catalogue strips the
#: trailing unit from a row name, so "Avgiven värme (kW)" is stored as
#: "Avgiven värme" with the unit kW.
_POWER_LABELS = {
    HEAT_POWER: ("energy output", "avgiven värme"),
    SUPPLIED_POWER: ("energy add", "tillförd effekt"),
}
_PERIOD_MARKERS = ("/", "24", "30")
#: How a display value's key starts: the page number it was harvested from.
_DISPLAY_KEY = re.compile(r"p\d+_")

# The status of the unit, which stays on the page whatever it reads: the tiles
# first, then the rows. Its heading is the pump's own name.
_STATUS_TILES = ("system_status", "hp1_status")
_STATUS_ROWS = (
    "compressor_running", "defrosting", "immersion_active", "alarm",
    "blocked", "smartgrid_active", "hs1_status", "sg_mode",
)

#: The overview's key figures: how warm it is around the circuit, how hard the
#: compressor is working and what comes out of it.
_KEY_TILES = (
    "outdoor_temp", "room_temp_1", "dhw_temp", "hs1_flow", "hp1_rps",
    HEAT_POWER, "cop_day",
)
#: The controls the overview carries, in the order they matter to someone
#: standing in the house. The rest are one tab away.
_QUICK_CONTROLS = ("ctl_room_setpoint_1", "ctl_dhw_mode", "ctl_extra_dhw", "ctl_price_mode")

#: Everything writable, grouped by what it does to the house: (id, icon, keys).
_CONTROL_GROUPS = (
    ("heating", "mdi:radiator", ("ctl_room_setpoint_1", "ctl_zone_mode_1")),
    ("hot_water", "mdi:water-boiler", ("ctl_dhw_mode", "ctl_dhw_setpoint", "ctl_extra_dhw")),
    ("power", "mdi:flash",
     ("ctl_price_mode", "ctl_max_rps", "ctl_immersion_lower", "ctl_immersion_upper")),
    ("release", "mdi:hand-back-left-outline", ("release_control",)),
)

# The readings, in sections: (id, icon, tiles, rows).
_READINGS = (
    ("temperatures", "mdi:thermometer",
     ("outdoor_temp", "room_temp_1"),
     ("set_room_1", "hs1_flow", "hs1_flow_setpoint", "return_temp",
      "radiator_temp", "room_temp_2", "hp1_outdoor_temp")),
    ("hot_water", "mdi:water-boiler",
     ("dhw_temp",),
     ("dhw_capacity", "set_dhw_mode", "dhw_lower_temp", "dhw_stop_temp",
      "tank_lower_setpoint", "set_extra_dhw", "dhw_circulation", "dhw_temp_raw")),
    ("energy", "mdi:lightning-bolt",
     ("cop_day", "cop_year", "cop_first_year", "cop_lifetime",
      HEAT_POWER, SUPPLIED_POWER),
     (ENERGY_OUT, "compressor_kwh", ENERGY_IN, "immersion_kwh",
      "immersion_upper_kw", "immersion_lower_kw",
      "current_l1", "current_l2", "current_l3", "hp1_power")),
)
_TECHNICAL = (
    ("compressor", "mdi:heat-pump-outline",
     ("hp1_rps",),
     ("hp1_charge_pump", "hp1_brine_pump", "hp1_fan", "hp1_in", "hp1_out",
      "hp1_brine_in", "hp1_brine_out", "hp1_discharge", "hp1_suction",
      "hp1_high_pressure", "hp1_low_pressure", "compressor_hours",
      "compressor_hours_24h", "degree_minutes", "hp1_defrost_timer")),
    ("settings", "mdi:cog-outline",
     (),
     ("set_heating_mode_1", "set_hp1_blocked", "set_slope_1", "set_adjust_1",
      "set_max_rps_1", "set_max_immersion_lower", "set_max_immersion_upper")),
    ("unit", "mdi:information-outline",
     (),
     ("hp_model", "made", "serial", "display_fw", "hp_fw", "bootloader",
      "control_sw", "control_sw_year")),
)

#: The graphs, in the order performance shows them: (id, kind, keys). A graph
#: whose entities this installation does not have is left out, which is how a
#: unit without a history page ends up without a coefficient of performance.
_GRAPHS = (
    ("graph_temps", "history",
     ("outdoor_temp", "hs1_flow", "return_temp", "dhw_temp", "room_temp_1")),
    ("graph_compressor", "history", ("hp1_rps", "degree_minutes")),
    ("graph_energy", "change", (ENERGY_OUT, "compressor_kwh", "immersion_kwh")),
    ("graph_cop", "mean", ("cop_day",)),
)

#: The sections of readings, where a value only ever zero is left out.
_LEARNT_SECTIONS = frozenset({"temperatures", "hot_water", "energy", "compressor"})

#: Tiles that carry a trend graph where the frontend has one.
_TREND_KEYS = frozenset({"outdoor_temp", "room_temp_1", "dhw_temp", "hp1_rps"})
#: Number controls with a wide range get a slider; the rest step with buttons.
_SLIDER_KEYS = frozenset({"ctl_max_rps", "ctl_immersion_lower", "ctl_immersion_upper"})
#: How every control entity's name starts (const.CONTROL_NUMBERS and _SELECTS).
_CONTROL_PREFIX = "Styr "

#: The full list, group by group, so that every value the integration makes has
#: a place in it: (id, icon, keys). Display values follow, a group per page.
_VALUE_GROUPS = (
    ("status", ICON, _STATUS_TILES + _STATUS_ROWS),
    ("control", "mdi:tune-variant", tuple(k for _id, _icon, keys in _CONTROL_GROUPS for k in keys)),
    *((section, icon, tiles + rows) for section, icon, tiles, rows in _READINGS + _TECHNICAL),
)

#: Every key the layout knows of. What an installation has beyond this is from a
#: newer integration than the page, and lands under "Other".
_KNOWN_KEYS = frozenset(
    key
    for _id, _icon, keys in _VALUE_GROUPS
    for key in keys
    if not key.startswith("@")
)

TEXT = {
    "sv": {
        "overview": "Översikt",
        "controls": "Styrning",
        "performance": "Prestanda",
        "values": "Alla värden",
        "display": "Displayvärden",
        "status": "Drift",
        "quick": "Snabbstyrning",
        "readings": "Nyckeltal",
        "temperatures": "Temperaturer",
        "hot_water": "Varmvatten",
        "energy": "Energi och värmefaktor",
        "heating": "Värme",
        "power": "Drift och el",
        "release": "Släpp styrningen",
        "control": "Styrning",
        "compressor": "Kompressor och köldkrets",
        "settings": "Pumpens inställningar",
        "unit": "Om enheten",
        "other": "Övrigt",
        "graph_temps": "Temperaturer det senaste dygnet",
        "graph_compressor": "Kompressorn det senaste dygnet",
        "graph_energy": "Energi per dygn",
        "graph_cop": "Värmefaktor per dygn",
        "filter": "Sök bland värdena",
        "empty": "Inget värde matchar sökningen.",
        "control_note": (
            "Styrningen skrivs till CTC:s flyktiga register, och pumpen glömmer bort den "
            "ungefär fem minuter efter den sista skrivningen. Ett läge släpps med "
            "*Släpp styrningen*, och alla överstyrningar på en gång med knappen här."
        ),
        "control_off": (
            "Styrning är avstängd. Slå på **Tillåt styrning av värmepumpen** i "
            "[integrationens inställningar](/config/integrations/integration/ctc_ecozenith), "
            "så dyker reglagen upp här."
        ),
        "no_controls": (
            "Styrning är påslagen, men inga reglage är aktiverade. De ligger på "
            "[enhetens sida](/config/integrations/integration/ctc_ecozenith), under entiteter."
        ),
        "not_running": (
            "Ingen CTC-värmepump är igång just nu. Sidan fylls i av sig själv när "
            "integrationen har startat."
        ),
        "failed": "Sidan kunde inte byggas. Detaljerna står i Home Assistants logg.",
        "more_info": "Mer info",
        "explain": "Förklaring",
    },
    "en": {
        "overview": "Overview",
        "controls": "Controls",
        "performance": "Performance",
        "values": "All values",
        "display": "Display values",
        "status": "Operation",
        "quick": "Quick controls",
        "readings": "Key readings",
        "temperatures": "Temperatures",
        "hot_water": "Hot water",
        "energy": "Energy and COP",
        "heating": "Heating",
        "power": "Operation and power",
        "release": "Release control",
        "control": "Control",
        "compressor": "Compressor and refrigerant circuit",
        "settings": "Settings in the heat pump",
        "unit": "About the unit",
        "other": "Other",
        "graph_temps": "Temperatures over the last day",
        "graph_compressor": "The compressor over the last day",
        "graph_energy": "Energy per day",
        "graph_cop": "COP per day",
        "filter": "Search the values",
        "empty": "No value matches the search.",
        "control_note": (
            "Control is written to CTC's volatile registers, and the pump forgets it about "
            "five minutes after the last write. A mode is released with *Release control*, "
            "and every override at once with the button here."
        ),
        "control_off": (
            "Control is switched off. Turn on **Allow controlling the heat pump** in "
            "[the integration's settings](/config/integrations/integration/ctc_ecozenith) "
            "and the controls appear here."
        ),
        "no_controls": (
            "Control is switched on, but no controls are enabled. They are on "
            "[the device's page](/config/integrations/integration/ctc_ecozenith), "
            "under entities."
        ),
        "not_running": (
            "No CTC heat pump is running right now. The page fills in by itself once "
            "the integration has started."
        ),
        "failed": "The page could not be built. The details are in Home Assistant's log.",
        "more_info": "More info",
        "explain": "Explanation",
    },
}


def language(lang: str | None) -> str:
    """Swedish for any sv-* setting, English otherwise."""
    return "sv" if str(lang or "").lower().startswith("sv") else "en"


def _heading(text: str, icon: str) -> dict[str, Any]:
    return {"type": "heading", "heading": text, "heading_style": "title", "icon": icon}


def _shown_when_available(entity_id: str) -> dict[str, Any]:
    return {"condition": "state", "entity": entity_id, "state_not": list(HIDDEN_STATES)}


def _any_of(conditions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return conditions if len(conditions) == 1 else [{"condition": "or", "conditions": conditions}]


def _label_of(value: Mapping[str, Any]) -> str:
    return str(value.get("label") or "").strip()


def display_roles(pump: Mapping[str, Any]) -> dict[str, str]:
    """The display rows the overview picks out, by role, when the pump has them."""
    roles: dict[str, str] = {}
    for key, role in ((pump.get("energy_out"), ENERGY_OUT), (pump.get("energy_in"), ENERGY_IN)):
        if key:
            roles[role] = key
    for page in pump.get("pages") or []:
        for value in page.get("values") or []:
            label = _label_of(value).casefold()
            if str(value.get("unit") or "").casefold() != "kw":
                continue
            if any(marker in label for marker in _PERIOD_MARKERS):
                continue
            for role, names in _POWER_LABELS.items():
                if role not in roles and label.startswith(names):
                    roles[role] = value["key"]
    # Modbus 62341 is the same counter as the display's consumed energy, and it
    # is fresh every half minute rather than every half hour. One is enough.
    if ENERGY_IN in roles and "compressor_kwh" in (pump.get("entities") or {}):
        del roles[ENERGY_IN]
    return roles


def _display_labels(pump: Mapping[str, Any]) -> dict[str, str]:
    """What each display value is called on the page: its own row name on the panel.

    The entity is named "<page title>: <row>", and under a heading that already
    names the page the prefix only pushes the value aside. A name the user gave
    the entity is kept as it is.
    """
    names = pump.get("names") or {}
    labels: dict[str, str] = {}
    for page in pump.get("pages") or []:
        title = str(page.get("title") or "")
        for value in page.get("values") or []:
            key = value.get("key")
            label = _label_of(value)
            original = f"{title}: {label}" if title else label
            given = names.get(key)
            labels[key] = label if not given or given == original else str(given)
    return labels


class _Builder:
    """Cards for one tab of one pump, remembering which keys have found a place."""

    def __init__(
        self, pump: Mapping[str, Any], text: Mapping[str, str], ha_version: tuple[int, int]
    ) -> None:
        self.entities: Mapping[str, str] = pump.get("entities") or {}
        self.names: Mapping[str, str] = pump.get("names") or {}
        self.labels = _display_labels(pump)
        self.roles = display_roles(pump)
        self.trend = tuple(ha_version) >= TREND_GRAPH_SINCE
        self.text = text
        self.pages: list[Mapping[str, Any]] = list(pump.get("pages") or [])
        self.interval = pump.get("display_interval")
        self.rows_of_page = {
            value.get("key"): (value, page)
            for page in self.pages for value in page.get("values") or []
        }
        self.unused = set(pump.get("unused") or [])
        self.placed: set[str] = set()

    def explanation(self, key: str) -> dict[str, str]:
        """What a value is and where it comes from, for the card to show."""
        found: dict[str, str] = {}
        if key in self.rows_of_page:
            value, page = self.rows_of_page[key]
            found["explanation"] = display_explanation(value.get("label"), page.get("title"))
        else:
            found["explanation"] = explain(key) or UNKNOWN_EXPLANATION
        found["source"] = source(key, self.pages, self.interval) or UNKNOWN_SOURCE
        return found

    def entity_id(self, key: str) -> str | None:
        """The entity behind a key or a display role, without claiming it."""
        real = self.roles.get(key) if key.startswith("@") else key
        return self.entities.get(real) if real else None

    def _take(self, key: str, learnt: bool) -> tuple[str, str, str | None] | None:
        """(key, entity_id, name) for a key or display role that has an entity.

        With learnt, a reading this installation has only ever given as zero is
        taken as placed but left out, so it turns up nowhere else on the tab.
        """
        real = self.roles.get(key) if key.startswith("@") else key
        entity_id = self.entities.get(real) if real else None
        if not entity_id or real in self.placed:
            return None
        self.placed.add(real)
        if learnt and real in self.unused:
            return None
        name = self.labels.get(real) or self.names.get(real)
        return real, entity_id, str(name) if name else None

    @staticmethod
    def _under_a_control_heading(name: str) -> str:
        """The name without the "Styr" every control's name starts with.

        Under a heading that already says control the prefix says nothing, and
        with the control beside it, it takes the room the name needs:
        "Styr max elpatron nedre" and "... övre" both end in "Styr max elpatr…".
        """
        if not name.startswith(_CONTROL_PREFIX):
            return name
        rest = name[len(_CONTROL_PREFIX):]
        return rest[:1].upper() + rest[1:]

    def tile(self, key: str, hideable: bool, learnt: bool = False) -> dict[str, Any] | None:
        """Home Assistant's tile inside the card that explains it, or None."""
        taken = self._take(key, learnt)
        if taken is None:
            return None
        real, entity_id, name = taken
        tile: dict[str, Any] = {"type": "tile", "entity": entity_id}
        if name:
            # Set explicitly, so a tile reads "Utetemperatur" rather than the
            # device name followed by it.
            tile["name"] = name
        domain = entity_id.split(".", 1)[0]
        if domain in ("number", "select"):
            if name:
                tile["name"] = self._under_a_control_heading(name)
            if domain == "number":
                style = "slider" if real in _SLIDER_KEYS else "buttons"
                tile["features"] = [{"type": "numeric-input", "style": style}]
            else:
                tile["features"] = [{"type": "select-options"}]
            # Under the name rather than beside it: the corner above the control
            # is where the "i" that explains the value sits.
        elif domain == "button":
            press = {"action": "perform-action", "perform_action": "button.press",
                     "target": {"entity_id": entity_id}}
            tile["hide_state"] = True
            tile["tap_action"] = press
            tile["icon_tap_action"] = dict(press)
        elif self.trend and real in _TREND_KEYS:
            tile["features"] = [{"type": "trend-graph", "hours_to_show": TREND_HOURS}]
        explained = self.explanation(real)
        if domain != "button" and explained.get("explanation"):
            # Tapping the name explains the value; the icon still opens its dialog.
            # A button keeps its press, and is explained by the "i" and on hover.
            tile["tap_action"] = {"action": "none"}
            tile["icon_tap_action"] = {"action": "more-info"}
        card: dict[str, Any] = {
            "type": TILE_CARD,
            "tile": tile,
            **explained,
            "more_info": self.text["more_info"],
            "explain": self.text["explain"],
            "grid_options": {"columns": 12},
        }
        if hideable:
            card["visibility"] = [_shown_when_available(entity_id)]
        return card

    def row(
        self, key: str, hideable: bool, learnt: bool = False, trim: bool = False
    ) -> dict[str, Any] | None:
        """One line of a list card: name on the left, value on the right."""
        taken = self._take(key, learnt)
        if taken is None:
            return None
        real, entity_id, name = taken
        name = name or entity_id
        if trim:
            name = self._under_a_control_heading(name)
        row: dict[str, Any] = {"entity": entity_id, "name": name}
        row.update(self.explanation(real))
        if hideable:
            # The card hides the row itself while it has nothing to show.
            row["hide_unavailable"] = True
        return row

    def rows(self, keys: Iterable[str], hideable: bool, learnt: bool = False) -> dict[str, Any] | None:
        """A list card with a row per key that has an entity, or None."""
        rows = [row for row in (self.row(key, hideable, learnt) for key in keys) if row]
        if not rows:
            return None
        card = self.list_card(rows)
        if hideable:
            # A card whose every row is hidden would be an empty frame.
            card["visibility"] = _any_of([_shown_when_available(row["entity"]) for row in rows])
        return card

    def list_card(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """The list card itself, around rows that are already built."""
        return {
            "type": ROWS_CARD,
            "rows": rows,
            "more_info": self.text["more_info"],
            "explain": self.text["explain"],
            "grid_options": {"columns": 12},
        }

    def graph(self, graph_id: str, kind: str, keys: Iterable[str]) -> dict[str, Any] | None:
        """A graph of what this installation has of the keys, or None.

        Graphs read what the sections show, so they claim nothing: the same
        value is a tile on one tab and a line here.
        """
        entities = []
        for key in keys:
            entity_id = self.entity_id(key)
            real = self.roles.get(key, key)
            if not entity_id or real in self.unused:
                continue
            name = self.labels.get(real) or self.names.get(real)
            entities.append({"entity": entity_id, "name": str(name)} if name else entity_id)
        if not entities:
            return None
        # A graph stands in a section two columns wide, where half the width
        # would be half a graph.
        card: dict[str, Any] = {"grid_options": {"columns": "full"}}
        if kind == "history":
            card.update({"type": "history-graph", "hours_to_show": GRAPH_HOURS,
                         "entities": entities})
        else:
            # Long term statistics: a day at a time, which is what a counter or
            # a coefficient of performance is worth looking at over a month.
            card.update({
                "type": "statistics-graph",
                "chart_type": "bar" if kind == "change" else "line",
                "period": "day",
                "days_to_show": GRAPH_DAYS,
                "stat_types": [kind],
                "entities": [e["entity"] if isinstance(e, dict) else e for e in entities],
            })
        card["visibility"] = _any_of([
            _shown_when_available(e["entity"] if isinstance(e, dict) else e) for e in entities
        ])
        return card


def _section(
    heading: dict[str, Any], cards: Iterable[dict[str, Any] | None], **extra: Any
) -> dict[str, Any] | None:
    """A grid section, hidden as a whole while every card in it is hidden."""
    cards = [card for card in cards if card]
    if not cards:
        return None
    section: dict[str, Any] = {"type": "grid", "cards": [heading, *cards], **extra}
    if all(card.get("visibility") for card in cards):
        conditions = []
        for card in cards:
            conditions.extend(_conditions_in(card["visibility"]))
        section["visibility"] = _any_of(conditions)
    return section


def _conditions_in(visibility: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The state conditions of a card's visibility, unwrapping an "or"."""
    found: list[dict[str, Any]] = []
    for condition in visibility:
        if condition.get("condition") == "or":
            found.extend(condition.get("conditions") or [])
        else:
            found.append(condition)
    return found


def _note(content: str) -> dict[str, Any]:
    return {"type": "markdown", "content": content, "grid_options": {"columns": 12}}


def overview_sections(
    pump: Mapping[str, Any], text: Mapping[str, str], ha_version: tuple[int, int]
) -> list[dict[str, Any]]:
    """What the pump is doing right now: status, the near controls, key figures, a day."""
    build = _Builder(pump, text, ha_version)
    sections = [
        _section(
            _heading(str(pump.get("name") or TITLE), ICON),
            [*(build.tile(key, hideable=False) for key in _STATUS_TILES),
             build.rows(_STATUS_ROWS, hideable=False)],
        ),
        _section(
            _heading(text["quick"], "mdi:tune-variant"),
            [build.tile(key, hideable=False) for key in _QUICK_CONTROLS],
        ),
        _section(
            _heading(text["readings"], "mdi:gauge"),
            [build.tile(key, hideable=True, learnt=True) for key in _KEY_TILES],
        ),
    ]
    temps = next(graph for graph in _GRAPHS if graph[0] == "graph_temps")
    # Twice the width of a column of tiles: a day of five lines needs the room.
    sections.append(_section(
        _heading(text[temps[0]], "mdi:chart-line"), [build.graph(*temps)], column_span=2
    ))
    return [section for section in sections if section]


def controls_sections(
    pump: Mapping[str, Any], text: Mapping[str, str], ha_version: tuple[int, int]
) -> list[dict[str, Any]]:
    """Everything writable, grouped by what it does to the house."""
    build = _Builder(pump, text, ha_version)
    sections: list[dict[str, Any] | None] = []
    for group, icon, keys in _CONTROL_GROUPS:
        cards: list[dict[str, Any] | None] = [build.tile(key, hideable=False) for key in keys]
        if group == "release" and any(cards):
            cards.append(_note(text["control_note"]))
        sections.append(_section(_heading(text[group], icon), cards))
    found = [section for section in sections if section]
    if found:
        return found
    # Nothing writable: either control is off, or every control entity is.
    note = text["control_off"] if not pump.get("control_enabled") else text["no_controls"]
    return [_section(_heading(text["control"], "mdi:tune-variant"), [_note(note)])]


def performance_sections(
    pump: Mapping[str, Any], text: Mapping[str, str], ha_version: tuple[int, int]
) -> list[dict[str, Any]]:
    """How the pump has run: the graphs, then every reading in its own section."""
    build = _Builder(pump, text, ha_version)
    sections: list[dict[str, Any] | None] = [
        _section(_heading(text[graph_id], "mdi:chart-bar" if kind == "change" else "mdi:chart-line"),
                 [build.graph(graph_id, kind, keys)], column_span=2)
        for graph_id, kind, keys in _GRAPHS
    ]
    for section_id, icon, tiles, rows in _READINGS + _TECHNICAL:
        learnt = section_id in _LEARNT_SECTIONS
        sections.append(_section(
            _heading(text[section_id], icon),
            [*(build.tile(key, hideable=True, learnt=learnt) for key in tiles),
             build.rows(rows, hideable=True, learnt=learnt)],
        ))

    # What no list here knows of, from a newer integration than the page. A
    # display value whose page is no longer harvested stays in the entity
    # registry but never gets a value again, so it is left off altogether.
    leftovers = [
        key for key in build.entities
        if key not in _KNOWN_KEYS and not _DISPLAY_KEY.match(key)
    ]
    sections.append(_section(
        _heading(text["other"], "mdi:dots-horizontal"),
        [build.rows(leftovers, hideable=True, learnt=True)],
    ))
    return [section for section in sections if section]


def values_sections(
    pump: Mapping[str, Any], text: Mapping[str, str], ha_version: tuple[int, int]
) -> list[dict[str, Any]]:
    """Every value the installation offers, in one list with a filter over it.

    Nothing is left out here, whatever it reads and whatever it has only ever
    read: this is the tab you open to find out whether a value exists at all.
    """
    build = _Builder(pump, text, ha_version)
    rows: list[dict[str, Any]] = []

    def group(heading: str, keys: Iterable[str], trim: bool = False) -> None:
        found = [row for row in (build.row(key, False, trim=trim) for key in keys) if row]
        if found:
            rows.append({"heading": heading})
            rows.extend(found)

    for group_id, _icon, keys in _VALUE_GROUPS:
        group(text[group_id], keys, trim=group_id == "control")
    group(text["other"], [
        key for key in build.entities
        if key not in _KNOWN_KEYS and not _DISPLAY_KEY.match(key)
    ])
    for page in build.pages:
        group(str(page.get("title") or TITLE),
              [value.get("key") for value in page.get("values") or [] if value.get("key")])

    if not rows:
        return []
    card = build.list_card(rows)
    card["filter"] = text["filter"]
    card["empty"] = text["empty"]
    # The list has a section of its own, two columns wide, and fills it.
    card["grid_options"] = {"columns": "full"}
    section = _section(_heading(text["values"], "mdi:format-list-bulleted"), [card],
                       column_span=2)
    return [section] if section else []


def _sort(pumps: Iterable[Any]) -> list[Mapping[str, Any]]:
    wanted = [p for p in pumps or [] if isinstance(p, Mapping)]
    return sorted(wanted, key=lambda p: str(p.get("name") or "").casefold())


def build_dashboard(
    pumps: Iterable[Mapping[str, Any]],
    fallback_language: str | None,
    ha_version: tuple[int, int],
) -> dict[str, Any]:
    """The whole page: four tabs per heat pump, in the order the questions come."""
    pumps = _sort(pumps)
    lang = language(pumps[0].get("language") if pumps else fallback_language)
    text = TEXT[lang]
    if not pumps:
        return message_dashboard(text["not_running"], lang)

    several = len(pumps) > 1
    views: list[dict[str, Any]] = []
    for index, pump in enumerate(pumps, start=1):
        name = str(pump.get("name") or TITLE)
        suffix = f"-{index}" if several else ""
        tabs = [
            ("overview", overview_sections(pump, text, ha_version)),
            ("controls", controls_sections(pump, text, ha_version)),
            ("performance", performance_sections(pump, text, ha_version)),
            ("values", values_sections(pump, text, ha_version)),
        ]
        for tab, sections in tabs:
            if not sections:
                continue
            views.append({
                "title": f"{name}: {text[tab].lower()}" if several else text[tab],
                "path": f"{tab}{suffix}",
                "type": "sections",
                "max_columns": 4,
                "sections": sections,
            })
    return {"title": TITLE, "views": views}


def message_dashboard(message: str, lang: str | None) -> dict[str, Any]:
    """A page with nothing but a note on it, for when there is nothing else to show."""
    text = TEXT[language(lang)]
    return {
        "title": TITLE,
        "views": [{
            "title": text["overview"],
            "path": "overview",
            "type": "sections",
            "sections": [{
                "type": "grid",
                "cards": [_heading(TITLE, ICON), _note(message)],
            }],
        }],
    }


def entity_ids(config: Mapping[str, Any]) -> set[str]:
    """Every entity a built page refers to, looking inside sections, rows and conditions."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            entity = node.get("entity")
            if isinstance(entity, str):
                found.add(entity)
            # A graph names its entities in a list, and a statistics graph
            # names them as plain strings.
            for item in node.get("entities") or []:
                if isinstance(item, str):
                    found.add(item)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(config)
    return found
