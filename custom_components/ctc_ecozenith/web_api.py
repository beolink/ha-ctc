"""Client for the CTC display's built in web server ("screen mirror").

The display serves the same interface that myUplink's remote view proxies under
``/remoteweb/disp``. It is undocumented, so everything here was derived from the
JavaScript the display itself serves.

Endpoints
    GET  /main.html            entry point, the root path answers 400
    GET  /sm/all               page map, ``page:screen|screen|...|page:...``
    GET  /wp/<n>               screen definition, always gzipped
    GET  /vars/<n>             values for screen n
    GET  /vars/glob            date and time
    GET  /vars/menu            first field is the page the panel is showing
    GET  /txt/<lang>/<id>      one label in one language
    GET  /settings/name        settings file name, encodes the model family
    POST /click/<path>         body "x,y"
    POST /scroll/<path>        body "<delta>"

The path for click and scroll is a list, ``glob;menu;<screen>;<screen>;...``,
naming the screens the answer should contain. The answer has one line per list
entry separated by carriage returns.

Two properties matter for anything built on this:

* Only the page the panel is currently showing is kept up to date. Every other
  screen returns a frozen snapshot from the last time it was rendered. Asking
  for another page's screens in the click path does not refresh them.
* Navigating moves the physical panel. There is one shared display state and no
  second session; ``/click2/`` and ``/scroll2/`` exist in the JavaScript but the
  firmware answers 400 to them.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import WEB_MAX_CONCURRENCY

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class CtcWebError(Exception):
    """Raised when the display's web server cannot be used."""


