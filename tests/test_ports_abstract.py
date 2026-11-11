"""Every interface in `arete.domain` is abstract, and every implementation inherits it.

A port whose methods carry bodies lets an implementer skip one and fail at the first
call. A port that implementations do not inherit is checked by the type checkers only.
These tests keep both closed, over the interfaces found by walking `arete.domain` and
the implementations found by walking the whole package, so a new port or adapter is
covered without a list to update.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from abc import ABC
from typing import Any

import pytest

import arete


def _modules(package: Any) -> list[Any]:
    names = [package.__name__]
    names += [m.name for m in pkgutil.walk_packages(package.__path__, f"{package.__name__}.")]
    return [importlib.import_module(name) for name in names]


def _classes(package: Any) -> list[type]:
    found: dict[type, None] = {}
    for module in _modules(package):
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__:
                found[cls] = None
    return list(found)


def _is_interface(cls: type) -> bool:
    return (
        bool(getattr(cls, "_is_protocol", False)) or ABC in cls.__bases__ or inspect.isabstract(cls)
    )


def _methods(cls: type) -> list[str]:
    """Public methods the interface declares or inherits, including async ones."""
    return sorted(
        name for name, _ in inspect.getmembers(cls, inspect.isfunction) if not name.startswith("_")
    )


INTERFACES = sorted(
    (cls for cls in _classes(importlib.import_module("arete.domain")) if _is_interface(cls)),
    key=lambda cls: cls.__name__,
)
WITH_METHODS = [cls for cls in INTERFACES if _methods(cls)]
SRC_CLASSES = _classes(arete)


def _implements(cls: type, interface: type) -> bool:
    """Whether `cls` has every public method of `interface`, inherited or not."""
    return all(callable(getattr(cls, name, None)) for name in _methods(interface))


def _inherits(cls: type, interface: type) -> bool:
    """`issubclass` refuses a Protocol that is not runtime-checkable; the MRO does not."""
    return interface in cls.__mro__


def _by_name(interface: type) -> str:
    return interface.__name__


def test_the_walk_finds_the_interfaces() -> None:
    names = {cls.__name__ for cls in INTERFACES}
    assert {
        "AnkiBridge",
        "NoteStorePort",
        "SyncPort",
        "QueuePort",
        "AnkiAdminPort",
        "NoteStoreSession",
        "ContentCache",
        "StatsRepository",
    } <= names


@pytest.mark.parametrize("interface", WITH_METHODS, ids=_by_name)
def test_every_public_method_is_abstract(interface: type) -> None:
    concrete = set(_methods(interface)) - set(interface.__abstractmethods__)  # type: ignore[attr-defined]
    assert not concrete, f"{interface.__name__} has methods with a default body: {sorted(concrete)}"


def _omitting(interface: type, omitted: str | None) -> type:
    def stub(*_args: Any, **_kwargs: Any) -> None:
        return None

    body = {name: stub for name in _methods(interface) if name != omitted}
    return type(f"Without_{omitted}", (interface,), body)


@pytest.mark.parametrize("interface", WITH_METHODS, ids=_by_name)
def test_a_subclass_with_every_method_can_be_built(interface: type) -> None:
    _omitting(interface, None)()


@pytest.mark.parametrize(
    ("interface", "method"),
    [(cls, name) for cls in WITH_METHODS for name in _methods(cls)],
    ids=lambda value: value if isinstance(value, str) else value.__name__,
)
def test_a_subclass_that_omits_one_method_cannot_be_built(interface: type, method: str) -> None:
    with pytest.raises(TypeError, match=method):
        _omitting(interface, method)()


@pytest.mark.parametrize("interface", WITH_METHODS, ids=_by_name)
def test_every_implementation_inherits_its_interface(interface: type) -> None:
    lookalikes = [
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in SRC_CLASSES
        if cls is not interface and _implements(cls, interface) and not _inherits(cls, interface)
    ]
    assert not lookalikes, f"implements {interface.__name__} without inheriting it: {lookalikes}"


@pytest.mark.parametrize("interface", WITH_METHODS, ids=_by_name)
def test_every_interface_has_a_concrete_implementation_in_src(interface: type) -> None:
    concrete = [
        cls
        for cls in SRC_CLASSES
        if cls is not interface and _inherits(cls, interface) and not inspect.isabstract(cls)
    ]
    assert concrete, f"no concrete implementation of {interface.__name__} found under src/arete"
