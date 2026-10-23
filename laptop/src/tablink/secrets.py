"""TabLink's secrets in the GNOME keyring (never in config files or logs).

Two secrets live here: the control-channel pairing token shared with the tablet app, and the
laptop hotspot's Wi-Fi password.
"""

from __future__ import annotations

import secrets as pysecrets
from collections.abc import Callable
from typing import Any, Protocol

LABELS = {"control-token": "TabLink pairing token", "hotspot-password": "TabLink hotspot password"}


class Store(Protocol):
    def get(self) -> str | None: ...

    def set(self, value: str) -> None: ...


class KeyringStore:
    """GNOME keyring through the Secret Service API. One store per kind of secret."""

    def __init__(self, kind: str = "control-token") -> None:
        if kind not in LABELS:
            raise ValueError(f"unknown secret kind: {kind}")
        self.kind = kind
        self.attrs = {"application": "tablink", "kind": kind}

    def _collection(self) -> Any:
        import secretstorage

        conn = secretstorage.dbus_init()
        coll = secretstorage.get_default_collection(conn)
        if coll.is_locked():
            coll.unlock()
        return coll

    def get(self) -> str | None:
        for item in self._collection().search_items(self.attrs):
            return str(item.get_secret().decode())
        return None

    def set(self, value: str) -> None:
        self._collection().create_item(LABELS[self.kind], self.attrs, value.encode(), replace=True)


class MemoryStore:
    """For tests."""

    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def get(self) -> str | None:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def new_token() -> str:
    return pysecrets.token_hex(32)


def control_token(
    store: Store, create: bool = False, make: Callable[[], str] = new_token
) -> str | None:
    """The pairing token; with create=True a new one is made when none exists."""
    token = store.get()
    if token is None and create:
        token = make()
        store.set(token)
    return token


def get_or_create(store: Store, make: Callable[[], str]) -> str:
    value = store.get()
    if value is None:
        value = make()
        store.set(value)
    return value
