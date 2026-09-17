"""The guarded walk to the display's system information page.

The panel is shared with whoever is standing at it, and the menus the walk
passes hold a function test, a compressor quick start, reinstallation and a
firmware update. These tests hold the walk to its promises: it presses nothing
but the controls it is allowed to, it stops at the first surprise, and it puts
the panel back where it found it.
"""

from __future__ import annotations

import asyncio


def run(coro):
    return asyncio.run(coro)


class FakePanel:
    """A display with a menu tree, counting every tap it is given."""

    def __init__(self, web_api, tree, values=None, start=1, swallow=None):
        self.web_api = web_api
        self.tree = tree            # page -> [(english, swedish, target page)]
        self.values = values or {}  # page -> list of (label, english, text)
        self.page = start
        self.parent = {t: p for p, items in tree.items() for _e, _s, t in items}
        self.taps: list[tuple[int, str]] = []
        self.swallow = swallow or set()   # pages where a tap does nothing

    # ---- the bits of CtcWebClient the walk uses
    async def async_screen_map(self):
        return {page: [page * 10] for page in set(self.tree) | set(self.values)}

    async def async_current_page(self):
        return self.page

    async def async_widgets(self, screen):
        page = screen // 10
        out = []
        for n, (_english, swedish, _target) in enumerate(self.tree.get(page, [])):
            out.append(self.web_api.Widget(index=n, kind=2, x=10 + n * 100, y=100,
                                           width=80, height=20, visible=True, label=swedish))
        for n, (swedish, _english, text) in enumerate(self.values.get(page, [])):
            out.append(self.web_api.Widget(index=100 + n, kind=2, x=10, y=40 + n * 30,
                                           width=120, height=20, visible=True, label=swedish))
            out.append(self.web_api.Widget(index=200 + n, kind=5, x=200, y=40 + n * 30,
                                           width=200, height=20, visible=True, text_value=text))
        return out

    async def async_english_label(self, screen, widget):
        page = screen // 10
        if widget.index < 100:
            items = self.tree.get(page, [])
            return items[widget.index][0] if widget.index < len(items) else None
        if 100 <= widget.index < 200:
            rows = self.values.get(page, [])
            n = widget.index - 100
            return rows[n][1] if n < len(rows) else None
        return None

    async def async_click(self, screens, x, y):
        if (x, y) == (440, 23):                      # the chrome's back button
            self.taps.append((self.page, "back"))
            self.page = self.parent.get(self.page, self.page)
            return []
        for english, _swedish, target in self.tree.get(self.page, []):
            n = [e for e, _s, _t in self.tree[self.page]].index(english)
            if abs(x - (10 + n * 100 + 40)) <= 40 and abs(y - 110) <= 10:
                self.taps.append((self.page, english))
                if self.page not in self.swallow:
                    self.page = target
                return []
        self.taps.append((self.page, f"miss at {x},{y}"))
        return []

    async def async_goto_home(self, hops: int = 6):
        for _ in range(hops):
            if self.page == 1:
                return 1
            await self.async_click([], 440, 23)
        return 1 if self.page == 1 else None

    async def async_goto_page(self, target, route=None):
        for _ in range(6):
            if self.page == target:
                return True
            await self.async_click([], 440, 23)
        return self.page == target

    @property
    def pressed(self):
        return [label for _page, label in self.taps]


SYSTEM_ROWS = [
    ("Serienummer", "Serial number", "720825408489"),
    ("MAC-adress", "MAC address", "02:00:00:12:34:56"),
    ("Programversion", "Program version", "20260610"),
    ("Bootloaderversion", "Bootloader version", "1.7"),
]

#: Home, Advanced with the service menu beside it, Display, and the page itself.
TREE = {
    1: [("Operation data", "Driftinfo", 20), ("Advanced", "Avancerat", 30)],
    30: [("Service", "Service", 50), ("Display", "Display", 40)],
    40: [("System information", "Systeminformation", 60)],
    50: [("Function test", "Funktionstest", 70), ("Reinstall", "Ominstallation", 80)],
}


def test_the_walk_finds_the_page_and_reads_it(identity, web_api):
    panel = FakePanel(web_api, TREE, {60: SYSTEM_ROWS}, start=20)
    found = run(identity.async_read_identity_via_panel(panel))
    assert found.serial == "720825408489"
    assert found.display_firmware == "20260610"
    assert found.bootloader == "1.7"


def test_the_panel_goes_back_home_by_itself(identity, web_api):
    # Stepping back only climbs the menu the walk came down, so a page on
    # another branch is out of reach: home is where the panel is left.
    panel = FakePanel(web_api, TREE, {60: SYSTEM_ROWS}, start=20)
    run(identity.async_read_identity_via_panel(panel))
    assert panel.page == 1


def test_the_harvester_can_put_the_panel_back_on_its_own_page(identity, web_api):
    panel = FakePanel(web_api, TREE, {60: SYSTEM_ROWS}, start=20)
    asked: list[int] = []

    async def restore(page):
        asked.append(page)
        panel.page = page

    run(identity.async_read_identity_via_panel(panel, restore=restore))
    assert asked == [20]
    assert panel.page == 20


def test_nothing_outside_the_allow_list_is_ever_pressed(identity, web_api):
    panel = FakePanel(web_api, TREE, {60: SYSTEM_ROWS}, start=1)
    run(identity.async_read_identity_via_panel(panel))
    assert "Function test" not in panel.pressed
    assert "Reinstall" not in panel.pressed
    assert "Operation data" not in panel.pressed


def test_a_tap_that_changes_nothing_stops_the_walk(identity, web_api):
    # A dialog, or a control that is not what it looked like: stop, do not poke.
    panel = FakePanel(web_api, TREE, {60: SYSTEM_ROWS}, start=1, swallow={30})
    found = run(identity.async_read_identity_via_panel(panel))
    assert found.serial is None
    assert panel.pressed.count("Service") + panel.pressed.count("Display") <= 1
    assert panel.page == 1


def test_a_page_the_menu_does_not_reach_gives_nothing(identity, web_api):
    without = {1: [("Advanced", "Avancerat", 30)], 30: [("Service", "Service", 50)], 50: []}
    panel = FakePanel(web_api, without, start=1)
    found = run(identity.async_read_identity_via_panel(panel))
    assert found.is_empty
    assert panel.page == 1


def test_the_walk_does_not_go_deeper_than_it_may(identity, web_api):
    deep = {
        1: [("Advanced", "Avancerat", 30)],
        30: [("Display", "Display", 40)],
        40: [("Service", "Service", 50)],
        50: [("System information", "Systeminformation", 60)],
    }
    panel = FakePanel(web_api, deep, {60: SYSTEM_ROWS}, start=1)
    found = run(identity.async_read_identity_via_panel(panel, depth=2))
    assert found.is_empty
