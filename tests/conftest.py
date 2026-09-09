"""Load the integration's Home Assistant free modules without installing HA.

The package's ``__init__`` pulls in Home Assistant, which the unit tests do not
need and should not require. The modules under test are therefore loaded
directly into a stub package so that their relative imports still resolve.
"""

import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ctc_ecozenith"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

if "ctc_ecozenith" not in sys.modules:
    stub = types.ModuleType("ctc_ecozenith")
    stub.__path__ = [str(COMPONENT)]
    sys.modules["ctc_ecozenith"] = stub


def load(name: str):
    full = f"ctc_ecozenith.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def const():
    return load("const")


@pytest.fixture(scope="session")
def web_api():
    return load("web_api")


@pytest.fixture(scope="session")
def modbus_api():
    return load("modbus_api")


@pytest.fixture(scope="session")
def catalogue():
    return load("catalogue")


@pytest.fixture(scope="session")
def discovery():
    return load("discovery")


@pytest.fixture(scope="session")
def stats_extra():
    return load("stats_extra")


@pytest.fixture(scope="session")
def wp118() -> str:
    return (FIXTURES / "wp_118.js").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def vars118() -> str:
    return (FIXTURES / "vars_118.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def sm_all() -> str:
    return (FIXTURES / "sm_all.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def cop():
    return load("cop")


@pytest.fixture(scope="session")
def identity():
    return load("identity")
