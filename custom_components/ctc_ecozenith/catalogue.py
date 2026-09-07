"""Discover which pages and values the connected display actually offers.

Screen and text numbering differs between models, an i255 and an i550 Pro do not
agree on either, so nothing here is hard coded. The catalogue is built by
reading the unit's own screen map, screen definitions and text catalogue.

Values are paired with labels geometrically: a value's name is the nearest label
to its left on the same row, falling back to the nearest label above it. That
mirrors how the panel itself is laid out.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .const import SENTINELS, SlowPage, SlowValue
from .web_api import CtcWebClient, CtcWebError, Widget

_LOGGER = logging.getLogger(__name__)

# Units that appear inside the display's own format strings.
_UNIT_PATTERN = re.compile(r"(kWh|l/min|ppm|°C|kW|rps|bar|min|%|A|V|h)")
_CONVERSION = re.compile(r"%[.\-0-9lu]*[dfsu]")

_ROW_TOLERANCE = 14
_FLOW_X = -10000


def _decimals(fmt: str) -> float:
    """Return the scale implied by the display's format string."""
    if ".1f" in fmt:
        return 0.1
    if ".-1f" in fmt:
        return 0.1
    if ".2f" in fmt:
        return 0.01
    return 1.0


_LABEL_UNIT = re.compile(r"[( ](kWh|l/min|ppm|°C|kW|rps|bar|min|%|A|V|h)\)?\s*$")


def _unit(fmt: str, label: str = "") -> str | None:
    """Extract the unit from the format string, or failing that the label.

    CTC writes the unit inside the format string on some rows, for example
    ``%.-1frps``, and inside the row's name on others, for example
    ``Avgiven värme (kW)``.
    """
    stripped = _CONVERSION.sub(" ", fmt).replace("%%", " % ")
    match = _UNIT_PATTERN.search(stripped)
    if match:
        return match.group(1)
    match = _LABEL_UNIT.search(label.strip())
    return match.group(1) if match else None


def _clean_label(label: str) -> str:
    """Drop a trailing unit from a row name so it reads well as an entity name."""
    return _LABEL_UNIT.sub("", label.strip()).strip(" ()") or label.strip()


def numeric_value(value: SlowValue, raw: list[Any]) -> float | None:
    """Turn a raw variable into a number, honouring CTC's missing markers."""
    if not value.var_indices:
        return None
    index = value.var_indices[0]
    if index >= len(raw):
        return None
    item = raw[index]
    if not isinstance(item, int) or item in SENTINELS:
        return None
    return round(item * value.scale, 3)


def _usable_label(widget: Widget) -> bool:
    text = (widget.label or "").strip()
    if not text or len(text) < 2:
        return False
    # Alarm and info catalogue entries are rendered into status fields; they are
    # never the name of a neighbouring reading.
    if text.startswith("[E") or text.startswith("[I") or text == "* Demo *":
        return False
    return True


def has_conversion(fmt: str) -> bool:
    """True when a format string actually renders a number.

    Entries such as ``" / "`` and ``" , "`` are separators between two readings
    on the same row, not readings of their own.
    """
    return bool(_CONVERSION.search(fmt))


def _row_of(widgets: list[Widget]) -> dict[int, int]:
    """Group widgets into rows.

    Operation data pages are drawn row by row, and each row starts with its name
    in the left hand column. Rows scrolled out of view are all parked at the same
    negative y, so y cannot separate them; draw order and the left column can.
    A widget placed at a large negative x is laid out after the previous one and
    therefore continues the same row.
    """
    positioned = [w for w in widgets if w.x > _FLOW_X]
    if not positioned:
        return {w.index: 0 for w in widgets}
    label_column = min(w.x for w in positioned)

    rows: dict[int, int] = {}
    row = -1
    started = False
    for widget in sorted(widgets, key=lambda w: w.index):
        starts_row = widget.x > _FLOW_X and abs(widget.x - label_column) <= 2
        if starts_row or not started:
            row += 1
            started = True
        rows[widget.index] = row
    return rows