@dataclass
class Widget:
    """One drawable element on a screen, with its resolved geometry."""

    index: int
    kind: int
    x: int
    y: int
    width: int
    height: int
    visible: bool
    label: str | None = None
    value_fmt: str | None = None
    value_vars: list[int] | None = None
    #: Content of a string element (widget kind 5). The panel uses these for the
    #: serial number, the MAC address and the firmware versions, which are
    #: written once and therefore readable whatever page the panel is showing.
    text_value: str | None = None

    @property
    def centre(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def overlaps_horizontally(self, other: "Widget") -> bool:
        return self.x < other.x + other.width and other.x < self.x + self.width


@dataclass
class ScreenDef:
    """A parsed screen definition from ``/wp/<n>``."""

    index: int
    c0: list[int]
    c1: list[list[Any]]
    v0: list[int]
    t0: list[int]
    t1: list[list[Any]]
    t2: list[list[Any]]


def _balanced_array(source: str, name: str) -> str | None:
    """Return the literal JavaScript array assigned to ``name``."""
    start = source.find(name + "=")
    if start < 0:
        return None
    open_at = source.find("[", start)
    if open_at < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    pos = open_at
    while pos < len(source):
        char = source[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return source[open_at : pos + 1]
        pos += 1
    return None


def _parse_array(source: str, name: str) -> list:
    raw = _balanced_array(source, name)
    if raw is None:
        return []
    try:
        return json.loads(raw)
    except ValueError:
        return []


def parse_vars(payload: str) -> list[Any]:
    """Split a ``/vars/`` payload into ints, keeping quoted strings as text."""
    out: list[Any] = []
    for field in payload.strip().split("|"):
        if field == "":
            continue
        if field.startswith('"'):
            out.append(field.strip('"'))
            continue
        try:
            out.append(int(field))
        except ValueError:
            out.append(field)
    return out


def tap_target(widgets: list["Widget"], label_widget: "Widget") -> tuple[int, int]:
    """Return a point that actually presses the tile a caption belongs to.

    Home screen tiles are an icon with the caption drawn underneath it as a
    separate element. Matching finds the caption, but its own centre can fall
    outside the touch area, so the icon sitting directly above it is preferred.
    """
    candidates = [
        w
        for w in widgets
        if w is not label_widget
        and w.visible
        and w.kind in (0, 1)
        and w.width > 20
        and w.height > 20
        and w.y + w.height <= label_widget.y + 4
        and w.overlaps_horizontally(label_widget)
    ]
    if candidates:
        icon = max(candidates, key=lambda w: w.y)
        return icon.centre
    return label_widget.centre


def _select_from_group(group: Any, selector: int) -> tuple[str, int] | None:
    """Pick one alternative out of a t1 group.

    A group is a flat list of alternatives. Codes 0, 1 and 2 occupy three slots
    and point at a format entry, codes 3 and 4 occupy two and hold a text id and
    a format index respectively. ``selector`` counts alternatives, not slots.
    """
    if not isinstance(group, list):
        return None
    pos = 0
    ordinal = 0
    while pos < len(group):
        code = group[pos]
        if ordinal == selector:
            if code in (0, 1, 2) and pos + 2 < len(group):
                return ("fmt", group[pos + 2])
            if code == 3 and pos + 1 < len(group):
                return ("text", group[pos + 1])
            if code == 4 and pos + 1 < len(group):
                return ("fmt", group[pos + 1])
            return None
        pos += 3 if code in (0, 1, 2) else 2
        ordinal += 1
    return None


def _primary_variant(spec: Any) -> tuple[str | None, list[int]]:
    """Return the normal format string of a t2 entry and the vars it reads.

    A t2 entry is a run of triples: format string, the selector value that picks
    it, and the list of variable references. The first triple is the normal
    case; the later ones are the "no sensor fitted" renderings.
    """
    if not isinstance(spec, list) or not spec or not isinstance(spec[0], str):
        return None, []
    fmt = spec[0]
    indices: list[int] = []
    refs = spec[2] if len(spec) > 2 else None
    if isinstance(refs, list):
        for i in range(0, len(refs) - 1, 2):
            if refs[i] == 1 and isinstance(refs[i + 1], int):
                indices.append(refs[i + 1])
    return fmt, indices


def parse_screen_map(payload: str) -> dict[int, list[int]]:
    """Parse ``/sm/all`` into ``{page: [screen, ...]}``."""
    pages: dict[int, list[int]] = {}
    current: int | None = None
    for token in payload.strip().split("|"):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            page_text, first = token.split(":", 1)
            try:
                current = int(page_text)
            except ValueError:
                current = None
                continue
            pages[current] = []
            if first:
                try:
                    pages[current].append(int(first))
                except ValueError:
                    pass
        elif current is not None:
            try:
                pages[current].append(int(token))
            except ValueError:
                pass
    return pages


class CtcWebClient:
    """Read, and when asked navigate, the CTC display over HTTP."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = 80,
        language: int = 1,
    ) -> None:
        self._session = session
        self._host = host
        self._port = port
        self._language = language
        self._semaphore = asyncio.Semaphore(WEB_MAX_CONCURRENCY)
        self._screen_cache: dict[int, ScreenDef] = {}
        self._text_cache: dict[int, str] = {}
        self._screen_map: dict[int, list[int]] | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    async def _request(self, path: str, body: str | None = None) -> str:
        """Perform one request, decompressing when the server gzips regardless."""
        url = f"{self.base_url}{path}"
        async with self._semaphore:
            try:
                if body is None:
                    response = await self._session.get(url, timeout=REQUEST_TIMEOUT)
                else:
                    response = await self._session.post(
                        url, data=body.encode(), timeout=REQUEST_TIMEOUT
                    )
                async with response:
                    if response.status != 200:
                        raise CtcWebError(f"{path} answered HTTP {response.status}")
                    raw = await response.read()
            except aiohttp.ClientError as err:
                raise CtcWebError(f"{path} failed: {err}") from err
            except asyncio.TimeoutError as err:
                raise CtcWebError(f"{path} timed out") from err
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace").lstrip("﻿")

    # ---------------------------------------------------------------- probing

    async def async_settings_name(self) -> str:
        """Return the settings file name, e.g. ``settings_ezi255.bin``."""
        return (await self._request("/settings/name")).strip()

    async def async_probe(self) -> str:
        """Confirm this host is a CTC display and return its model family."""
        name = await self.async_settings_name()
        if not name.startswith("settings_") or not name.endswith(".bin"):
            raise CtcWebError(f"unexpected settings name {name!r}")
        return name

    # ------------------------------------------------------------------ reads

    async def async_screen_map(self, refresh: bool = False) -> dict[int, list[int]]:
        if self._screen_map is None or refresh:
            self._screen_map = parse_screen_map(await self._request("/sm/all"))
        return self._screen_map

    async def async_vars(self, screen: int | str) -> list[Any]:
        return parse_vars(await self._request(f"/vars/{screen}"))

    async def async_current_page(self) -> int:
        """Return the page the physical panel is showing right now."""
        values = await self.async_vars("menu")
        if not values or not isinstance(values[0], int):
            raise CtcWebError("could not read the current page")
        return values[0]

    async def async_text(self, text_id: int, language: int | None = None) -> str:
        lang = self._language if language is None else language
        cache_key = text_id if language is None else -(text_id * 100 + language)
        if cache_key in self._text_cache:
            return self._text_cache[cache_key]
        try:
            value = (await self._request(f"/txt/{lang}/{text_id}")).strip()
        except CtcWebError:
            value = ""
        self._text_cache[cache_key] = value
        return value

    async def async_screen_def(self, screen: int) -> ScreenDef:
        if screen in self._screen_cache:
            return self._screen_cache[screen]
        source = await self._request(f"/wp/{screen}")
        definition = ScreenDef(
            index=screen,
            c0=_parse_array(source, f"p{screen}c0"),
            c1=_parse_array(source, f"p{screen}c1"),
            v0=_parse_array(source, f"p{screen}v0"),
            t0=_parse_array(source, f"p{screen}t0"),
            t1=_parse_array(source, f"p{screen}t1"),
            t2=_parse_array(source, f"p{screen}t2"),
        )
        self._screen_cache[screen] = definition
        return definition

    # -------------------------------------------------------------- rendering

    async def async_widgets(
        self, screen: int, values: list[Any] | None = None, globals_: list[Any] | None = None
    ) -> list[Widget]:
        """Resolve a screen's widgets, with geometry and labels."""
        definition = await self.async_screen_def(screen)
        if values is None:
            values = await self.async_vars(screen)
        if globals_ is None:
            globals_ = await self.async_vars("glob")

        def var_value(kind: int, val: int) -> int:
            if kind == 0:
                return globals_[val] if val < len(globals_) else -1  # type: ignore[return-value]
            if kind == 1:
                got = values[val] if val < len(values) else -1
                return got if isinstance(got, int) else -1
            return val

        def ref(index: int) -> int:
            if index * 2 >= len(definition.v0):
                return -1
            return var_value(definition.v0[index * 2], definition.v0[index * 2 + 1])

        def raw_ref(index: int) -> str | None:
            """Resolve a reference without forcing it to a number.

            Geometry is always numeric, but a string element points at a text
            variable, and coercing that to an integer would throw the content
            away.
            """
            if index * 2 >= len(definition.v0):
                return None
            kind, val = definition.v0[index * 2], definition.v0[index * 2 + 1]
            if kind == 0:
                got = globals_[val] if val < len(globals_) else None
            elif kind == 1:
                got = values[val] if val < len(values) else None
            else:
                got = val
            if got is None or got == "":
                return None
            return str(got)

        widgets: list[Widget] = []
        for order, entry in enumerate(definition.c1):
            if not isinstance(entry, list) or len(entry) < 9:
                continue
            widget = Widget(
                index=order,
                kind=entry[0],
                x=ref(entry[2]),
                y=ref(entry[3]),
                width=ref(entry[4]),
                height=ref(entry[5]),
                visible=ref(entry[8]) != 0,
            )
            # A string element keeps its content where the others keep their
            # text array index, and it is resolved the same way the geometry is.
            if widget.kind == 5:
                if len(entry) > 12 and isinstance(entry[12], int):
                    widget.text_value = raw_ref(entry[12])
                widgets.append(widget)
                continue

            # Images carry their text array index at position 10, text elements
            # at position 12. Other widget kinds carry none.
            if widget.kind in (0, 1):
                slot_pos = 10
            elif widget.kind in (2, 3):
                slot_pos = 12
            else:
                widgets.append(widget)
                continue
            if slot_pos >= len(entry) or not isinstance(entry[slot_pos], int):
                widgets.append(widget)
                continue

            arr = entry[slot_pos]
            base = arr * 3
            if base + 2 >= len(definition.t0):
                widgets.append(widget)
                continue
            selector = var_value(definition.t0[base], definition.t0[base + 1])
            group_index = definition.t0[base + 2]
            if not isinstance(group_index, int) or group_index >= len(definition.t1):
                widgets.append(widget)
                continue
            group = definition.t1[group_index]
            picked = _select_from_group(group, selector)
            if picked is None:
                widgets.append(widget)
                continue
            kind, payload = picked
            if kind == "text":
                widget.label = await self.async_text(payload)
            elif kind == "fmt" and payload < len(definition.t2):
                spec = definition.t2[payload]
                fmt, indices = _primary_variant(spec)
                if fmt is not None:
                    widget.value_fmt = fmt
                    widget.value_vars = indices
            widgets.append(widget)
        return widgets

    async def async_english_label(self, screen: int, widget: Widget) -> str | None:
        """Resolve a widget's label in English, which is model independent.

        Text ids differ between models, 532 on an i255 and 570 on an i550 Pro for
        the same menu item, so navigation matches on the English string instead.
        """
        definition = await self.async_screen_def(screen)
        if widget.index >= len(definition.c1):
            return None
        entry = definition.c1[widget.index]
        if not isinstance(entry, list):
            return None
        slot_pos = 10 if widget.kind in (0, 1) else 12
        if slot_pos >= len(entry) or not isinstance(entry[slot_pos], int):
            return None
        base = entry[slot_pos] * 3
        if base + 2 >= len(definition.t0):
            return None
        values = await self.async_vars(screen)
        globals_ = await self.async_vars("glob")

        def var_value(kind: int, val: int) -> int:
            if kind == 0:
                got = globals_[val] if val < len(globals_) else -1
            elif kind == 1:
                got = values[val] if val < len(values) else -1
            else:
                return val
            return got if isinstance(got, int) else -1

        selector = var_value(definition.t0[base], definition.t0[base + 1])
        group_index = definition.t0[base + 2]
        if not isinstance(group_index, int) or group_index >= len(definition.t1):
            return None
        picked = _select_from_group(definition.t1[group_index], selector)
        if picked is None or picked[0] != "text":
            return None
        return await self.async_text(picked[1], language=0)

    async def async_find_widget(self, screen: int, label: str) -> Widget | None:
        """Return the first visible widget whose label matches, case folded."""
        wanted = label.strip().casefold()
        for widget in await self.async_widgets(screen):
            if widget.visible and widget.label and widget.label.strip().casefold() == wanted:
                return widget
        return None

    # ------------------------------------------------------------- navigation

    def _click_path(self, screens: list[int]) -> str:
        return ";".join(["glob", "menu", *(str(s) for s in screens)])

    async def async_click(self, screens: list[int], x: int, y: int) -> list[list[Any]]:
        """Send a tap. This moves the physical panel."""
        payload = await self._request(f"/click/{self._click_path(screens)}", f"{x},{y}")
        return [parse_vars(line) for line in payload.split("\r")]

    async def async_click_noop(self, screens: list[int]) -> list[list[Any]]:
        """Fetch the listed screens using a tap that cannot hit anything.

        The coordinate is outside the 480 by 272 panel, which is a verified no
        operation: the current page is unchanged afterwards.
        """
        return await self.async_click(screens, 9999, 9999)

    async def async_goto_page(
        self,
        target: int,
        route: list[tuple[int, int]] | None = None,
    ) -> bool:
        """Walk the panel to ``target``.

        With a ``route`` recorded during setup the taps are simply replayed from
        the operation data root, which is reliable across models. Without one the
        only thing attempted is stepping back, which is enough to restore the
        page the panel started on.
        """
        page_map = await self.async_screen_map()
        if target not in page_map:
            raise CtcWebError(f"page {target} is not in the screen map")
        if await self.async_current_page() == target:
            return True

        if route:
            if not await self.async_goto_operation_root():
                return False
            for x, y in route:
                here = await self.async_current_page()
                await self.async_click(page_map.get(here, []), x, y)
            return await self.async_current_page() == target

        return await self.async_step_back_to(target)

    async def async_step_back_to(self, target: int, hops: int = 6) -> bool:
        """Press the chrome's back button until ``target`` is showing.

        The button at the top right steps back inside a submenu, but on the home
        screen the same spot is a tile of its own. Revisiting a page therefore
        means backing out is going in circles, and the walk stops.
        """
        page_map = await self.async_screen_map()
        seen: set[int] = set()
        for _ in range(hops):
            here = await self.async_current_page()
            if here == target:
                return True
            if here in seen:
                return False
            seen.add(here)
            await self.async_click(page_map.get(here, []), 440, 23)
            if await self.async_current_page() == here:
                return False
        return await self.async_current_page() == target

    async def _async_operation_tile(
        self, page: int
    ) -> tuple[int, list[int], tuple[int, int]] | None:
        """Find the operation data tile on ``page``, if it is there."""
        from .const import OPERATION_DATA_LABEL_EN

        page_map = await self.async_screen_map()
        screens = page_map.get(page, [])
        for screen in screens:
            try:
                widgets = await self.async_widgets(screen)
            except CtcWebError:
                continue
            for widget in widgets:
                if not widget.visible or widget.label is None or widget.width <= 0:
                    continue
                if await self.async_english_label(screen, widget) == OPERATION_DATA_LABEL_EN:
                    return screen, screens, tap_target(widgets, widget)
        return None

    async def async_goto_home(self, hops: int = 6) -> int | None:
        """Step back until the home screen is showing.

        Home is recognised by carrying the operation data tile rather than by the
        back button ceasing to work, because on the home screen that spot is a
        button which would navigate somewhere else entirely.
        """
        page_map = await self.async_screen_map()
        seen: set[int] = set()
        for _ in range(hops):
            here = await self.async_current_page()
            if await self._async_operation_tile(here) is not None:
                return here
            if here in seen:
                return None
            seen.add(here)
            await self.async_click(page_map.get(here, []), 440, 23)
            if await self.async_current_page() == here:
                return None
        here = await self.async_current_page()
        return here if await self._async_operation_tile(here) is not None else None

    async def async_goto_operation_root(self) -> bool:
        """Navigate to the operation data menu from wherever the panel is."""
        home = await self.async_goto_home()
        if home is None:
            return False
        found = await self._async_operation_tile(home)
        if found is None:
            return False
        _screen, screens, (x, y) = found
        before = await self.async_current_page()
        await self.async_click(screens, x, y)
        return await self.async_current_page() != before
