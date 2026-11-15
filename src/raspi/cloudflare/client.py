"""Minimal Cloudflare API client (tunnels, ingress, DNS). The API token is never logged or put in errors.

Endpoints, checked against Cloudflare's docs on 2026-10-17:
  GET  /user/tokens/verify
  GET  /accounts/{account}/cfd_tunnel?name=&is_deleted=false
  POST /accounts/{account}/cfd_tunnel            {"name", "config_src": "cloudflare"} -> id, token
  GET  /accounts/{account}/cfd_tunnel/{id}/token -> result is the token string
  DELETE /accounts/{account}/cfd_tunnel/{id}      (needs no active connection; DNS records are not removed)
  GET|PUT /accounts/{account}/cfd_tunnel/{id}/configurations   {"config": {"ingress": [...]}}
  POST /zones/{zone}/dns_records                 {"type": "CNAME", "proxied": true, ...}
"""

from __future__ import annotations

import re
from typing import Any

import httpx

API = "https://api.cloudflare.com/client/v4"


class CloudflareError(Exception):
    """Readable failure; never contains the token."""


_ID = re.compile(r"[0-9a-f]{32}")


def _describe(path: str) -> str:
    """Path with identifiers shortened (they are not secret, but long and noisy)."""
    return _ID.sub(lambda m: m.group(0)[:6] + "…", path)


def _permission_hint(path: str) -> str:
    if "/cfd_tunnel" in path:
        return (
            " The token needs Account › Cloudflare Tunnel › Edit for THIS account "
            "(check the Account ID and that the token's account resources include it)."
        )
    if "/dns_records" in path:
        return " The token needs Zone › DNS › Edit on this zone (check the zone ID and the token's zone resources)."
    if path.startswith("/zones"):
        return (
            " Looking up a zone needs Zone › Zone › Read on it; "
            "or type the zone ID and account ID by hand and skip the look-up."
        )
    return " Check the token permissions and its resources (account, zone)."


class CloudflareClient:
    def __init__(self, token: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=20.0)
        self._headers = {"Authorization": f"Bearer {token}"}

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(
                method, API + path, headers=self._headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise CloudflareError(
                f"Cannot reach Cloudflare: {type(exc).__name__}"
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise CloudflareError(
                f"Unexpected answer from Cloudflare (HTTP {response.status_code})"
            ) from exc
        if not body.get("success", False):
            errors = body.get("errors") or []
            detail = "; ".join(
                f"{e.get('message', 'error')} (code {e.get('code', '?')})"
                for e in errors
            )
            hint = _permission_hint(path) if response.status_code in (401, 403) else ""
            raise CloudflareError(
                f"Cloudflare refused {method} {_describe(path)} (HTTP {response.status_code}): "
                f"{detail or 'no detail'}.{hint}"
            )
        return body.get("result")

    # --- identity ----------------------------------------------------------------
    def verify_token(self) -> dict:
        """User-owned tokens only; account-owned tokens may not verify here."""
        return self._call("GET", "/user/tokens/verify")

    # --- tunnels -----------------------------------------------------------------
    def list_tunnels(self, account: str, name: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"is_deleted": "false", "per_page": 100}
        if name:
            params["name"] = name
        return self._call("GET", f"/accounts/{account}/cfd_tunnel", params=params) or []

    def create_tunnel(self, account: str, name: str) -> dict:
        """Returns the tunnel including its `id` and connector `token` (a secret)."""
        return self._call(
            "POST",
            f"/accounts/{account}/cfd_tunnel",
            json={"name": name, "config_src": "cloudflare"},
        )

    def delete_tunnel(self, account: str, tunnel_id: str) -> dict:
        """Irreversible. Cloudflare refuses it while the tunnel has active connections."""
        return self._call("DELETE", f"/accounts/{account}/cfd_tunnel/{tunnel_id}")

    def tunnel_token(self, account: str, tunnel_id: str) -> str:
        return self._call("GET", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/token")

    def get_ingress(self, account: str, tunnel_id: str) -> list[dict]:
        result = (
            self._call(
                "GET", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/configurations"
            )
            or {}
        )
        return (result.get("config") or {}).get("ingress") or []

    def put_ingress(self, account: str, tunnel_id: str, ingress: list[dict]) -> None:
        self._call(
            "PUT",
            f"/accounts/{account}/cfd_tunnel/{tunnel_id}/configurations",
            json={"config": {"ingress": ingress}},
        )

    # --- zones and DNS -------------------------------------------------------------
    def list_zones(self, name: str | None = None) -> list[dict]:
        params = {"name": name} if name else {}
        return self._call("GET", "/zones", params=params) or []

    def list_all_dns(self, zone: str, max_pages: int = 20) -> list[dict]:
        """Every record of the zone (read-only), page by page."""
        records: list[dict] = []
        for page in range(1, max_pages + 1):
            batch = (
                self._call(
                    "GET",
                    f"/zones/{zone}/dns_records",
                    params={"page": page, "per_page": 100},
                )
                or []
            )
            records.extend(batch)
            if len(batch) < 100:
                break
        return records

    def list_dns(self, zone: str, name: str) -> list[dict]:
        return (
            self._call("GET", f"/zones/{zone}/dns_records", params={"name": name}) or []
        )

    def repoint_cname(self, zone: str, record: dict, target: str) -> dict:
        """PATCH an existing CNAME to a new target, keeping its ttl and comment.

        Type and name are sent too: the docs do not say whether omitted fields are kept."""
        body: dict[str, Any] = {
            "type": "CNAME",
            "name": record["name"],
            "content": target,
            "proxied": True,
            "ttl": record.get("ttl", 1),
        }
        if record.get("comment"):
            body["comment"] = record["comment"]
        return self._call(
            "PATCH", f"/zones/{zone}/dns_records/{record['id']}", json=body
        )

    def create_cname(self, zone: str, name: str, target: str) -> dict:
        return self._call(
            "POST",
            f"/zones/{zone}/dns_records",
            json={"type": "CNAME", "name": name, "content": target, "proxied": True},
        )
