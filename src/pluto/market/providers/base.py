from __future__ import annotations

from typing import Any

import httpx

from pluto.market.types import ProviderError


class HttpError(ProviderError):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} from {url}")
        self.status = status


async def request_json(client: httpx.AsyncClient, method: str, url: str, **kw: Any) -> Any:
    try:
        r = await client.request(method, url, **kw)
    except httpx.HTTPError as e:
        raise ProviderError(f"{type(e).__name__}: {e} ({url})") from e
    if r.status_code >= 400:
        raise HttpError(r.status_code, url)
    try:
        return r.json()
    except ValueError as e:
        raise ProviderError(f"invalid JSON from {url}") from e


async def get_json(client: httpx.AsyncClient, url: str, **kw: Any) -> Any:
    return await request_json(client, "GET", url, **kw)