def _pair_labels(widgets: list[Widget]) -> dict[int, str]:
    """Name each reading after the label that starts its row.

    Operation data pages are two columns: the name sits at the left edge and the
    reading, sometimes several of them, to its right. Rows scrolled out of view
    keep their values, so they are kept as well.
    """
    rows = _row_of(widgets)
    labels_by_row: dict[int, list[Widget]] = {}
    for widget in widgets:
        if widget.visible and _usable_label(widget) and widget.width > 0:
            labels_by_row.setdefault(rows[widget.index], []).append(widget)

    values_by_row: dict[int, list[Widget]] = {}
    for widget in sorted(widgets, key=lambda w: w.index):
        if widget.visible and widget.value_fmt and has_conversion(widget.value_fmt):
            values_by_row.setdefault(rows[widget.index], []).append(widget)

    ordered_rows = sorted(labels_by_row)
    pairing: dict[int, str] = {}
    for row, values in values_by_row.items():
        candidates = labels_by_row.get(row)
        if candidates:
            name = (min(candidates, key=lambda w: w.x).label or "").strip()
        else:
            # No label on this row: borrow the closest row above that has one.
            above = [r for r in ordered_rows if r < row]
            name = (
                (min(labels_by_row[above[-1]], key=lambda w: w.x).label or "").strip()
                if above
                else ""
            )
        for position, widget in enumerate(values, start=1):
            pairing[widget.index] = (
                name if len(values) == 1 else f"{name} {position}"
            ).strip()
    return pairing


def _slug(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower().replace("å", "a").replace("ä", "a").replace("ö", "o"))
    cleaned = cleaned.strip("_")
    return cleaned or fallback


async def async_page_title(client: CtcWebClient, screens: list[int]) -> str:
    """Return a human title for a page, taken from its first real label."""
    for screen in screens:
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            continue
        for widget in widgets:
            if not widget.visible or not widget.label:
                continue
            text = widget.label.strip()
            if not text or text.startswith("[") or text == "* Demo *":
                continue
            if len(text) < 3:
                continue
            return text
    return f"Sida {screens[0] if screens else '?'}"


async def async_page_values(
    client: CtcWebClient, page: int, screens: list[int]
) -> list[SlowValue]:
    """Describe every formatted value on a page."""
    found: list[SlowValue] = []
    seen: set[str] = set()
    for screen in screens:
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError as err:
            _LOGGER.debug("Skipping screen %s: %s", screen, err)
            continue
        pairing = _pair_labels(widgets)
        for widget in widgets:
            if widget.value_fmt is None or not widget.value_vars:
                continue
            if not widget.visible or not has_conversion(widget.value_fmt):
                continue
            raw_label = (pairing.get(widget.index) or f"Värde {widget.index}").strip().rstrip(":")
            unit = _unit(widget.value_fmt, raw_label)
            label = _clean_label(raw_label)
            base = _slug(label, f"s{screen}_w{widget.index}")
            key = f"p{page}_{base}"
            suffix = 2
            while key in seen:
                key = f"p{page}_{base}_{suffix}"
                suffix += 1
            seen.add(key)
            found.append(
                SlowValue(
                    key=key,
                    label=label,
                    page=page,
                    screen=screen,
                    fmt=widget.value_fmt,
                    var_indices=list(widget.value_vars),
                    unit=unit,
                    scale=_decimals(widget.value_fmt),
                )
            )
    return found


async def async_discover_pages(client: CtcWebClient) -> list[SlowPage]:
    """Walk the operation data subtree and describe every page it contains.

    The panel is moved while this runs and put back where it started. Only the
    operation data subtree is entered, which is read only, so no setting can be
    changed by a tap that lands slightly off.
    """
    page_map = await client.async_screen_map(refresh=True)
    origin = await client.async_current_page()
    discovered: list[SlowPage] = []
    visited: set[int] = set()

    try:
        root = await _async_operation_root(client, page_map, origin)
        if root is None:
            _LOGGER.warning("Could not find the operation data menu; offering the current page only")
            root = await client.async_current_page()

        await _async_collect(client, page_map, root, discovered, visited)

        tabs = await _async_tab_positions(client, page_map, root)
        for x, y in tabs:
            here = await client.async_current_page()
            screens = page_map.get(here, [])
            await client.async_click(screens, x, y)
            landed = await client.async_current_page()
            if landed not in visited:
                await _async_collect(client, page_map, landed, discovered, visited)
            if landed != root:
                await client.async_click(page_map.get(landed, []), 440, 23)
    finally:
        await _async_restore(client, page_map, origin)

    return [page for page in discovered if page.values]


