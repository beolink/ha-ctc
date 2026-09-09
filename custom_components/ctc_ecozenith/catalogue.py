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
from .web_api import CtcWebClient, CtcWebError, Widget, tap_target

_LOGGER = logging.getLogger(__name__)

# Units that appear inside the display's own format strings.
_UNIT_PATTERN = re.compile(r"(kWh|l/min|ppm|°C|kW|rps|bar|min|%|A|V|h)")
_CONVERSION = re.compile(r"%[.\-0-9lu]*[dfsu]")

_ROW_TOLERANCE = 14
_FLOW_X = -10000
MAX_READINGS_PER_ROW = 4
#: Anything this high up and this wide is the page heading, not a row name.
_HEADER_HEIGHT = 45


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


_POSITION_SUFFIX = re.compile(r"\s+\d+$")


def _base_label(label: str) -> str:
    """Drop the positional suffix added when a row carries several readings."""
    return _POSITION_SUFFIX.sub("", label.strip())


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
    match = _LABEL_UNIT.search(_base_label(label))
    return match.group(1) if match else None


def _clean_label(label: str) -> str:
    """Drop a trailing unit from a row name so it reads well as an entity name.

    The positional suffix is kept, since it is what tells "in" from "out" on a
    row that carries two readings.
    """
    suffix = _POSITION_SUFFIX.search(label.strip())
    base = _base_label(label)
    cleaned = _LABEL_UNIT.sub("", base).strip(" ()") or base
    return f"{cleaned}{suffix.group(0)}" if suffix else cleaned


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


def _is_caption(widget: Widget) -> bool:
    """True when a widget is a text element, not an icon.

    Icons resolve to whatever entry their selector lands on, which on a
    schematic page is often an unrelated string from a long list such as the
    language names. Only text elements are trusted to name a reading.
    """
    return widget.kind in (2, 3)


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

    # The column is where the row names are, which is not the same as the
    # leftmost thing on the screen: a divider or a rule drawn at x=0 would
    # otherwise become the column, and then no row would ever break. The most
    # common left edge among the captions is what the layout is actually built
    # on, so that is used, falling back to the leftmost element.
    columns: dict[int, int] = {}
    for widget in positioned:
        if _is_caption(widget) and _usable_label(widget):
            columns[widget.x] = columns.get(widget.x, 0) + 1
    if columns:
        label_column = min(columns, key=lambda x: (-columns[x], x))
    else:
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
    """Name each reading after the caption that belongs to it.

    Two layouts have to work. Most pages draw a row at a time, caption on the
    left and reading to its right, which geometry solves. Some pages draw every
    caption first and then every reading, and rows scrolled out of view all
    share one off screen y, so geometry has nothing to go on there. What is left
    then is the order they are drawn in, where the two blocks run in step.
    """
    captions = [
        w for w in widgets if w.visible and _is_caption(w) and _usable_label(w) and w.width > 0
    ]
    values = [
        w
        for w in widgets
        if w.visible and w.value_fmt and has_conversion(w.value_fmt)
    ]
    if not values:
        return {}

    rows = _row_of(widgets)
    pairing: dict[int, str] = {}
    used: set[int] = set()

    # Geometry first: a caption on the same row, to the left of the reading.
    by_row: dict[int, list[Widget]] = {}
    for caption in captions:
        by_row.setdefault(rows[caption.index], []).append(caption)
    values_by_row: dict[int, list[Widget]] = {}
    for value in sorted(values, key=lambda w: w.index):
        values_by_row.setdefault(rows[value.index], []).append(value)

    for row, members in values_by_row.items():
        candidates = by_row.get(row)
        if not candidates:
            continue
        # A genuine row holds a handful of readings at most: the widest seen is
        # "Överhettning S/H" with four. More than that means this is not a row
        # but a block of readings that ran past its caption, and guessing would
        # give several unrelated numbers the same name.
        if len(members) > MAX_READINGS_PER_ROW:
            continue
        name = (min(candidates, key=lambda w: w.x).label or "").strip()
        if not name:
            continue
        used.add(min(candidates, key=lambda w: w.x).index)
        for position, value in enumerate(members, start=1):
            pairing[value.index] = (
                name if len(members) == 1 else f"{name} {position}"
            ).strip()

    # Whatever is left is a caption block followed by a reading block. The two
    # run in the same order, so they are matched off against each other. The
    # page heading sits across the top and is not part of the block; counting it
    # in would shift every name by one.
    spare = [
        c
        for c in sorted(captions, key=lambda w: w.index)
        if c.index not in used and not (0 <= c.y < _HEADER_HEIGHT and c.width >= 80)
    ]
    orphans = [v for v in sorted(values, key=lambda w: w.index) if v.index not in pairing]
    for value, caption in zip(orphans, spare):
        name = (caption.label or "").strip()
        if name:
            pairing[value.index] = name
    return pairing


