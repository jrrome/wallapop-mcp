"""The Wallapop API client: every endpoint call + session bootstrap."""

from __future__ import annotations

import json
import math
import random
import time
import uuid
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Optional

from .config import (
    API_BASE,
    API_ROOT,
    CLIENT_ID,
    DEFAULT_GEO,
    PNSDK,
    PUB_KEY,
    PUB_ORIGIN,
    SUB_KEY,
    USER_AGENTS,
    WALLAPOP_COOKIE_KEYS,
    WEB_APP_VERSION,
    WEB_BASE,
    load_session,
    save_session,
)
from .http import ApiError, NotSentError, Response, eprint, http_request, http_request_raw
from .models import Item, deep_find, extract_items, first, normalize_item
from .tokens import user_hash_of


class InvalidPrice(ApiError, ValueError):
    """Raised when a price is not a valid, finite amount."""


#: Prices are amounts of money: only whole cents are accepted.
CENT = Decimal("0.01")

#: Local validation cap: a price outside it is rejected, never silently
#: truncated. This is a local validation limit, not a documented API limit.
MAX_PRICE = Decimal("99999999.99")


def price_amount(value: Any) -> float:
    """Convert a price into a validated amount, keeping the cents.

    ``19.95`` stays ``19.95`` (it used to be rounded to ``20``), while
    non-numeric, non-finite (``nan``/``inf``), negative, out of range
    (``0 .. 99999999.99``) and sub-cent values are rejected with
    :class:`InvalidPrice`.

    A price carrying more than two decimals is **rejected** instead of being
    rounded: rounding silently changes the amount the buyer pays.

    >>> price_amount(19.95)
    19.95
    """
    if value is None or isinstance(value, bool):
        raise InvalidPrice(f"invalid price: {value!r}")
    try:
        if isinstance(value, Decimal):
            amount = value
        elif isinstance(value, int):
            amount = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise InvalidPrice(f"invalid price: {value!r}")
            amount = Decimal(str(value))
        elif isinstance(value, str):
            text = value.strip().replace(",", ".")
            if not text:
                raise InvalidPrice(f"invalid price: {value!r}")
            amount = Decimal(text)
        else:
            raise InvalidPrice(f"invalid price: {value!r}")
    except (InvalidOperation, ValueError):
        raise InvalidPrice(f"invalid price: {value!r}") from None
    if not amount.is_finite():
        raise InvalidPrice(f"invalid price: {value!r}")
    if amount < 0:
        raise InvalidPrice(f"price must not be negative: {value!r}")
    if amount > MAX_PRICE:
        raise InvalidPrice(f"price out of range (0..{MAX_PRICE}): {value!r}")
    try:
        rounded = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise InvalidPrice(f"invalid price: {value!r}") from None
    if rounded != amount:
        raise InvalidPrice(f"price has more than two decimals: {value!r}")
    return float(rounded)


def _as_int(value: Any) -> Optional[int]:
    """Best effort int conversion (``None`` when the value is not a number)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_pubnub_ack(payload: Any) -> bool:
    """Return True when a PubNub publish payload acknowledges the message.

    PubNub answers ``[1, "Sent", <timetoken>]``.  Depending on the transport
    (and on the ``Content-Type`` the CDN replies with) that acknowledgement
    reaches us either already decoded (a list) or as a JSON string.
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return False
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return False
    if not isinstance(payload, list) or not payload:
        return False
    if payload[0] != 1:
        return False
    return len(payload) > 1 and str(payload[1]).strip().lower() == "sent"


def refresh_web_session(session: dict) -> Optional[str]:
    """Exchange a browser session cookie set for a fresh access token (GET /api/auth/session)."""
    session_token = session.get("__Secure-next-auth.session-token")
    if not session_token:
        return None
    cookie_parts = [f"__Secure-next-auth.session-token={session_token}"]
    for key in ("device_id", "device_fgp", "accessToken"):
        if session.get(key):
            cookie_parts.append(f"{key}={session[key]}")
    headers = {
        "Accept": "application/json",
        "Cookie": "; ".join(cookie_parts),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }
    status, data, _ = http_request("GET", f"{WEB_BASE}/api/auth/session", headers=headers)
    if status != 200 or not isinstance(data, dict):
        return None
    token = data.get("token") or data.get("accessToken") or ""
    return str(token) if isinstance(token, str) and token else None


def persist_browser_session(session: dict, token: Optional[str] = None) -> None:
    """Save a browser session (cookies + optional access token) to local storage."""
    updated = dict(session)
    if token:
        updated["accessToken"] = token
    save_session(updated)


def _saved_search_id_from_location(location: str) -> Optional[str]:
    """Extract the saved-search id from a ``Location`` header (last path segment).

    The server answers with a fresh UUID per saved search (e.g.
    ``/api/v3/searchalerts/savedsearch/ae1a42fb-...``).
    """
    last = urllib.parse.urlparse(location or "").path.rstrip("/").split("/")[-1]
    return last or None


