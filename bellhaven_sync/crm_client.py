"""HTTP client for the Bellhaven CRM sandbox API."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .models import CrmAccount, CrmContact


class CrmApiError(RuntimeError):
    """Raised when the CRM API returns an HTTP error or invalid JSON payload."""


class CrmClient:
    """Thin JSON REST client for GET/POST/PATCH against /api/v1."""

    def __init__(
        self,
        api_base: str,
        api_token: str,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not api_token:
            raise ValueError("BELLHAVEN_API_TOKEN is required to access the CRM API.")
        self.api_base = api_base.rstrip("/")
        self.api_token = api_token
        self._opener = opener or urllib.request.urlopen

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.api_base}{path}"
        if params:
            filtered = {k: v for k, v in params.items() if v is not None and v != ""}
            if filtered:
                url = f"{url}?{urllib.parse.urlencode(filtered)}"

        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
            "User-Agent": "bellhaven-sync/1.0",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener(req, timeout=25) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CrmApiError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CrmApiError(f"{method} {path} connection failed: {exc.reason}") from exc

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/me")

    def list_accounts(self, page_size: int = 100) -> list[CrmAccount]:
        return [CrmAccount.from_api(row) for row in self._paginate("/accounts", page_size)]

    def list_contacts(self, page_size: int = 100) -> list[CrmContact]:
        return [CrmContact.from_api(row) for row in self._paginate("/contacts", page_size)]

    def _paginate(self, path: str, page_size: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._request("GET", path, params={"page": page, "page_size": page_size})
            if isinstance(data, dict):
                items = data.get("data") or data.get("accounts") or data.get("contacts") or []
            else:
                items = data
            if not items:
                break
            results.extend(items)
            total = data.get("total") if isinstance(data, dict) else None
            if total is not None and len(results) >= int(total):
                break
            if len(items) < page_size:
                break
            page += 1
        return results

    def get_account(self, account_id: str) -> CrmAccount:
        return CrmAccount.from_api(self._request("GET", f"/accounts/{account_id}"))

    def create_account(self, payload: dict[str, Any]) -> CrmAccount:
        return CrmAccount.from_api(self._request("POST", "/accounts", payload=payload))

    def patch_account(self, account_id: str, payload: dict[str, Any]) -> CrmAccount:
        return CrmAccount.from_api(self._request("PATCH", f"/accounts/{account_id}", payload=payload))

    def create_contact(self, payload: dict[str, Any]) -> CrmContact:
        return CrmContact.from_api(self._request("POST", "/contacts", payload=payload))

    def patch_contact(self, contact_id: str, payload: dict[str, Any]) -> CrmContact:
        return CrmContact.from_api(self._request("PATCH", f"/contacts/{contact_id}", payload=payload))