def _slug(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower().replace("å", "a").replace("ä", "a").replace("ö", "o"))
    cleaned = cleaned.strip("_")
    return cleaned or fallback


async def async_page_title(client: CtcWebClient, screens: list[int]) -> str:
    """Return a human title for a page.

    Operation data pages carry their heading as a wide text across the top. The
    chrome screens have only buttons, and icons resolve to whatever entry their
    selector lands on, so a title candidate has to be both near the top and wide
    enough not to be an icon.
    """
    candidates: list[Widget] = []
    for screen in screens:
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            continue
        candidates.extend(
            w
            for w in widgets
            if w.visible
            and _usable_label(w)
            and 0 <= w.y < 45
            and w.width >= 80
        )
    if candidates:
        best = max(candidates, key=lambda w: w.width)
        text = (best.label or "").strip()
        if len(text) >= 3:
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

    The panel moves while this runs and is put back where it started. Only the
    operation data subtree is entered. That subtree is read only on every CTC
    model checked, so a tap landing slightly off cannot change a setting.

    Layout differs between models: an i255 puts a tab strip along the bottom, an
    i550 Pro does not. Rather than guess, every plausible control on the root
    page is tried once and the tap that reached each page is recorded, so poll
    time can replay a known route instead of deriving one again.
    """
    page_map = await client.async_screen_map(refresh=True)
    origin = await client.async_current_page()
    discovered: list[SlowPage] = []
    visited: set[int] = set()

    try:
        root = await _async_operation_root(client, page_map, origin)
        if root is None:
            _LOGGER.warning(
                "Could not find the operation data menu; offering the current page only"
            )
            root = await client.async_current_page()
        await _async_collect(client, page_map, root, discovered, visited, [])
        await _async_explore(client, page_map, root, discovered, visited)
    finally:
        await _async_restore(client, page_map, origin)

    return [page for page in discovered if page.values]


async def _async_explore(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    root: int,
    into: list[SlowPage],
    visited: set[int],
    max_taps: int = 26,
) -> None:
    """Tap what looks like a control, note where it leads, and go one deeper.

    One level is not always enough. An i255 puts every operation data page one
    tap from the root, an i550 Pro hides the heat pump's own page behind a
    second tab strip, and that page is the one carrying the model, the control
    board's firmware and the delivered heat.
    """
    taps = await _async_tap_pages(client, page_map, root, root, into, visited, max_taps)

    # Second level: pages found above that carry a strip of their own.
    for page in [p.page for p in list(into) if p.page != root]:
        if taps >= max_taps:
            break
        if not await _async_return_to_root(client, page_map, root):
            break
        before = await client.async_current_page()
        route = next((p.route for p in into if p.page == page), [])
        for x, y in route:
            await client.async_click(page_map.get(await client.async_current_page(), []), x, y)
        if await client.async_current_page() != page:
            continue
        taps += await _async_tap_pages(
            client, page_map, page, root, into, visited, max_taps - taps, route
        )
    await _async_return_to_root(client, page_map, root)


async def _async_tap_pages(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    page: int,
    root: int,
    into: list[SlowPage],
    visited: set[int],
    budget: int,
    prefix: list[tuple[int, int]] | None = None,
) -> int:
    """Tap every target on one page, recording where each tap led."""
    targets = await _async_tap_targets(client, page_map, page)
    taps = 0
    for x, y in targets:
        if taps >= budget:
            break
        here = await client.async_current_page()
        if here != page:
            # Getting back may fail on one target without the rest being lost,
            # so this carries on rather than giving up on the whole page.
            if not await _async_return_to(client, page_map, root, page, prefix):
                continue
        taps += 1
        await client.async_click(page_map.get(page, []), x, y)
        landed = await client.async_current_page()
        if landed == page or landed in visited:
            continue
        await _async_collect(
            client, page_map, landed, into, visited, list(prefix or []) + [(x, y)]
        )
    return taps


async def _async_return_to(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    root: int,
    page: int,
    prefix: list[tuple[int, int]] | None,
) -> bool:
    """Walk back to a page, through the root when there is a route to replay."""
    if page == root:
        return await _async_return_to_root(client, page_map, root)
    if not await _async_return_to_root(client, page_map, root):
        return False
    for x, y in prefix or []:
        await client.async_click(page_map.get(await client.async_current_page(), []), x, y)
    return await client.async_current_page() == page


async def _async_return_to_root(
    client: CtcWebClient, page_map: dict[int, list[int]], root: int
) -> bool:
    """Get back to the operation data root from wherever a tap led.

    Stepping back is enough within the subtree, but a tap on the header can drop
    the panel all the way to the home screen, where the back button does nothing.
    """
    if await _async_back_to(client, page_map, root):
        return True
    try:
        if await client.async_goto_operation_root():
            return await client.async_current_page() == root
    except CtcWebError:
        return False
    return False


async def _async_tap_targets(
    client: CtcWebClient, page_map: dict[int, list[int]], page: int
) -> list[tuple[int, int]]:
    """Return distinct points worth tapping on a page, in reading order."""
    seen: set[tuple[int, int, int, int]] = set()
    targets: list[tuple[int, int, int, int]] = []
    for screen in page_map.get(page, []):
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            continue
        for widget in widgets:
            if not widget.visible or widget.width < 20 or widget.height < 14:
                continue
            if widget.x < 0 or widget.y < 0:
                continue
            if widget.y < 45:
                continue  # the header carries the clock and the back button
            if widget.width >= 460 and widget.height >= 250:
                continue  # the page background, not a control
            box = (widget.x, widget.y, widget.width, widget.height)
            if box in seen:
                continue
            seen.add(box)
            targets.append(box)
    return [(x + w // 2, y + h // 2) for x, y, w, h in _order_targets(targets)]


def _order_targets(
    targets: list[tuple[int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    """Try the tab strip first, then everything else.

    A row of equally sized boxes along the bottom is a tab strip, and tabs are
    what actually lead somewhere. The rest of an operation data page is usually
    the schematic, worth trying but not worth trying first: spending the tap
    budget on it is how the history page got missed.
    """
    bottom = [b for b in targets if b[1] > 200]
    widths = {b[2] for b in bottom}
    tabs = sorted(bottom, key=lambda b: b[0]) if len(bottom) >= 3 and len(widths) <= 2 else []
    rest = sorted((b for b in targets if b not in tabs), key=lambda b: (b[1], b[0]))
    return tabs + rest


async def _async_back_to(
    client: CtcWebClient, page_map: dict[int, list[int]], target: int, hops: int = 4
) -> bool:
    """Step back with the chrome button until the target page is showing."""
    for _ in range(hops):
        here = await client.async_current_page()
        if here == target:
            return True
        await client.async_click(page_map.get(here, []), 440, 23)
        if await client.async_current_page() == here:
            return False
    return await client.async_current_page() == target


async def _async_collect(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    page: int,
    into: list[SlowPage],
    visited: set[int],
    route: list[tuple[int, int]],
) -> None:
    """Describe one page and remember how it was reached."""
    if page in visited:
        return
    visited.add(page)
    screens = page_map.get(page, [])
    if not screens:
        return
    title = await async_page_title(client, screens)
    values = await async_page_values(client, page, screens)
    into.append(
        SlowPage(
            page=page,
            title=title,
            screens=list(screens),
            values=values,
            route=list(route),
        )
    )


async def _async_operation_root(
    client: CtcWebClient, page_map: dict[int, list[int]], origin: int
) -> int | None:
    """Navigate to the operation data menu and return the page it landed on."""
    if await client.async_goto_operation_root():
        return await client.async_current_page()
    return None


async def _async_restore(
    client: CtcWebClient, page_map: dict[int, list[int]], origin: int
) -> None:
    """Put the panel back on the page it was showing before we started."""
    try:
        if await client.async_step_back_to(origin):
            return
        # Backing out did not get there. If the panel started on the operation
        # data root, walking in from the home screen does.
        if await client.async_goto_operation_root():
            if await client.async_current_page() == origin:
                return
        await client.async_goto_home()
    except CtcWebError:
        _LOGGER.debug("Could not restore the panel to page %s", origin)


def pages_to_storage(pages: list[SlowPage]) -> list[dict[str, Any]]:
    """Serialise the catalogue so it survives a restart without rescanning."""
    return [
        {
            "page": page.page,
            "title": page.title,
            "screens": list(page.screens),
            "route": [list(step) for step in page.route],
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
                route=[(int(step[0]), int(step[1])) for step in item.get("route", [])],
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