async def _async_collect(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    page: int,
    into: list[SlowPage],
    visited: set[int],
) -> None:
    if page in visited:
        return
    visited.add(page)
    screens = page_map.get(page, [])
    if not screens:
        return
    title = await async_page_title(client, screens)
    values = await async_page_values(client, page, screens)
    into.append(SlowPage(page=page, title=title, screens=list(screens), values=values))


async def _async_operation_root(
    client: CtcWebClient, page_map: dict[int, list[int]], origin: int
) -> int | None:
    """Return the page id of the operation data menu, navigating there."""
    from .const import OPERATION_DATA_LABEL_EN

    for _ in range(4):
        here = await client.async_current_page()
        screens = page_map.get(here, [])
        for screen in screens:
            try:
                widgets = await client.async_widgets(screen)
            except CtcWebError:
                continue
            for widget in widgets:
                if not widget.visible or widget.label is None or widget.width <= 0:
                    continue
                english = await client.async_english_label(screen, widget)
                if english == OPERATION_DATA_LABEL_EN:
                    x, y = widget.centre
                    await client.async_click(screens, x, y)
                    landed = await client.async_current_page()
                    if landed != here:
                        return landed
        # Step back towards the home screen and try again.
        before = await client.async_current_page()
        await client.async_click(screens, 440, 23)
        if await client.async_current_page() == before:
            return None
    return None


async def _async_tab_positions(
    client: CtcWebClient, page_map: dict[int, list[int]], page: int
) -> list[tuple[int, int]]:
    screens = page_map.get(page, [])
    for screen in screens:
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            continue
        row = [
            w
            for w in widgets
            if w.visible and w.y > 200 and w.width > 20 and w.height > 10
        ]
        if len(row) >= 3 and len({w.width for w in row}) <= 2:
            return [w.centre for w in sorted(row, key=lambda w: w.x)]
    return []


async def _async_restore(
    client: CtcWebClient, page_map: dict[int, list[int]], origin: int
) -> None:
    """Put the panel back on the page it was showing before we started."""
    for _ in range(6):
        here = await client.async_current_page()
        if here == origin:
            return
        before = here
        await client.async_click(page_map.get(here, []), 440, 23)
        if await client.async_current_page() == before:
            break
    try:
        await client.async_goto_page(origin)
    except CtcWebError:
        _LOGGER.debug("Could not restore the panel to page %s", origin)


def pages_to_storage(pages: list[SlowPage]) -> list[dict[str, Any]]:
    """Serialise the catalogue so it survives a restart without rescanning."""
    return [
        {
            "page": page.page,
            "title": page.title,
            "screens": list(page.screens),
            "values": [
                {
                    "key": value.key,
                    "label": value.label,
                    "screen": value.screen,
                    "fmt": value.fmt,
                    "vars": list(value.var_indices),
                    "unit": value.unit,
                    "scale": value.scale,
                }
                for value in page.values
            ],
        }
        for page in pages
    ]


def pages_from_storage(stored: list[dict[str, Any]] | None) -> list[SlowPage]:
    """Rebuild the catalogue saved by :func:`pages_to_storage`."""
    pages: list[SlowPage] = []
    for item in stored or []:
        try:
            page = SlowPage(
                page=int(item["page"]),
                title=str(item.get("title", "")),
                screens=[int(s) for s in item.get("screens", [])],
            )
            for raw in item.get("values", []):
                page.values.append(
                    SlowValue(
                        key=str(raw["key"]),
                        label=str(raw.get("label", raw["key"])),
                        page=page.page,
                        screen=int(raw["screen"]),
                        fmt=str(raw.get("fmt", "%d")),
                        var_indices=[int(i) for i in raw.get("vars", [])],
                        unit=raw.get("unit"),
                        scale=float(raw.get("scale", 1.0)),
                    )
                )
            pages.append(page)
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("Discarding a stored page: %s", err)
    return pages
