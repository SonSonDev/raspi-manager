"""Cloudflare settings: the API token lives in the Windows Credential Manager (keyring),
account/zone identifiers (not secret) in ~/.raspi/cloudflare.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import keyring
import yaml
from keyring.errors import KeyringError, PasswordDeleteError

from raspi import APP_NAME
from raspi.config.loader import raspi_home
from raspi.core.routes import RouteError, check_cf_id

_USERNAME = "cloudflare-api-token"


class SecretStoreError(Exception):
    """The OS credential store is unavailable or refused the operation."""


def get_token() -> str | None:
    try:
        return keyring.get_password(APP_NAME, _USERNAME)
    except KeyringError as exc:
        raise SecretStoreError(
            f"Cannot read the Windows Credential Manager: {type(exc).__name__}"
        ) from exc


def set_token(token: str) -> None:
    token = token.strip()
    if not token:
        raise SecretStoreError("The token is empty")
    try:
        keyring.set_password(APP_NAME, _USERNAME, token)
    except KeyringError as exc:
        raise SecretStoreError(
            f"Cannot write to the Windows Credential Manager: {type(exc).__name__}"
        ) from exc


def delete_token() -> None:
    try:
        keyring.delete_password(APP_NAME, _USERNAME)
    except PasswordDeleteError:
        pass  # nothing stored
    except KeyringError as exc:
        raise SecretStoreError(
            f"Cannot delete from the Windows Credential Manager: {type(exc).__name__}"
        ) from exc


@dataclass(frozen=True)
class CloudflareSettings:
    account_id: str = ""
    zone_id: str = ""
    zone_name: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.account_id and self.zone_id and self.zone_name)


def _path() -> Path:
    return raspi_home() / "cloudflare.yaml"


def load_settings() -> CloudflareSettings:
    path = _path()
    if not path.exists():
        return CloudflareSettings()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return CloudflareSettings(
        account_id=str(data.get("account_id", "")),
        zone_id=str(data.get("zone_id", "")),
        zone_name=str(data.get("zone_name", "")),
    )


def save_settings(settings: CloudflareSettings) -> None:
    """Validates identifiers before writing."""
    account = (
        check_cf_id(settings.account_id, "account ID") if settings.account_id else ""
    )
    zone = check_cf_id(settings.zone_id, "zone ID") if settings.zone_id else ""
    name = settings.zone_name.strip().lower()
    if name and "." not in name:
        raise RouteError(f"Invalid zone name: {name!r}")
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        yaml.safe_dump(
            {"account_id": account, "zone_id": zone, "zone_name": name}, sort_keys=False
        ),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)