class Wallapop:
    def __init__(
        self,
        token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        email: Optional[str] = None,
        persist_fn: Optional[Callable[["Wallapop"], None]] = None,
        web_cookies: Optional[dict] = None,
    ) -> None:
        self.token = token
        self.refresh_token = refresh_token
        self.email = email
        self.persist_fn = persist_fn
        self.web_cookies = web_cookies
        self.user: dict = {}

    def _headers(self, auth: bool = False) -> dict:
        device_id = (self.web_cookies or {}).get("device_id", "") or ""
        hdrs = {
            "Accept": "application/json, text/plain, */*",
            "Origin": WEB_BASE,
            "Referer": f"{WEB_BASE}/",
            "User-Agent": random.choice(USER_AGENTS),
            "X-DeviceOS": "0",
            "X-Product": "wallapop",
            "X-AppVersion": "826560",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        }
        if device_id:
            hdrs["X-DeviceID"] = device_id
        if auth and self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        return hdrs

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        auth: bool = False,
        extra_headers: Optional[dict] = None,
    ) -> Response:
        headers = self._headers(auth=auth)
        if extra_headers:
            headers.update(extra_headers)
        status, data, _ = http_request(
            method,
            f"{API_BASE}{path}",
            params=params,
            body=body,
            headers=headers,
            retry_safe=method.upper() in ("GET", "HEAD") and body is None,
        )
        return Response(status, data, 200 <= status < 300, status == 401)

    def _authed(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        extra_headers: Optional[dict] = None,
    ) -> Response:
        resp = self._request(method, path, params=params, body=body, auth=True, extra_headers=extra_headers)
        if resp.unauthorized and self._refresh_access_token():
            resp = self._request(method, path, params=params, body=body, auth=True, extra_headers=extra_headers)
        return resp

    def _refresh_access_token(self) -> bool:
        """Renew the access token after a 401.

        The ``refresh_token`` is tried first and the browser session second.
        Returns ``True`` only when a *different* token was obtained, so a
        request is never replayed with the very same (already rejected)
        token.
        """
        before = self.token
        if self.try_refresh() and self.token and self.token != before:
            return True
        if self.refresh_web() and self.token and self.token != before:
            return True
        return False

    def refresh_web(self) -> bool:
        """Refresh the access token from the browser session cookie; return True on success."""
        if not self.web_cookies:
            return False
        token = refresh_web_session(self.web_cookies)
        if not token:
            return False
        self.token = token
        if self.persist_fn is not None:
            self.persist_fn(self)
        return True

    def _save(self) -> None:
        if self.persist_fn is not None:
            self.persist_fn(self)

    def login(self, email: str, password: str) -> tuple[str, str]:
        """Log in by email/password and return (access_token, refresh_token)."""
        resp = self._request(
            "POST",
            "/access/login",
            body={"email": email, "password": password, "client_id": CLIENT_ID},
        )
        if not resp.ok:
            raise ApiError(f"login failed ({resp.status}): {resp.error_reason or resp.data}")
        data = resp.data
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            payload = data if isinstance(data, dict) else {}
        token = payload.get("access_token") or payload.get("token") or ""
        refresh = payload.get("refresh_token") or ""
        if not token:
            raise ApiError(f"login accepted but no token in response: {json.dumps(data, ensure_ascii=False)[:400]}")
        self.email = email
        self.token = token
        self.refresh_token = refresh
        self.user = payload.get("user", {}) if isinstance(payload, dict) else {}
        self._save()
        return token, refresh

    def try_refresh(self) -> bool:
        """Exchange refresh_token for a fresh access token; return True on success."""
        if not self.refresh_token or not self.token:
            return False
        resp = self._request(
            "POST",
            "/access/refresh",
            body={"refresh_token": self.refresh_token, "client_id": CLIENT_ID},
        )
        if not resp.ok:
            return False
        data = resp.data
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            payload = data if isinstance(data, dict) else {}
        token = payload.get("access_token") or payload.get("token") or ""
        if not token:
            return False
        self.token = token
        self.refresh_token = payload.get("refresh_token") or self.refresh_token
        self._save()
        return True

    def search(
        self,
        keywords: str,
        category_id: Optional[int] = None,
        subcategory_ids: Optional[int] = None,
        order_by: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        pages: int = 1,
        delay: float = 0.0,
        condition: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        ship_only: Optional[bool] = None,
    ) -> list[Item]:
        if latitude is None or longitude is None:
            latitude, longitude = DEFAULT_GEO
        order = order_by or "most_relevance"
        base = {
            "keywords": keywords,
            "source": "search_box",
            "order_by": order,
            "filters_source": "search_box",
        }
        if category_id:
            category_id = self.top_level_category(category_id)
            base["category_id"] = category_id
        if subcategory_ids:
            if not category_id:
                category_id = self.category_for_subcategory(subcategory_ids)
                if category_id is not None:
                    base["category_id"] = category_id
            base["subcategory_ids"] = subcategory_ids
        if condition:
            base["condition"] = condition
        if min_price is not None:
            base["min_sale_price"] = min_price
        if max_price is not None:
            base["max_sale_price"] = max_price
        if ship_only:
            base["is_shippable"] = "true"
        if latitude is not None and longitude is not None:
            base["latitude"] = latitude
            base["longitude"] = longitude
        components = self._request("GET", "/search/components", params=base)
        if not components.ok:
            raise ApiError(f"search failed ({components.status}): {components.error_reason or str(components.data)[:300]}")
        search_id = first(deep_find(components.data, "search_id"))
        if search_id:
            return self._search_sections(base, search_id, pages, delay)
        return self._search_legacy(keywords, category_id, order, latitude, longitude, condition=condition, subcategory_ids=subcategory_ids, min_price=min_price, max_price=max_price, ship_only=ship_only)

    def _search_sections(self, base: dict, search_id: str, pages: int, delay: float) -> list[Item]:
        all_items: list[Item] = []
        params = dict(base)
        params.update({"search_id": search_id, "section_type": "organic_search_results", "source": "deep_link"})
        next_page: Optional[str] = None
        budget = max(pages, 1)
        hops = 0
        while budget > 0:
            qp = dict(params)
            if next_page:
                qp["next_page"] = next_page
            resp = self._request("GET", "/search/section", params=qp)
            if not resp.ok:
                raise ApiError(f"search failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
            items = extract_items(resp.data)
            if items:
                all_items.extend(normalize_item(item) for item in items)
                budget -= 1
            next_page = first(deep_find(resp.data, "next_page"))
            if not next_page:
                break
            hops += 1
            if hops > 20:
                break
            if delay > 0:
                time.sleep(delay + random.uniform(0, 0.5))
        return all_items

    def _search_legacy(
        self,
        keywords: str,
        category_id: Optional[int],
        order: str,
        latitude: Optional[float],
        longitude: Optional[float],
        condition: Optional[str] = None,
        subcategory_ids: Optional[int] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        ship_only: Optional[bool] = None,
    ) -> list[Item]:
        params = {
            "keywords": keywords,
            "order_by": order,
            "filters_source": "search_box",
            "source": "search_box",
        }
        if category_id:
            params["category_ids"] = category_id
        if subcategory_ids:
            params["subcategory_ids"] = subcategory_ids
        if condition:
            params["condition"] = condition
        if min_price is not None:
            params["min_sale_price"] = min_price
        if max_price is not None:
            params["max_sale_price"] = max_price
        if ship_only:
            params["is_shippable"] = "true"
        if latitude is not None and longitude is not None:
            params["latitude"] = latitude
            params["longitude"] = longitude
            params["distance"] = 1000000
        resp = self._request("GET", "/general/search", params=params)
        if not resp.ok:
            raise ApiError(f"search failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        objs = data.get("search_objects", []) if isinstance(data, dict) else []
        return [normalize_item(obj) for obj in objs]

    def categories(self) -> list[dict[str, Any]]:
        """Return the search category tree (same source as the web sidebar).

        Each node is ``{"id": int, "name": str, "subcategories": [...]}``
        (recursive). Backed by ``GET /api/v3/categories?context=search``, the
        canonical menu the search page renders.
        """
        resp = self._request("GET", "/categories", params={"context": "search"})
        if not resp.ok:
            raise ApiError(f"categories failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        raw = data.get("categories", []) if isinstance(data, dict) else []

        def clean(node: dict) -> dict[str, Any]:
            return {
                "id": node.get("id"),
                "name": node.get("name", ""),
                "subcategories": [clean(s) for s in node.get("subcategories") or []],
            }

        return [clean(c) for c in raw]

    def category_for_subcategory(self, subcategory_id: int) -> Optional[int]:
        """Resolve the top-level category that a subcategory belongs to.

        The search API needs ``category_id`` (a top-level node) alongside
        ``subcategory_ids``; sending a deep ``subcategory_ids`` alone makes
        ``/search/components`` return 400. ``--subcat`` alone is therefore
        resolved by climbing the taxonomy tree to its root.
        """
        for node in self.categories():
            root = self._top_level_for(node, subcategory_id)
            if root is not None:
                return root
        return None

    def top_level_category(self, category_id: int) -> Optional[int]:
        """Normalize a category id to its top-level root.

        ``category_id`` in the search API must be a top-level node (100,
        12800, 24200...); passing a mid-level node (e.g. 24203, a child of
        24200) silently returns 0 results. Given any node id, returns its
        root when found in the tree, otherwise the id unchanged.
        """
        roots = self.categories()
        if any(node["id"] == category_id for node in roots):
            return category_id
        for node in roots:
            if self._top_level_for(node, category_id) is not None:
                return node["id"]
        return category_id

    def _top_level_for(self, node: dict[str, Any], subcategory_id: int) -> Optional[int]:
        if node["id"] == subcategory_id:
            return node["id"]
        for child in node.get("subcategories") or []:
            if self._top_level_for(child, subcategory_id) is not None:
                return node["id"]
        return None

    def item(self, item_id: str, authed: bool = False) -> Item:
        """Fetch a listing.

        Raises ``ApiError`` when the listing is not available, when the server
        answers with something that is not an item (e.g. an HTML anti-bot page)
        or when the payload does not contain the item at all.
        """
        resp = self._authed("GET", f"/items/{item_id}") if authed else self._request("GET", f"/items/{item_id}")
        if not resp.ok:
            raise ApiError(f"item {item_id} unavailable ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        if isinstance(data, str):
            if data.lstrip().startswith("<"):
                raise ApiError(f"item {item_id} unavailable: unexpected HTML response (anti-bot page?)")
            raise ApiError(f"item {item_id} unavailable: unexpected non-JSON response ({data[:120]!r})")
        if not isinstance(data, dict):
            raise ApiError(f"item {item_id} unavailable: unexpected payload ({type(data).__name__})")
        if not data.get("id"):
            raise ApiError(f"item {item_id} unavailable: response does not contain the item")
        return Item.from_raw(data)

    def seller(self, user_id: str) -> dict:
        """Fetch a seller's public profile (GET /users/{id}).

        Raises ``ApiError`` on HTTP errors or when the payload is not an
        object (an error page must never look like an empty profile).
        """
        resp = self._request("GET", f"/users/{user_id}")
        if not resp.ok:
            raise ApiError(f"seller {user_id} unavailable ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        if not isinstance(data, dict):
            raise ApiError(f"seller {user_id} unavailable: unexpected payload ({type(data).__name__})")
        return data

    def seller_reviews(self, user_id: str) -> list[dict]:
        """Fetch a seller's recent reviews (GET /users/{id}/reviews).

        Raises ``ApiError`` on HTTP errors or when the payload is not a list.
        """
        resp = self._request("GET", f"/users/{user_id}/reviews")
        if not resp.ok:
            raise ApiError(f"reviews of {user_id} unavailable ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        if not isinstance(data, list):
            raise ApiError(f"reviews of {user_id} unavailable: unexpected payload ({type(data).__name__})")
        return data

    def seller_stats(self, user_id: str) -> dict:
        """Fetch a seller's aggregated stats incl. rating average and review count (GET /users/{id}/stats).

        Returns the real values the web shows on an item page (~rating_average
        and counters[].reviews), unlike /reviews which only gives one page.

        Raises ``ApiError`` on HTTP errors or when the payload is not an
        object.
        """
        resp = self._request("GET", f"/users/{user_id}/stats")
        if not resp.ok:
            raise ApiError(f"stats of {user_id} unavailable ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        if not isinstance(data, dict):
            raise ApiError(f"stats of {user_id} unavailable: unexpected payload ({type(data).__name__})")
        return data

    def seller_extra_info(self, user_id: str) -> dict:
        """Fetch a seller's trust data (GET /api/v3/users/{id}/extra-info).

        Public payload: ``response_rate``, ``scoring_stars``,
        ``rating_average``, ``validations`` (email/mobile/level/...),
        ``activity_level``.
        """
        resp = self._request("GET", f"/users/{user_id}/extra-info")
        data = resp.data if isinstance(resp.data, dict) else {}
        return data

    def seller_items(self, user_id: str, pages: int = 1, max_items: int = 25) -> list[Item]:
        """Fetch a user's published items (GET /api/v3/users/{id}/items).

        Walks the ``meta.next`` cursor (``since`` waypoint) across ``pages``
        calls, the same pagination as the web profile grid. Returns
        ``Item`` objects with real prices/titles (no geolocation filter).
        """
        all_items: list[Item] = []
        since: Optional[str] = None
        for page_no in range(max(pages, 1)):
            params: dict[str, Any] = {"max_items": max_items}
            if page_no == 0:
                params["page"] = 0
            if since:
                params["since"] = since
            resp = self._request("GET", f"/users/{user_id}/items", params=params)
            if not resp.ok:
                raise ApiError(f"seller items failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
            data = resp.data if isinstance(resp.data, dict) else {}
            raw_items = data.get("data") or []
            if isinstance(raw_items, list):
                all_items.extend(normalize_item(it, fallback_user_id=user_id) for it in raw_items if isinstance(it, dict))
            meta = data.get("meta") or {}
            since = meta.get("next") if isinstance(meta, dict) else None
            if not since:
                break
        return all_items

    def other_items(self, item_id: str) -> list[Item]:
        """More active items from the same seller (GET /api/v3/items/{id}/seller/other-items).

        Feeds the web's "more items from this seller" rail on an item page.
        """
        resp = self._request("GET", f"/items/{item_id}/seller/other-items")
        if not resp.ok:
            raise ApiError(f"other items failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data if isinstance(resp.data, dict) else {}
        box = data.get("data") or {}
        raw = []
        if isinstance(box, dict):
            raw = box.get("items") or []
        return [normalize_item(it) for it in raw if isinstance(it, dict)]

    def seller_phone_number(self, item_id: str) -> str:
        """The seller's phone for an item (GET /api/v3/items/{id}/seller-phone-number).

        Returns ``""`` when unavailable (the web only unlocks it after a
        conversation is started or the seller shares it).
        """
        resp = self._authed("GET", f"/items/{item_id}/seller-phone-number")
        if not resp.ok:
            return ""
        data = resp.data if isinstance(resp.data, dict) else {}
        return str(data.get("phone_number") or "")

    def item_actions_allowed(self, item_id: str) -> list[dict[str, Any]]:
        """Which actions the API allows on an item (GET /api/v3/items/{id}/actions-allowed).

        Returns the ``{"type", "allowed", "cause"}`` entries (e.g.
        inactivate/reserve/delete) that drive the web's action menus.
        """
        resp = self._request("GET", f"/items/{item_id}/actions-allowed")
        data = resp.data if isinstance(resp.data, list) else []
        return [dict(a) for a in data if isinstance(a, dict)]

    def suggest(self, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
        """Autocomplete suggestions for a search keyword (GET /api/v3/suggesters/nonlocated).

        Returns ``[{"text", "highlight", "subtitle"}]`` for the top
        suggestions, the same data the search box dropdown shows.
        """
        resp = self._request("GET", "/suggesters/nonlocated", params={"keyword": keyword})
        if not resp.ok:
            raise ApiError(f"suggest failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
        data = resp.data
        raw = data.get("suggestions", []) if isinstance(data, dict) else []
        out: list[dict[str, Any]] = []
        for s in raw:
            if not isinstance(s, dict):
                continue
            sug = (s.get("regular_suggestion") or {}).get("suggestion") or {}
            if not isinstance(sug, dict):
                continue
            out.append(
                {
                    "text": str(sug.get("text") or ""),
                    "highlight": str(sug.get("highlight") or ""),
                    "subtitle": str(sug.get("subtitle") or ""),
                }
            )
        return out[: max(limit, 1)]

    def download_images(self, item: Item, dest: str = "images") -> list[str]:
        """Download an item's images into ``dest`` and return the saved file paths.

        Uses each URL in ``item.images``; filenames are derived from the item id
        plus an index (``{item_id}_1.jpg``, ...). The directory is created if it
        does not exist. Uses only the standard library.
        """
        images = list(item.images)
        if not images:
            return []
        out_dir = Path(dest)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for i, url in enumerate(images, start=1):
            ext = Path(urllib.parse.urlparse(url).path).suffix or ".jpg"
            filename = f"{item.id}_{i}{ext}"
            target = out_dir / filename
            try:
                with urllib.request.urlopen(url, timeout=30) as resp, open(target, "wb") as f:
                    f.write(resp.read())
                saved.append(str(target))
            except Exception:
                continue
        return saved

    def send_offer(self, item_id: str, amount: float, message: Optional[str] = None) -> Response:
        """POST the delivery/buyer offer endpoint (the web app's real offer send).

        The legacy /items/{id}/offers endpoint is dead (404). The current flow
        posts to /api/v3/delivery/buyer/offers with a client-generated offer_id
        (uuid) and the item's price currency.
        """
        body: dict[str, Any] = {
            "offer_id": str(uuid.uuid4()),
            "offer_price_amount": price_amount(amount),
            "offer_price_currency": self._item_currency(item_id),
            "item_ids": [item_id],
        }
        if message:
            body["message"] = message
        resp = self._authed("POST", "/delivery/buyer/offers", body=body)
        if not resp.ok and message and (resp.status == 400 or resp.status == 422):
            del body["message"]
            resp = self._authed("POST", "/delivery/buyer/offers", body=body)
        return resp

    def offer_response(self, offer_id: str, action: str) -> Response:
        """Accept or decline an offer (e.g. a seller's counteroffer).

        Maps to PATCH /api/v3/delivery/offers/{id}/status with the status the
        web app uses (ACCEPTED / DECLINED).
        """
        status_map = {"accept": "ACCEPTED", "decline": "DECLINED"}
        status = status_map.get(action)
        if not status:
            raise ApiError(f"invalid offer action: {action!r} (use accept or decline)")
        return self._authed(
            "PATCH", f"/delivery/offers/{offer_id}/status", body={"status": status}
        )

    def counter_offer(self, offer_id: str, amount: float, currency: str = "EUR") -> Response:
        """Replace a pending offer's price with a new counteroffer.

        Maps to PATCH /api/v3/delivery/offers/{id}/offer-price (the web app's
        "hacer una nueva oferta" flow on an open offer).
        """
        return self._authed(
            "PATCH",
            f"/delivery/offers/{offer_id}/offer-price",
            body={
                "offer_price_amount": price_amount(amount),
                "offer_price_currency": currency,
            },
        )

    def offer_details(self, offer_id: str) -> Response:
        """Fetch the Offer Tracking Status payload for an offer.

        Only meaningful when WE are the party waiting for reply (the modal
        payload is generated for the current responder).  The endpoint lives
        under the BFF prefix (``/bff/delivery/offer-details``), not under
        ``/api/v3``.
        """
        return self._bff("GET", "/bff/delivery/offer-details", params={"offer_id": offer_id})

    def favorite(self, item_id: str, favorited: bool = True) -> Response:
        """Toggle an item's favorite status.

        Uses the real web endpoint ``PUT /api/v3/items/{id}/favorite``
        with body ``{"favorited": <bool>}``.  Returns 204 on success.
        """
        return self._authed("PUT", f"/items/{item_id}/favorite", body={"favorited": favorited})

    def favorite_lists(self, pages: int = 1) -> list[Item]:
        """List the items you favorited (GET /api/v3/favorite-lists).

        Walks ``meta.next`` (the ``since`` cursor) the way the favorites page
        does. Returns normalized ``Item`` objects so the list can be fed to
        the same printers/deal logic as ``search``.
        """
        all_items: list[Item] = []
        since: Optional[str] = None
        for _ in range(max(pages, 1)):
            params: dict[str, Any] = {}
            if since:
                params["since"] = since
            resp = self._authed("GET", "/favorite-lists", params=params)
            if not resp.ok:
                raise ApiError(f"favorites failed ({resp.status}): {resp.error_reason or str(resp.data)[:300]}")
            data = resp.data if isinstance(resp.data, dict) else {}
            raw = data.get("data") or []
            if isinstance(raw, list):
                all_items.extend(normalize_item(it) for it in raw if isinstance(it, dict))
            meta = data.get("meta") or {}
            since = meta.get("next") if isinstance(meta, dict) else None
            if not since:
                break
        return all_items

    def favorite_list_add(self, list_id: str, item_id: str) -> Response:
        """Add an item to a favorite list (POST /api/v3/favorite-lists/{id}/items)."""
        return self._authed(
            "POST",
            f"/favorite-lists/{list_id}/items",
            body={"items": [{"id": item_id, "is_favorited": True}]},
        )

    def favorite_list_remove(self, list_id: str, item_id: str) -> Response:
        """Remove an item from a favorite list (DELETE /api/v3/favorite-lists/{id}/items/{itemId})."""
        return self._authed("DELETE", f"/favorite-lists/{list_id}/items/{item_id}")

    def purchases(self, tab: str = "ongoing") -> Response:
        """Fetch the purchases center (SDUI card tree).

        ``tab`` is ``"ongoing"`` (default) or ``"finished"``.  Hits
        ``GET /sdui/purchases/{tab}`` (root, not under /api/v3).  The
        transactions live in the response's ``list_item_transaction`` nodes;
        flatten them with ``parse_sdui_transactions``.
        """
        return self._bff("GET", f"/sdui/purchases/{tab}")

    def selling(self, tab: str = "ongoing") -> Response:
        """Fetch the selling center (SDUI card tree).

        Same contract as ``purchases`` but for ``GET /sdui/selling/{tab}``.
        """
        return self._bff("GET", f"/sdui/selling/{tab}")

    def reserve(self, item_id: str, reserved: bool = True) -> Response:
        """Reserve/unreserve an owned item via ``PUT /api/v3/items/{id}/reserve``.

        Body ``{"reserved": <bool>}``; returns 204 on success.
        """
        return self._authed("PUT", f"/items/{item_id}/reserve", body={"reserved": reserved})

    def mark_sold(self, item_id: str, sold: bool = True) -> Response:
        """Mark an owned item as sold (or unsold) via ``PUT /api/v3/items/{id}/sold``.

        The web sends an empty body to mark as sold; ``{"sold": false}``
        unmarks it (no UI button exists for un-selling, but the API accepts it).
        Returns 204 on success.
        """
        body = {} if sold else {"sold": False}
        return self._authed("PUT", f"/items/{item_id}/sold", body=body)

    def edit_item(
        self,
        item_id: str,
        price: Optional[float] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        condition: Optional[str] = None,
        brand: Optional[str] = None,
        model: Optional[str] = None,
        color: Optional[str] = None,
        category_leaf_id: Optional[str] = None,
        max_weight_kg: Optional[int] = None,
    ) -> Response:
        """Edit an owned, published item (what the web does on "Actualizar").

        Uses ``PUT /api/v3/items/{id}`` with the ``Accept:
        application/vnd.upload-v2+json`` header (which selects the edit
        handler; without it the API answers 404).  The body is built by
        merging the requested changes over the item's current state fetched
        from ``GET /items/{id}``, so every editable attribute is always sent
        (same as the web form).

        Returns 204 on success (the ``PUT`` answer, as the API sends none).

        The price keeps its cents (``19.95`` is sent as ``19.95``); invalid
        prices (non numeric, ``nan``/``inf``, negative, out of range or with
        more than two decimals) are rejected with :class:`InvalidPrice` before
        anything is sent.

        The edit **preserves the listing**: category, condition, shipping,
        weight and location are taken from the item itself, never guessed.
        When the current state cannot be inferred (no category, no condition,
        no location...) the edit is refused with an explicit error instead of
        applying a default that would change the listing.
        """
        new_price = price_amount(price) if price is not None else None
        # Read the current state with a PLAIN GET. The versioned edit shape
        # (``Accept: application/vnd.upload-v2+json``) now answers 406, while a
        # plain GET returns 200 but nests brand/model/color/condition under
        # ``type_attributes`` and the category under ``taxonomy``; the mapping
        # below reads either shape.
        current = self._authed("GET", f"/items/{item_id}")
        if not current.ok:
            return Response(current.status, current.data, False, current.unauthorized)
        raw = current.data if isinstance(current.data, dict) else {}

        def text(v) -> str:
            if isinstance(v, dict):
                return v.get("original") or v.get("value") or ""
            return v or ""

        type_attrs = raw.get("type_attributes") if isinstance(raw.get("type_attributes"), dict) else {}

        def attr(key: str) -> str:
            """Read an item attribute from the top level or from
            ``type_attributes`` (the plain GET nests brand/model/color/
            condition there as ``{"value": ...}``)."""
            top = text(raw.get(key))
            if top:
                return top
            node = type_attrs.get(key)
            if isinstance(node, dict):
                return node.get("value") or ""
            return text(node)

        def refuse(message: str) -> Response:
            return Response(0, {"error_message": message}, False, False)

        price_raw = raw.get("price") or {}
        if isinstance(price_raw, dict) and "cash" in price_raw:
            price_raw = price_raw.get("cash") or {}
        if not isinstance(price_raw, dict):
            price_raw = {}
        if new_price is None:
            current_amount = price_raw.get("amount")
            if current_amount is None:
                return refuse(
                    "cannot infer the current price of the item; pass price=... to set it"
                )
            new_price = price_amount(current_amount)

        leaf = category_leaf_id if category_leaf_id is not None else text(raw.get("category_leaf_id"))
        if not leaf:
            # plain GET: the leaf is the last node of the taxonomy chain.
            taxonomy = raw.get("taxonomy")
            if isinstance(taxonomy, list) and taxonomy and isinstance(taxonomy[-1], dict):
                leaf = str(taxonomy[-1].get("id") or "")
        if not leaf:
            return refuse(
                "cannot infer the current category_leaf_id of the item; "
                "pass category_leaf_id=... to set it"
            )

        cond = condition if condition is not None else attr("condition")
        if not cond:
            return refuse(
                "cannot infer the current condition of the item; pass condition=... to set it"
            )

        shipping = raw.get("shipping") if isinstance(raw.get("shipping"), dict) else {}
        # shipping is only kept as it is: it is never switched on by an edit.
        allowed_by_user = shipping.get("user_allows_shipping", shipping.get("allowed_by_user"))
        if not isinstance(allowed_by_user, bool):
            return refuse("cannot infer whether the user allows shipping; refusing to change the listing")

        weight = max_weight_kg if max_weight_kg is not None else shipping.get("max_weight_kg")
        if weight is None:
            up = type_attrs.get("up_to_kg")
            weight = up.get("value") if isinstance(up, dict) else up
        if allowed_by_user and weight is None:
            return refuse(
                "cannot infer the current shipping weight of the item; "
                "pass max_weight_kg=... to set it"
            )
        if weight is not None:
            try:
                parsed_weight = Decimal(str(weight))
            except (InvalidOperation, ValueError):
                return refuse("invalid shipping weight; refusing to change the listing")
            if not parsed_weight.is_finite() or parsed_weight <= 0 or parsed_weight != parsed_weight.to_integral_value():
                return refuse("shipping weight must be a positive whole number of kilograms")
            weight = int(parsed_weight)

        cost_cfg = None
        if allowed_by_user:
            # the cost configuration is only meaningful for a shipping item:
            # it is account specific and must never be guessed.
            cost_cfg = shipping.get("cost_configuration_id") or self._cost_configuration_id()
            if not cost_cfg:
                return refuse(
                    "the item ships but its delivery cost_configuration_id cannot be inferred; "
                    "it is account specific and must never be guessed"
                )

        loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        latitude = loc.get("latitude", loc.get("lat"))
        longitude = loc.get("longitude", loc.get("lon"))
        if latitude is None or longitude is None:
            return refuse(
                "cannot infer the location of the item; refusing to move it to another place"
            )
        approximated = bool(loc.get("approximated", False))

        attributes = {
            "brand": brand if brand is not None else attr("brand"),
            "model": model if model is not None else attr("model"),
            "color": color if color is not None else attr("color"),
            "title": title if title is not None else text(raw.get("title")),
            "description": description if description is not None else text(raw.get("description")),
            "condition": cond,
            "price_amount": new_price,
        }

        images = raw.get("images")
        if not isinstance(images, list) or not images or any(not isinstance(img, dict) or not img.get("id") for img in images):
            return refuse("cannot infer all current picture IDs; refusing to change the listing")
        pictures = [{"id": img["id"], "order": i} for i, img in enumerate(images)]

        delivery: dict[str, Any] = {"allowed_by_user": allowed_by_user}
        if weight is not None:
            delivery["max_weight_kg"] = weight
        if cost_cfg:
            delivery["cost_configuration_id"] = cost_cfg

        item = {
            "attributes": attributes,
            "category_leaf_id": leaf,
            "apply_discount": False,
            "location": {
                "latitude": latitude,
                "longitude": longitude,
                "approximated": approximated,
            },
            "delivery": delivery,
            "pictures": pictures,
        }

        headers = {
            "Accept": "application/vnd.upload-v2+json",
            "Content-Type": "application/json",
            "X-AppVersion": "826800",
        }
        return self._authed("PUT", f"/items/{item_id}", body=item, extra_headers=headers)

    def delete_item(self, item_id: str) -> Response:
        """Permanently delete an owned listing via ``DELETE /items/{id}``.

        Unlike ``reserve`` (holds it briefly) or ``mark_sold`` (removes it
        from the marketplace but keeps it in your sales history), this
        removes the listing for good. Returns 204 on success.
        """
        return self._authed("DELETE", f"/items/{item_id}")

    def inactivate_item(self, item_id: str) -> Response:
        """Pause an owned item: it leaves the marketplace but stays in your catalog.

        Exactly the web's "Pausar" action: ``PUT /api/v3/items/{id}/inactivate``
        with an empty body. To put it back online, edit it (``PUT /items/{id}``
        revives paused items).
        """
        return self._authed("PUT", f"/items/{item_id}/inactivate")

    def mark_viewed(self, item_id: str) -> Response:
        """Register that you opened an item page (PUT /api/v3/items/{id}/view).

        The tracking call the web fires on every item detail view (it feeds the
        price-drop and "te interesa" features).
        """
        return self._request("PUT", f"/items/{item_id}/view")

    def _item_currency(self, item_id: str) -> str:
        return self.item(item_id).currency

    def make_offer_info(self, item_id: str) -> Response:
        """Fetch the offer editor config (price, max_offer_percentage, restriction)."""
        return self._bff("GET", "/bff/delivery/make-an-offer", params={"item_id": item_id})

    def _bff(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> Response:
        full = f"{API_ROOT}{path}"
        if params:
            full += ("&" if "?" in full else "?") + urllib.parse.urlencode(params)
        retry_safe = method.upper() in ("GET", "HEAD") and body is None
        status, data, _ = http_request(
            method, full, body=body, headers=self._headers(auth=True), retry_safe=retry_safe
        )
        if status == 401 and self._refresh_access_token():
            status, data, _ = http_request(
                method, full, body=body, headers=self._headers(auth=True), retry_safe=retry_safe
            )
        return Response(status, data, 200 <= status < 300, status == 401)

    def ensure_conversation(self, item_hash: str) -> Response:
        """Create (or fetch the existing) conversation for an item. Idempotent."""
        return self._authed("POST", "/instant-messaging/conversation", body={"item_hash_id": item_hash})

    def publish(
        self,
        photo_paths: list[str],
        title: str,
        price: float,
        category_leaf_id: str = "10088",
        condition: str = "good",
        description: str = "",
        brand: str = "",
        model: str = "",
        color: str = "",
        max_weight_kg: int = 1,
        cost_configuration_id: Optional[str] = None,
        shipping_allowed: bool = True,
    ) -> Response:
        """Publish an item through the pure API (no browser).

        Mirrors the exact body the web wizard POSTs to ``/api/v3/items``:
        a ``multipart/form-data`` request with two fields — ``image`` (the
        cover photo bytes) and ``item`` (a JSON document with the attributes,
        category leaf, seller location, delivery config and a fresh
        ``upload_id``).

        When ``photo_paths`` has more than one entry, the cover is sent as the
        ``image`` of the final POST and each remaining photo is linked to the
        created item with ``POST /api/v3/items/<id>/picture2`` (multipart
        ``image`` with the full bytes + an ``order`` field). This is the pure-API
        path that reproduces the browser's multi-photo publish (resolved
        2026-09-03; see ``docs/multi-photo-upload.md``).

        .. note:: Wallapop's POST ``/api/v3/items`` rejects the body with
           ``400 INVALID_ATTRIBUTE`` when ``color`` is empty (verified 2026-09-03
           on several categories). Pass a ``color`` value (the web wizard fills
           it via suggested-item-data, but the pure API POST must include it);
           ``brand``/``model`` may stay empty.

        The seller location is taken from ``GET /users/me``; the delivery
        ``cost_configuration_id`` is the account's constant (returned by
        published items' ``shipping``).  It is *account specific*: when it
        cannot be inferred the publish is refused with a clear error instead
        of guessing a value (pass ``cost_configuration_id=`` to provide it).

        Set ``shipping_allowed=False`` for an in-person-only listing. Its
        delivery payload contains only ``allowed_by_user: false``; shipping
        weight and cost configuration are ignored, and no existing listings
        are consulted to infer a shipping configuration.

        On success the response body is ``{"id":"<item id>","flags":{}}``
        (``Response.data`` carries the parsed JSON).  Any ``2xx`` status is
        accepted; a ``2xx`` answer *without* an item id is reported as an
        unknown creation (the listing may exist) and never as a success.

        The price keeps its cents (``19.95`` is published as ``19.95``); an
        invalid price (non numeric, ``nan``/``inf``, negative, out of range or
        with more than two decimals) is rejected with :class:`InvalidPrice`
        before anything is sent.

        If an *extra* photo cannot be uploaded the item **is** already
        created: the response is then a partial result (``Response.partial``
        is ``True``) whose data carries the created ``item_id``, how many
        photos were uploaded (``photos_completed``), which photo failed
        (``failed_photo_index`` / ``failed_photo_order``) and how to retry it
        (``retry``).  The listing is never reported as non existent.  The
        same holds when the upload raises: the id of the created item is
        kept and the ``outcome`` says whether the photo was surely not sent
        (``not_sent``) or maybe sent (``unknown``).
        """
        amount = price_amount(price)
        if not photo_paths:
            return Response(0, {"error_message": "at least one photo is required"}, False, False)

        me = self._authed("GET", "/users/me")
        loc = {}
        if me.ok and isinstance(me.data, dict):
            loc = me.data.get("location") or {}
        latitude = loc.get("approximated_latitude")
        longitude = loc.get("approximated_longitude")
        approximated = bool(loc.get("approximated_location"))
        if latitude is None or longitude is None:
            return Response(me.status, {"error_message": "no location in /users/me; publish requires geolocation"}, False, False)

        cost_cfg = (cost_configuration_id or self._cost_configuration_id()) if shipping_allowed else None
        if shipping_allowed and not cost_cfg:
            return Response(
                0,
                {
                    "error_message": (
                        "cannot infer the delivery cost_configuration_id of this account: "
                        "it is account specific and must never be guessed. Pass "
                        "cost_configuration_id=... (the shipping.cost_configuration_id of "
                        "any of your published items)."
                    ),
                },
                False,
                False,
            )

        upload_id = str(uuid.uuid4())

        attributes = {
            "suggested_data_banner": None,
            "brand": brand,
            "model": model,
            "title": title,
            "description": description,
            "condition": condition,
            "price_amount": amount,
        }
        # Only send ``color`` when one is given. Categories with no color
        # attribute (e.g. mice, leaf 10311) answer 400 INVALID_ATTRIBUTE when
        # ``color`` is present at all, even as an empty string; for categories
        # that require a color, pass a non-empty value.
        if color:
            attributes["color"] = color
        delivery: dict[str, Any] = {"allowed_by_user": shipping_allowed}
        if shipping_allowed:
            delivery.update(max_weight_kg=max_weight_kg, cost_configuration_id=cost_cfg)
        item = {
            "attributes": attributes,
            "category_leaf_id": category_leaf_id,
            "apply_discount": False,
            "location": {
                "latitude": latitude,
                "longitude": longitude,
                "approximated": approximated,
            },
            "delivery": delivery,
            "upload_id": upload_id,
        }

        with open(photo_paths[0], "rb") as fh:
            photo = fh.read()
        boundary = "----publishFormBoundary" + uuid.uuid4().hex[:14]
        item_json = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="image"; filename="foto.jpg"\r\n',
            b"Content-Type: image/jpeg\r\n\r\n",
            photo, b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="item"\r\n\r\n',
            item_json, b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])

        headers = self._headers(auth=True)
        headers.update({
            "Accept": "application/vnd.upload-v2+json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
        try:
            status, payload = http_request_raw("POST", f"{API_BASE}/items", raw_body=body, headers=headers)
            if status == 401 and self._refresh_access_token():
                headers = self._headers(auth=True)
                headers.update({
                    "Accept": "application/vnd.upload-v2+json",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                })
                status, payload = http_request_raw("POST", f"{API_BASE}/items", raw_body=body, headers=headers)
        except ApiError as exc:
            # the listing may or may not exist: say so instead of losing it
            return Response(
                0,
                {
                    "error_message": f"publish POST did not complete: {exc}",
                    "created": "unknown",
                    "outcome": getattr(exc, "outcome", "unknown"),
                    "photos_total": len(photo_paths),
                },
                False,
                False,
            )
        if not (200 <= status < 300):
            return Response(status, {"error_message": f"publish POST failed with status {status}"}, False, status == 401)

        item_id = payload.get("id") if isinstance(payload, dict) else None
        if not item_id:
            return Response(
                status,
                {
                    "error_message": (
                        f"publish POST answered {status} but the response carries no item id; "
                        "the listing may or may not have been created: check your listings "
                        "before publishing it again"
                    ),
                    "created": "unknown",
                    "outcome": "unknown",
                    "photos_total": len(photo_paths),
                },
                False,
                False,
            )

        if len(photo_paths) > 1:
            for index, extra_photo in enumerate(photo_paths[1:]):
                order = index + 2
                try:
                    pic_resp = self._add_picture(item_id, extra_photo, order)
                except ApiError as exc:
                    # the item exists: keep its id and say what is known
                    return self._partial_publish(
                        item_id,
                        payload,
                        photo_paths,
                        index,
                        extra_photo,
                        order,
                        status=0,
                        error=f"picture {order} failed: {exc}",
                        outcome=getattr(exc, "outcome", "unknown"),
                    )
                except OSError as exc:
                    # the photo could not even be read: it was surely not sent
                    return self._partial_publish(
                        item_id,
                        payload,
                        photo_paths,
                        index,
                        extra_photo,
                        order,
                        status=0,
                        error=f"picture {order} could not be read: {exc}",
                        outcome="not_sent",
                    )
                if not pic_resp.ok:
                    return self._partial_publish(
                        item_id,
                        payload,
                        photo_paths,
                        index,
                        extra_photo,
                        order,
                        status=pic_resp.status,
                        error=(
                            f"picture {order} failed ({pic_resp.status}): "
                            f"{pic_resp.error_reason or str(pic_resp.data)[:300]}"
                        ),
                        outcome="unknown" if pic_resp.status >= 500 else "failed",
                    )

        return Response(200, payload or {}, True, False)

    def _partial_publish(
        self,
        item_id: str,
        payload: Any,
        photo_paths: list[str],
        index: int,
        extra_photo: str,
        order: int,
        status: int,
        error: str,
        outcome: str,
    ) -> Response:
        """Structured "the item exists but a photo did not" result."""
        data = dict(payload) if isinstance(payload, dict) else {}
        data.update(
            {
                "id": item_id,
                "item_id": item_id,
                "created": True,
                "partial": True,
                "error_message": error,
                "outcome": outcome,
                "photos_total": len(photo_paths),
                "photos_completed": index + 1,
                "failed_photo_index": index + 1,
                "failed_photo_order": order,
                "failed_photo_path": extra_photo,
                "retry": (
                    f"item {item_id} was created; "
                    + ("inspect the listing before uploading this photo again; its upload outcome is unknown" if outcome == "unknown" else
                       f"upload the missing photo with POST /api/v3/items/{item_id}/picture2 (order={order})")
                ),
            }
        )
        return Response(status, data, False, False, partial=True)

    def _add_picture(self, item_id: str, photo_path: str, order: int) -> Response:
        """Link a photo to an already-created item via picture2.

        ``POST /api/v3/items/<id>/picture2`` with a multipart body of ``image``
        (the full JPEG bytes) and ``order`` (its position, starting at 2, as a
        string). Returns 200 with a short photo id. This is how the browser adds
        the extra photos after the item is created (resolved 2026-09-03).
        """
        with open(photo_path, "rb") as fh:
            photo = fh.read()
        boundary = "----pictureBoundary" + uuid.uuid4().hex[:16]
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="image"; filename="foto.jpg"\r\n',
            b"Content-Type: image/jpeg\r\n\r\n",
            photo, b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="order"\r\n\r\n',
            str(order).encode(), b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        headers = self._headers(auth=True)
        headers.update({
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
        status, payload = http_request_raw(
            "POST", f"{API_BASE}/items/{item_id}/picture2",
            raw_body=body, headers=headers, timeout=60,
        )
        if status == 401 and self._refresh_access_token():
            headers = self._headers(auth=True)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            status, payload = http_request_raw(
                "POST", f"{API_BASE}/items/{item_id}/picture2",
                raw_body=body, headers=headers, timeout=60,
            )
        if status == 200:
            return Response(status, payload or {}, True, False)
        detail = payload if isinstance(payload, str) else str(payload)[:300]
        return Response(
            status,
            {"error_message": f"picture2 failed with status {status}", "details": detail},
            False,
            status == 401,
        )

    def _cost_configuration_id(self) -> Optional[str]:
        """Our account's delivery cost configuration UUID, if it can be inferred.

        The value is **account specific**: it is read from the ``shipping``
        block of any item this account has published.  It is never hardcoded:
        when it cannot be inferred ``None`` is returned and the caller has to
        decide what to do (see :meth:`publish` and :meth:`edit_item`).
        """
        uid = self.user.get("id") or ""
        if not uid:
            me = self._authed("GET", "/users/me")
            if me.ok and isinstance(me.data, dict):
                uid = me.data.get("id") or ""
        if uid:
            items = self._authed("GET", f"/users/{uid}/items", params={"page": 0, "max_items": 25})
            if items.ok and isinstance(items.data, dict):
                for it in items.data.get("data") or []:
                    shipping = (it or {}).get("shipping") or {}
                    cfg = shipping.get("cost_configuration_id")
                    if cfg:
                        return str(cfg)
        return None

    def conversation_thread(self, conversation_id: str) -> Response:
        """Fetch the full message thread (messages, item, with_user) for a conversation."""
        return self._bff("GET", f"/bff/messaging/conversation/{conversation_id}")

    def inbox(self) -> Response:
        """Fetch the inbox: up to 30 conversations with their latest messages."""
        return self._bff("GET", "/bff/messaging/inbox", params={"page_size": 30, "max_messages": 30})

    def unread_count(self) -> Response:
        """Total unread message counter (instant messaging)."""
        return self._authed("GET", "/instant-messaging/messages/unread")

    def save_search(self, params: dict) -> str:
        """Save a favorite search and return its id.

        POSTs the search filter params (``keywords``, ``category_id``, prices,
        coordinates, ...) to ``/api/v3/searchalerts/savedsearch``; the server
        answers 201 with the new id (a UUID) in the ``Location`` header and no
        body.
        """
        headers = self._headers(auth=True)
        status, payload, _, resp_headers = http_request(
            "POST",
            f"{API_BASE}/searchalerts/savedsearch",
            params=params,
            headers=headers,
            include_headers=True,
        )
        if status == 401 and self.refresh_web():
            headers = self._headers(auth=True)
            status, payload, _, resp_headers = http_request(
                "POST",
                f"{API_BASE}/searchalerts/savedsearch",
                params=params,
                headers=headers,
                include_headers=True,
            )
        if status not in (200, 201, 204):
            if isinstance(payload, dict):
                status_block = payload.get("status")
                message = status_block.get("message") if isinstance(status_block, dict) else payload.get("message", "")
            else:
                message = payload or ""
            raise ApiError(f"save search failed ({status}): {message}")
        search_id = _saved_search_id_from_location((resp_headers or {}).get("Location") or "")
        if search_id is None:
            raise ApiError(f"save search accepted but no id in Location header: {resp_headers.get('Location')!r}")
        return search_id

    def saved_search_exists(self, params: dict) -> Optional[str]:
        """Return the id of a saved search matching these filters, or None.

        HEAD ``/api/v3/searchalerts/savedsearch/exists`` answers 200 with the
        id in the ``Location`` header when the search is saved and 404
        otherwise.
        """
        headers = self._headers(auth=True)
        status, _, _, resp_headers = http_request(
            "HEAD",
            f"{API_BASE}/searchalerts/savedsearch/exists",
            params=params,
            headers=headers,
            include_headers=True,
        )
        if status == 404:
            return None
        if status == 401 and self.refresh_web():
            headers = self._headers(auth=True)
            status, _, _, resp_headers = http_request(
                "HEAD",
                f"{API_BASE}/searchalerts/savedsearch/exists",
                params=params,
                headers=headers,
                include_headers=True,
            )
        if status not in (200, 201, 204):
            raise ApiError(f"check saved search failed ({status})")
        return _saved_search_id_from_location((resp_headers or {}).get("Location") or "")

    def saved_search_hits(self) -> int:
        """Total new-listing counter across your saved searches (GET .../alerts/total-hits).

        This is the badge number the web shows next to the saved searches ("búsquedas guardadas") menu.
        """
        resp = self._authed("GET", "/searchalerts/savedsearch/alerts/total-hits")
        if not resp.ok:
            return 0
        data = resp.data if isinstance(resp.data, dict) else {}
        hits = data.get("hits")
        return int(hits) if isinstance(hits, (int, float)) else 0

    def remove_saved_search(self, search_id: str) -> Response:
        """Delete a saved search by id (DELETE /api/v3/searchalerts/savedsearch/{id})."""
        return self._authed("DELETE", f"/searchalerts/savedsearch/{search_id}")

    def send_message(
        self,
        item_hash: str,
        message: str,
        report: Optional[dict] = None,
        allow_browser_fallback: bool = False,
    ) -> Response:
        """Send a chat message to an item's seller.

        The PubNub REST publish (the way the web app delivers chat messages)
        is used first.  The browser flow is **never** started automatically:
        it only runs when the caller explicitly asks for it with
        ``allow_browser_fallback=True`` *and* the message was surely not sent
        yet (see below).

        Idempotent guard: no send when the exact text is already our last
        message in the thread (the thread is ordered by timestamp before the
        check, so an out-of-order payload cannot fool it).

        When ``report`` is given it is filled with ``{"path": "rest"|
        "browser"|"skipped"|"unknown"|"not_sent", "skipped": bool,
        "browser_s": float, "delivery": "confirmed"|"skipped"|"unknown"|
        "not_sent"}``.

        Delivery outcomes:

        - the PubNub acknowledgement confirms the message: a compact answer
          (``conversation_id`` + the peer) is returned right away, without
          waiting for the message to show up in the thread.
        - the publish was attempted but **not acknowledged** (a negative or
          missing ack, a ``5xx``, a truncated/non-JSON answer): the outcome is
          **unknown**.  The message is *not* retransmitted (not even with the
          browser fallback): a publish without a valid ack is no proof that
          the message was not delivered, so the answer says so and asks to
          inspect the conversation before sending it again.
        - the publish was **never attempted** (a prerequisite failed: no
          channel, no IM token...) or the transport raised a typed
          ``NotSentError``: the message was surely not sent, so the browser
          fallback is allowed (when it was explicitly requested) and the
          message can safely be sent again.
        """
        resp = self.ensure_conversation(item_hash)
        if not resp.ok:
            return resp
        conv_id = first(deep_find(resp.data, "conversation_id"))
        if not conv_id:
            return Response(resp.status, resp.data, False, resp.unauthorized)
        thread = self.conversation_thread(conv_id)
        thread_data = thread.data if isinstance(thread.data, dict) else {}
        msgs = (((thread_data.get("messages") or {}).get("messages")) or [])
        # the payload is not guaranteed to be ordered: compare against the
        # *last* message we sent, by timestamp.
        ordered = sorted(msgs, key=lambda m: m.get("timestamp") or 0)
        skipped = False
        for m in reversed(ordered):
            if m.get("from_self"):
                skipped = str(m.get("text") or "") == message
                break
        if skipped:
            if report is not None:
                report["path"] = "skipped"
                report["skipped"] = True
                report["delivery"] = "skipped"
            return self.conversation_thread(conv_id)

        channel = thread_data.get("channel") or ""
        to_user = (thread_data.get("with_user") or {}).get("hash") or ""
        if channel and to_user:
            try:
                tok = self.im_token()
            except ApiError:
                # a prerequisite failed: nothing was sent, so the browser
                # fallback is still a safe retransmission.
                tok = None
            if tok is not None and tok.ok:
                try:
                    ack = self.pubnub_publish(channel, message, to_user, conv_id, tok.data.get("token", ""))
                except ApiError as exc:
                    # the transport failed: the message may already be on its
                    # way, so it must not be sent again.
                    if not isinstance(exc, NotSentError):
                        return self._unknown_delivery(conv_id, message, report, str(exc))
                    # a typed NotSentError: the request never left the client,
                    # so the browser fallback is still safe.
                else:
                    if ack:
                        if report is not None:
                            report["path"] = "rest"
                            report["skipped"] = False
                            report["delivery"] = "confirmed"
                        return self._confirmed_delivery(conv_id, message, thread_data, channel)
                    # the publish was attempted but not acknowledged: the
                    # message may or may not have been delivered.  It must
                    # NOT be retransmitted (not even through the browser).
                    return self._unknown_delivery(
                        conv_id,
                        message,
                        report,
                        "PubNub did not acknowledge the publish (it may still have been delivered)",
                    )

        if allow_browser_fallback:
            # the browser fallback is only allowed when the message was surely
            # NOT sent: a failed prerequisite (the publish was never
            # attempted) or a typed NotSentError from the transport.
            t0 = time.monotonic()
            if not send_chat_text(item_hash, message):
                return Response(
                    0,
                    {"error_message": "chat delivery failed (browser step)", "delivery": "failed"},
                    False,
                    False,
                )
            browser_s = time.monotonic() - t0
            if report is not None:
                report["path"] = "browser"
                report["skipped"] = False
                report["browser_s"] = browser_s
                report["delivery"] = "confirmed"
            return self._confirmed_delivery(conv_id, message, thread_data, channel)

        if report is not None:
            report["path"] = "not_sent"
            report["skipped"] = False
            report["delivery"] = "not_sent"
        return Response(
            0,
            {
                "error_message": (
                    "chat message was not sent (the publish prerequisites failed); "
                    "it is safe to send it again"
                ),
                "delivery": "not_sent",
                "outcome": "not_sent",
                "conversation_id": conv_id,
            },
            False,
            False,
        )

    def reply_to_conversation(
        self,
        conversation_id: str,
        message: str,
        report: Optional[dict] = None,
    ) -> Response:
        """Reply in an existing conversation by its id (seller-safe).

        Unlike :meth:`send_message`, this does **not** call
        ``ensure_conversation`` (which POSTs a buyer→seller conversation and is
        rejected with ``403`` on *your own* item). It publishes straight to the
        conversation's PubNub channel, so it works when you are the seller
        answering a buyer who messaged first. Same acknowledgement-based
        delivery semantics and idempotency guard as :meth:`send_message`; there
        is no browser fallback (there is no single-item context).
        """
        if not conversation_id:
            return Response(0, {"error_message": "conversation_id required"}, False, False)
        thread = self.conversation_thread(conversation_id)
        if not thread.ok:
            return thread
        thread_data = thread.data if isinstance(thread.data, dict) else {}
        msgs = (((thread_data.get("messages") or {}).get("messages")) or [])
        # idempotency: never resend when our last message already says this.
        for m in sorted(msgs, key=lambda m: m.get("timestamp") or 0)[::-1]:
            if m.get("from_self"):
                if str(m.get("text") or "") == message:
                    if report is not None:
                        report["path"] = "skipped"
                        report["skipped"] = True
                        report["delivery"] = "skipped"
                    return self.conversation_thread(conversation_id)
                break
        channel = thread_data.get("channel") or ""
        to_user = (thread_data.get("with_user") or {}).get("hash") or ""
        if not channel or not to_user:
            return Response(
                0,
                {"error_message": "conversation has no channel or peer; cannot reply",
                 "delivery": "not_sent", "conversation_id": conversation_id},
                False, False,
            )
        try:
            tok = self.im_token()
        except ApiError:
            tok = None
        if tok is None or not tok.ok:
            return Response(
                0,
                {"error_message": "no IM token; message not sent",
                 "delivery": "not_sent", "conversation_id": conversation_id},
                False, False,
            )
        try:
            ack = self.pubnub_publish(channel, message, to_user, conversation_id, tok.data.get("token", ""))
        except ApiError as exc:
            if isinstance(exc, NotSentError):
                return Response(
                    0,
                    {"error_message": f"message not sent: {exc}",
                     "delivery": "not_sent", "conversation_id": conversation_id},
                    False, False,
                )
            return self._unknown_delivery(conversation_id, message, report, str(exc))
        if ack:
            if report is not None:
                report["path"] = "rest"
                report["skipped"] = False
                report["delivery"] = "confirmed"
            return self._confirmed_delivery(conversation_id, message, thread_data, channel)
        return self._unknown_delivery(
            conversation_id, message, report,
            "PubNub did not acknowledge the publish (it may still have been delivered)",
        )

    def _confirmed_delivery(
        self,
        conversation_id: str,
        message: str,
        thread_data: dict,
        channel: str,
    ) -> Response:
        """Compact answer for a confirmed delivery (no extra GET needed)."""
        return Response(
            200,
            {
                "conversation_id": conversation_id,
                "delivery": "confirmed",
                "outcome": "confirmed",
                "message": message,
                "channel": channel,
                "with_user": thread_data.get("with_user") or {},
                "item": thread_data.get("item") or {},
            },
            True,
            False,
        )

    def _unknown_delivery(
        self,
        conversation_id: str,
        message: str,
        report: Optional[dict],
        detail: str,
    ) -> Response:
        """Answer for a send whose outcome is unknown (it may have arrived)."""
        if report is not None:
            report["path"] = "unknown"
            report["skipped"] = False
            report["delivery"] = "unknown"
        return Response(
            0,
            {
                "error_message": (
                    "chat delivery is unknown: the message may or may not have reached "
                    f"the seller ({detail}); inspect the conversation before sending the "
                    "message again"
                ),
                "delivery": "unknown",
                "outcome": "unknown",
                "conversation_id": conversation_id,
                "message": message,
            },
            False,
            False,
        )

    def im_token(self) -> Response:
        """Fetch the PubNub access-manager token used as `auth` on publish."""
        return self._authed("GET", "/instant-messaging/token")

    def pubnub_publish(
        self,
        channel: str,
        text: str,
        to_user_hash: str,
        conversation_hash: str,
        auth_token: str,
    ) -> bool:
        """Publish a chat message to a conversation channel via PubNub REST.

        Returns True when PubNub acks with ``[1,"Sent",<timetoken>]`` (the
        acknowledgement is accepted both as a decoded list and as a JSON
        string).

        The publish is a *write* carried over a ``GET``: it is never retried
        automatically, because a replay could deliver the message twice.

        .. warning::
           ``False`` only means "there is no valid acknowledgement".  It is
           **not** a proof that the message was not delivered: a ``5xx``, a
           truncated or non-JSON answer and even a negative ack may hide a
           publish the server already accepted.  Callers must treat a
           ``False`` as an *unknown* outcome and must never retransmit the
           message on their own.
        """
        message = json.dumps({"id": str(uuid.uuid4()), "payload": {"text": text}}, separators=(",", ":"))
        meta = {
            "type": "text",
            "sender": {
                "platform": {"app_version": WEB_APP_VERSION, "os_version": "0"},
            },
            "to_user_hash": to_user_hash,
            "from_user_hash": user_hash_of(self.token),
            "conversation_hash": conversation_hash,
        }
        path = (
            f"https://{PUB_ORIGIN}/publish/{PUB_KEY}/{SUB_KEY}/0/"
            f"{channel}/0/{urllib.parse.quote(message, safe='')}"
        )
        params = {
            "meta": json.dumps(meta, separators=(",", ":")),
            "uuid": user_hash_of(self.token),
            "requestid": str(uuid.uuid4()),
            "pnsdk": PNSDK,
            "auth": auth_token,
        }
        url = path + "?" + urllib.parse.urlencode(params)
        status, payload, _ = http_request("GET", url, headers={}, retry_safe=False)
        if status != 200:
            return False
        return parse_pubnub_ack(payload)


def send_chat_text(item_hash: str, text: str) -> bool:
    """Actually deliver a chat message using a headless browser session (the web app sends over PubNub)."""
    session = load_session()
    cookies = {k: session[k] for k in WALLAPOP_COOKIE_KEYS if session.get(k)}
    if not cookies or not cookies.get("accessToken"):
        eprint("no browser session stored; run: wallapop login --browser firefox  (or --cookies ...)")
        return False
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        eprint("playwright not installed. run: pip install --user --break-system-packages playwright && python3 -m playwright install firefox")
        return False
    try:
        t0 = time.monotonic()
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            ctx.add_cookies(
                [{"name": n, "value": v, "domain": ".wallapop.com", "path": "/"}
                 for n, v in cookies.items() if v]
            )
            page = ctx.new_page()
            page.goto(f"{WEB_BASE}/app/chat?itemId={item_hash}", wait_until="domcontentloaded", timeout=90000)
            t1 = time.monotonic()
            eprint(f"[chat] browser up + page loaded in {t1 - t0:.1f}s")
            ta = page.locator("textarea.wallapop-chat-input__textarea").first
            ready = False
            for _ in range(30):
                if ta.count():
                    ready = True
                    break
                page.wait_for_timeout(1000)
            if not ready:
                browser.close()
                eprint("browser chat did not open a textarea")
                return False
            t2 = time.monotonic()
            eprint(f"[chat] textarea ready in {t2 - t1:.1f}s")
            ta.fill(text)
            page.keyboard.press("Enter")
            page.wait_for_timeout(5000)
            body = page.locator("body").inner_text() or ""
            browser.close()
            t3 = time.monotonic()
            eprint(f"[chat] send + settle in {t3 - t2:.1f}s  (total {t3 - t0:.1f}s)")
            return text in body
    except Exception as err:
        eprint(f"browser send failed: {err}")
        return False
