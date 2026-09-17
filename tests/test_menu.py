"""The stored menu: what is kept, and what is harvested after an update."""

from __future__ import annotations

import json
import pathlib
import re


def page(const, number, title="Sida"):
    return const.SlowPage(page=number, title=f"{title} {number}", screens=[number * 10])


def test_the_first_reading_switches_every_page_on(catalogue, const):
    discovered = [page(const, 1), page(const, 2), page(const, 3)]
    menu, selected = catalogue.merge_menu([], [], discovered)
    assert [p.page for p in menu] == [1, 2, 3]
    assert selected == [1, 2, 3]


def test_a_page_switched_off_stays_off(catalogue, const):
    previous = [page(const, 1), page(const, 2), page(const, 3)]
    discovered = [page(const, 1), page(const, 2), page(const, 3)]
    _menu, selected = catalogue.merge_menu(previous, [1, 3], discovered)
    assert selected == [1, 3]


def test_a_page_the_menu_gained_is_harvested(catalogue, const):
    # A new version reads the menu again; a page nobody has said no to is read.
    previous = [page(const, 1), page(const, 2)]
    discovered = [page(const, 1), page(const, 2), page(const, 9)]
    _menu, selected = catalogue.merge_menu(previous, [1], discovered)
    assert selected == [1, 9]


def test_a_page_that_is_gone_is_forgotten(catalogue, const):
    previous = [page(const, 1), page(const, 2)]
    discovered = [page(const, 1)]
    menu, selected = catalogue.merge_menu(previous, [1, 2], discovered)
    assert [p.page for p in menu] == [1]
    assert selected == [1]


# ------------------------------------------------- the repairs view's texts


def test_every_repair_message_has_its_texts():
    # Without the strings Home Assistant shows the bare key in the repairs view.
    root = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"
    source = (root / "__init__.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'^ISSUE_\w+ = "([a-z_]+)"', source, re.M))
    assert keys, "inga meddelanden hittades i __init__.py"
    for name in ("strings.json", "translations/en.json", "translations/sv.json"):
        issues = json.loads((root / name).read_text(encoding="utf-8")).get("issues", {})
        for key in keys:
            assert key in issues, f"{key} saknas i {name}"
            assert issues[key].get("title") and issues[key].get("description")
