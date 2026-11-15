"""Latest official cloudflared release (tag + SHA-256 of the file for one architecture)."""

from __future__ import annotations

import httpx

from raspi.core.connector import (
    ConnectorError,
    Download,
    asset_for,
    parse_release_checksum,
)

LATEST = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"


def fetch_download(machine: str, http: httpx.Client | None = None) -> Download:
    """Sends nothing about the user: a plain public GET with a generic client name."""
    asset = asset_for(machine)
    client = http or httpx.Client(timeout=15)
    try:
        response = client.get(
            LATEST,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "raspi-manager",
            },
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectorError(
            f"Could not read the latest cloudflared release: {exc}"
        ) from exc
    if asset not in {a.get("name") for a in data.get("assets", [])}:
        raise ConnectorError(f"The latest release has no file named {asset}")
    sha = parse_release_checksum(data.get("body") or "", asset)
    if not sha or not data.get("tag_name"):
        raise ConnectorError(
            f"No SHA-256 for {asset} in the release notes: install refused"
        )
    return Download(data["tag_name"], asset, sha)
