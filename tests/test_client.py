"""Offline tests for the Wallapop client with a mocked HTTP transport.

No network: ``wallapop_mcp.client.http_request`` is replaced by a fake that
answers endpoint calls the way the real API does (shapes taken from live
captures, ids de-sensibilized).
"""

import argparse
import builtins
import uuid
from decimal import Decimal

import pytest

import wallapop_mcp.client as client_mod
import wallapop_mcp.http as http_mod
from wallapop_mcp.client import Wallapop, refresh_web_session

# --- payload fixtures (live-shape, des-sensibilized) ------------------------

RAW_ITEM = {
    "id": "v6gpwy9d476e",
    "title": {"original": "Nintendo DS Lite Blanca + Funda"},
    "price": {"amount": 45.0, "currency": "EUR"},
    "user": {"id": "wz0123456789", "micro_name": "Daniel"},
    "shipping": {"item_is_shippable": True},
    "location": {"city": "Barcelona"},
    "web_slug": "nintendo-ds-lite-blanca-funda-v6gpwy9d476e",
    "flags": {"reserved": False, "sold": False},
    "images": [
        {
            "id": "nz0gdgn1dwrj",
            "average_color": "13C1AC",
            "urls": {
                "small": "https://cdn.wallapop.com/images/s/i1.jpg",
                "medium": "https://cdn.wallapop.com/images/m/i1.jpg",
                "big": "https://cdn.wallapop.com/images/b/i1.jpg",
            },
        }
    ],
}

SEARCH_COMPONENTS = {"data": {"search_id": "abc123", "sections": {"organic": {"position": 1}}}}

RAW_ME = {
    "id": "wz0123456789",
    "micro_name": "Daniel",
    "location": {"approximated_latitude": 43.66, "approximated_longitude": -7.60, "approximated_location": True},
}

RAW_PUBLISHED_ITEM = {
    "id": "v6gpwy9d476e",
    "price": {"amount": 45.0, "currency": "EUR"},
    "shipping": {"item_is_shippable": True, "user_allows_shipping": True, "cost_configuration_id": "814429d6-0000-0000-0000-000000000000"},
}

#: An *owned* item, as ``GET /items/<id>`` returns it for the seller: it
#: carries the editable state (category, condition, shipping, weight and
#: location) the edit has to preserve.
RAW_OWNED_ITEM = {
    "id": "v6gpwy9d476e",
    "title": {"original": "Nintendo DS Lite Blanca + Funda"},
    "price": {"cash": {"amount": 45.0, "currency": "EUR"}},
    "condition": "good",
    "category_leaf_id": "10088",
    "brand": "Nintendo",
    "model": "DS Lite",
    "color": "white",
    "description": {"original": "En buen estado"},
    "shipping": {
        "item_is_shippable": True,
        "user_allows_shipping": True,
        "cost_configuration_id": "814429d6-0000-0000-0000-000000000000",
    },
    "type_attributes": {"up_to_kg": "5.0"},
    "location": {"lat": 40.4168, "lon": -3.7038, "city": "Madrid"},
    "user": {"id": "wz0123456789", "micro_name": "Daniel"},
    "flags": {"reserved": False, "sold": False},
    "images": [{"id": "nz0gdgn1dwrj"}],
}

LEGACY_SEARCH = {
    "search_objects": [RAW_ITEM]
}

RAW_SELLER = {
    "id": "wz0123456789",
    "micro_name": "Daniel",
    "is_top_profile": True,
    "location": {"city": "Barcelona", "country_code": "ES"},
}

RAW_REVIEWS = [
    {"item": {"title": "GTA V"}, "review": {"rating_over_five": 5, "scoring": 100}},
    {"item": {"title": "3DS"}, "review": {"rating_over_five": 4, "scoring": 80}},
]

RAW_SELLER_STATS = {
    "rating_average": 4.9,
    "counters": [
        {"type": "publish", "value": 139},
        {"type": "buys", "value": 98},
        {"type": "sells", "value": 505},
        {"type": "reviews", "value": 465},
        {"type": "sold", "value": 505},
    ],
}

RAW_THREAD = {
    "channel": "channel-abc",
    "with_user": {"hash": "36ewgvee286d", "name": "pixelia"},
    "messages": {"messages": []},
}

RAW_THREAD_SAME_TEXT = {
    **RAW_THREAD,
    "messages": {
        "messages": [
            {
                "timestamp": 1760000001000,
                "from_self": True,
                "type": "text",
                "text": "Y tienes Super Mario 3D Land?",
            }
        ]
    },
}

RAW_THREAD_NO_MY_TEXT = {
    **RAW_THREAD,
    "messages": {
        "messages": [
            {"timestamp": 1760000001000, "from_self": False, "type": "text", "text": "Si, lo tengo!"}
        ]
    },
}

RAW_CATEGORIES = {
    "categories": [
        {
            "id": 100,
            "name": "Coches",
            "subcategories": [],
        },
        {
            "id": 12800,
            "name": "Motor y accesorios",
            "subcategories": [
                {"id": 10165, "name": "Herramientas", "subcategories": []},
                {
                    "id": 10167,
                    "name": "Recambios de coches y furgonetas",
                    "subcategories": [{"id": 10328, "name": "Accesorios", "subcategories": []}],
                },
            ],
        },
    ]
}

RAW_SUGGESTIONS = {
    "suggestions": [
        {"regular_suggestion": {"suggestion": {"text": "switch nintendo", "highlight": "nintendo switch", "subtitle": ""}}},
        {"regular_suggestion": {"suggestion": {"text": "switch 2 nintendo", "highlight": "nintendo switch", "subtitle": ""}}},
    ]
}

RAW_EXTRA_INFO = {
    "response_rate": "less_than_one_hour",
    "scoring_stars": 98.0,
    "rating_average": 4.9,
    "validations": {"email": True, "mobile": True, "level": "verified", "kyc": True},
    "activity_level": "unknown",
}

RAW_OTHER_ITEMS = {"data": {"items": [RAW_ITEM], "tracking": {"slider": 1}}}

RAW_ACTIONS_ALLOWED = [
    {"type": "inactivate", "allowed": True, "cause": None},
    {"type": "delete", "allowed": True, "cause": None},
]

RAW_PHONE = {"phone_number": "+34 600 000 000"}

RAW_FAVORITE_LISTS = {"data": [RAW_ITEM], "meta": {"next": "fav-cursor-2", "search_enabled": True}}

RAW_HITS = {"hits": 7}

# --- transport fake ---------------------------------------------------------


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.retry_safe: list = []
        self.thread_same = False
        self.section_pages = 1
        self.empty_section_pages = 0
        self.force_legacy = False
        #: when set, ``GET /items/<id>`` answers with it (an *owned* item,
        #: with the editable state) instead of the public one.
        self.owned_item: dict | None = None
        #: when set, ``GET /users/<id>/items`` answers with it instead of the
        #: default list (used to hide the account's cost configuration).
        self.published_items: dict | None = None

    def __call__(self, method: str, url: str, params=None, body=None, headers=None, retry_safe=None, **kwargs):
        self.calls.append((method, url, dict(params or {}), dict(body or {})))
        self.retry_safe.append(retry_safe)
        if url.endswith("/search/components"):
            if self.force_legacy:
                return 200, {"data": {}}, "application/json"
            return 200, SEARCH_COMPONENTS, "application/json"
        if "/search/section" in url:
            section_calls = [c for c in self.calls if "/search/section" in c[1]]
            current = section_calls.index((method, url, dict(params or {}), dict(body or {})))
            if current < self.empty_section_pages:
                return 200, {
                    "data": {
                        "next_page": "empty-cursor-page-next",
                        "section": {"type": "organic_search_results", "title": None, "items": []},
                    }
                }, "application/json"
            item_page = current - self.empty_section_pages
            has_next = item_page < self.section_pages - 1
            return 200, {
                "data": {
                    "next_page": "cursor-page-2" if has_next else None,
                    "items": [RAW_ITEM],
                }
            }, "application/json"
        if url.endswith("/general/search"):
            return 200, LEGACY_SEARCH, "application/json"
        if url.endswith(f"/items/{RAW_ITEM['id']}") and method == "GET":
            return 200, self.owned_item or RAW_ITEM, "application/json"
        if url.endswith(f"/items/{RAW_ITEM['id']}") and method == "PUT":
            return 204, None, "text/plain"
        if url.endswith("/users/wz0123456789/reviews"):
            return 200, RAW_REVIEWS, "application/json"
        if url.endswith("/users/wz0123456789/stats"):
            return 200, RAW_SELLER_STATS, "application/json"
        if url.endswith("/users/wz0123456789"):
            return 200, RAW_SELLER, "application/json"
        if url.endswith("/instant-messaging/conversation"):
            return 200, {"conversation_id": "wzy050ee4e65", "item_hash_id": RAW_ITEM["id"]}, "application/json"
        if "/bff/messaging/conversation/" in url:
            if self.thread_same or "same" in url:
                return 200, RAW_THREAD_SAME_TEXT, "application/json"
            return 200, RAW_THREAD_NO_MY_TEXT, "application/json"
        if url.endswith("/instant-messaging/token"):
            return 200, {"token": "im-auth-token"}, "application/json"
        if "publish" in url:
            return 200, '[1,"Sent",1234567890]', "text/plain"
        if "/bff/delivery/offer-details" in url:
            return 200, {"offer_id": "offer-1", "status": "PENDING"}, "application/json"
        if "/bff/delivery" in url or "/bff/messaging/inbox" in url:
            data = {"restriction": {"max_offer_percentage": 30.0, "remaining_offers_per_day": 3}, "conversations": []}
            return 200, data, "application/json"
        if "/delivery/buyer/offers" in url or "/delivery/offers/" in url:
            return 200, {"offer_id": "created"}, "application/json"
        if url.endswith("/categories"):
            return 200, RAW_CATEGORIES, "application/json"
        if url.endswith("/access/login"):
            return 200, {
                "data": {
                    "access_token": "access-123",
                    "refresh_token": "refresh-123",
                    "user": {"id": "wz0123456789", "micro_name": "Daniel"},
                }
            }, "application/json"
        if url.endswith("/steps"):
            return 200, {"step": {"id": "title", "components": [], "metadata": {}}}, "application/json"
        if url.endswith("/users/me"):
            return 200, RAW_ME, "application/json"
        if "/users/wz0123456789/items" in url:
            if self.published_items:
                return 200, self.published_items, "application/json"
            if params.get("since"):
                return 200, {"data": [RAW_PUBLISHED_ITEM], "meta": {"next": None}}, "application/json"
            return 200, {"data": [RAW_PUBLISHED_ITEM], "meta": {"next": "page-cursor-2"}}, "application/json"
        if url.endswith("/favorite") and method == "PUT":
            return 204, None, "text/plain"
        if url.endswith(f"/items/{RAW_ITEM['id']}") and method == "DELETE":
            return 204, None, "text/plain"
        if url.endswith("/suggesters/nonlocated"):
            return 200, RAW_SUGGESTIONS, "application/json"
        if url.endswith("/users/wz0123456789/extra-info"):
            return 200, RAW_EXTRA_INFO, "application/json"
        if url.endswith("/seller/other-items"):
            return 200, RAW_OTHER_ITEMS, "application/json"
        if url.endswith("/seller-phone-number"):
            return 200, RAW_PHONE, "application/json"
        if url.endswith("/actions-allowed"):
            return 200, RAW_ACTIONS_ALLOWED, "application/json"
        if url.endswith("/favorite-lists") and method == "GET":
            if params.get("since"):
                return 200, {"data": [], "meta": {"next": None}}, "application/json"
            return 200, RAW_FAVORITE_LISTS, "application/json"
        if "/favorite-lists/" in url and "/items" in url and method in ("POST", "DELETE"):
            return 204, None, "text/plain"
        if url.endswith("/inactivate") and method == "PUT":
            return 204, None, "text/plain"
        if url.endswith("/view") and method == "PUT":
            return 204, None, "text/plain"
        base = "https://api.wallapop.com/api/v3/searchalerts/savedsearch"
        saved_id = "ae1a42fb-e6a7-4507-ae3f-3dd3139aa69d"
        if url.endswith("/savedsearch/alerts/total-hits"):
            return 200, RAW_HITS, "application/json"
        if url.endswith("/savedsearch/exists"):
            if params.get("keywords") == "not-saved-thing":
                return 404, None, "text/plain", {}
            return 200, None, "text/plain", {"Location": f"{base}/{saved_id}"}
        if url.endswith("/savedsearch") and method == "POST":
            return 201, None, "text/plain", {"Location": f"{base}/{saved_id}"}
        if "/savedsearch/" in url and method == "DELETE":
            return 204, None, "text/plain"
        raise AssertionError(f"unexpected call: {method} {url} params={params} body={body}")


@pytest.fixture
def fake(monkeypatch):
    t = FakeTransport()
    monkeypatch.setattr(client_mod, "http_request", t)
    return t


# --- search / normalize -----------------------------------------------------

def test_seller_profile(fake):
    client = Wallapop()
    profile = client.seller("wz0123456789")
    assert profile["micro_name"] == "Daniel"
    assert profile["is_top_profile"] is True


def test_seller_reviews(fake):
    client = Wallapop()
    reviews = client.seller_reviews("wz0123456789")
    assert len(reviews) == 2
    ratings = [r["review"]["rating_over_five"] for r in reviews]
    assert sum(ratings) / len(ratings) == 4.5


def test_seller_stats(fake):
    client = Wallapop()
    stats = client.seller_stats("wz0123456789")
    assert stats["rating_average"] == 4.9
    counters = {c["type"]: c["value"] for c in stats["counters"]}
    assert counters["reviews"] == 465


def test_search_uses_default_geo_when_no_coords(fake):
    client = Wallapop()
    client.search("gamecube", pages=1, delay=0)
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    params = comps[2]
    assert params["latitude"] == 40.0
    assert params["longitude"] == -3.5


def test_search_respects_provided_coords(fake):
    client = Wallapop()
    client.search("gamecube", pages=1, delay=0, latitude=41.5, longitude=2.1)
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    params = comps[2]
    assert params["latitude"] == 41.5
    assert params["longitude"] == 2.1


def test_search_sends_filter_params(fake):
    client = Wallapop()
    client.search(
        "gamecube",
        pages=1,
        delay=0,
        category_id=1,
        subcategory_ids=2,
        condition="new",
        min_price=10.0,
        max_price=100.0,
        ship_only=True,
    )
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    params = comps[2]
    assert params["keywords"] == "gamecube"
    assert params["category_id"] == 1
    assert params["subcategory_ids"] == 2
    assert params["condition"] == "new"
    assert params["min_sale_price"] == 10.0
    assert params["max_sale_price"] == 100.0
    assert params["is_shippable"] == "true"


def test_search_orders_by_relevance_by_default(fake):
    client = Wallapop()
    client.search("gamecube", pages=1, delay=0)
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    assert comps[2]["order_by"] == "most_relevance"


def test_search_paginates_with_next_page(fake):
    fake.section_pages = 3
    client = Wallapop()
    items = client.search("gamecube", pages=3, delay=0)
    sections = [c for c in fake.calls if "/search/section" in c[1]]
    assert len(sections) == 3
    assert sections[1][2].get("next_page") == "cursor-page-2"
    assert len(items) == 3


def test_search_skips_empty_section_pages(fake):
    fake.section_pages = 3
    fake.empty_section_pages = 1
    client = Wallapop()
    items = client.search("gamecube", pages=3, delay=0)
    sections = [c for c in fake.calls if "/search/section" in c[1]]
    assert len(sections) == 4
    assert sections[0][2].get("next_page") is None
    assert sections[1][2].get("next_page") == "empty-cursor-page-next"
    assert len(items) == 3


def test_search_loads_items_when_first_page_is_empty(fake):
    fake.section_pages = 1
    fake.empty_section_pages = 1
    client = Wallapop()
    items = client.search("gamecube", pages=1, delay=0)
    sections = [c for c in fake.calls if "/search/section" in c[1]]
    assert len(sections) == 2
    assert len(items) == 1


def test_search_resolves_category_from_subcategory_alone(fake):
    client = Wallapop()
    client.search("gamecube", subcategory_ids=10328, pages=1, delay=0)
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    assert comps[2]["category_id"] == 12800
    assert comps[2]["subcategory_ids"] == 10328


def test_search_keeps_given_category_with_subcategory(fake):
    client = Wallapop()
    client.search("gamecube", category_id=12800, subcategory_ids=10328, pages=1, delay=0)
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    assert comps[2]["category_id"] == 12800
    assert comps[2]["subcategory_ids"] == 10328


def test_search_promotes_midlevel_category_to_root(fake):
    client = Wallapop()
    client.search("gamecube", category_id=10167, pages=1, delay=0)
    comps = next(c for c in fake.calls if c[1].endswith("/search/components"))
    assert comps[2]["category_id"] == 12800
    assert "subcategory_ids" not in comps[2]


def test_search_falls_back_to_legacy_when_no_search_id(fake):
    fake.force_legacy = True
    client = Wallapop()
    items = client.search("gamecube", pages=1, delay=0)
    assert len(items) == 1
    legacy = next(c for c in fake.calls if c[1].endswith("/general/search"))
    assert legacy[2]["keywords"] == "gamecube"
    assert legacy[2]["distance"] == 1000000


def test_search_returns_items(fake):
    client = Wallapop()
    items = client.search("nintendo ds", pages=1, delay=0)
    assert len(items) == 1
    it = items[0]
    assert it.id == RAW_ITEM["id"]
    assert it.price == 45.0
    assert it.title == "Nintendo DS Lite Blanca + Funda"


def test_item_has_image_urls(fake):
    client = Wallapop()
    it = client.item(RAW_ITEM["id"])
    assert it.images == ["https://cdn.wallapop.com/images/m/i1.jpg"]


def test_favorite_calls_put_endpoint(fake):
    client = Wallapop()
    resp = client.favorite(RAW_ITEM["id"], True)
    assert resp.status == 204
    put_calls = [c for c in fake.calls if "/favorite" in c[1] and c[0] == "PUT"]
    assert len(put_calls) == 1
    assert put_calls[0][3] == {"favorited": True}


def test_unfavorite_sends_false(fake):
    client = Wallapop()
    resp = client.favorite(RAW_ITEM["id"], False)
    assert resp.status == 204
    put_calls = [c for c in fake.calls if "/favorite" in c[1] and c[0] == "PUT"]
    assert put_calls[0][3] == {"favorited": False}


def test_delete_item_calls_delete_endpoint(fake):
    client = Wallapop()
    resp = client.delete_item(RAW_ITEM["id"])
    assert resp.status == 204
    delete_calls = [c for c in fake.calls if c[0] == "DELETE"]
    assert len(delete_calls) == 1
    assert delete_calls[0][1].endswith(f"/items/{RAW_ITEM['id']}")


def test_login_sends_client_id_and_stores_tokens(fake):
    client = Wallapop()
    access, refresh = client.login("daniel@example.com", "secret123")
    login_calls = [c for c in fake.calls if c[1].endswith("/access/login")]
    assert len(login_calls) == 1
    body = login_calls[0][3]
    assert body == {"email": "daniel@example.com", "password": "secret123", "client_id": "wallapop"}
    assert access == "access-123"
    assert refresh == "refresh-123"
    assert client.user["micro_name"] == "Daniel"


def test_download_images(tmp_path, fake, monkeypatch):
    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def read(self):
            return self._data

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout: FakeResponse(b"JPEGDATA"),
    )
    client = Wallapop()
    it = client.item(RAW_ITEM["id"])
    saved = client.download_images(it, str(tmp_path))
    assert len(saved) == 1
    assert saved[0].endswith(f"{RAW_ITEM['id']}_1.jpg")
    with open(saved[0], "rb") as f:
        assert f.read() == b"JPEGDATA"


def test_download_images_no_op_when_no_images(fake, tmp_path):
    client = Wallapop()
    raw = dict(RAW_ITEM)
    raw["images"] = []
    item = client_mod.Item.from_raw(raw)
    assert client.download_images(item, str(tmp_path)) == []


# --- session / auth ---------------------------------------------------------

def test_refresh_web_session_returns_token(fake, monkeypatch):
    monkeypatch.setattr(client_mod, "http_request", lambda *a, **k: (
        200, {"token": "fake.access.token.xyz"}, "application/json"
    ))
    session = {"__Secure-next-auth.session-token": "st", "device_id": "d1", "device_fgp": "f1", "accessToken": "a1"}
    assert refresh_web_session(session) == "fake.access.token.xyz"


def test_authed_request_refreshes_token_on_401(fake, monkeypatch):
    calls = []

    def flaky(method, url, params=None, body=None, headers=None, retry_safe=None):
        calls.append(method)
        if "/bff/messaging/inbox" in url:
            if len(calls) == 1:
                return (401, {"error": "expired"}, "application/json")
            return 200, {"conversations": []}, "application/json"
        if "/api/auth/session" in url:
            return 200, {"token": "refreshed.token"}, "application/json"
        return 200, {}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", flaky)
    client = Wallapop(token="old.token", web_cookies={"__Secure-next-auth.session-token": "st"})
    resp = client.inbox()
    assert resp.ok
    assert resp.data == {"conversations": []}


# --- offers -----------------------------------------------------------------

def test_send_offer_builds_uuid_and_price(fake):
    client = Wallapop(token="tk")
    resp = client.send_offer(RAW_ITEM["id"], 25.0, message="hola")
    assert resp.ok
    matched = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/delivery/buyer/offers")]
    assert len(matched) == 1
    body = matched[0][3]
    uuid.UUID(body["offer_id"])  # raises if not a uuid
    assert body["offer_price_amount"] == 25.0
    assert body["offer_price_currency"] == "EUR"
    assert body["item_ids"] == [RAW_ITEM["id"]]


def test_offer_response_maps_accept_and_decline(fake):
    client = Wallapop(token="tk")
    client.offer_response("offer-1", "accept")
    client.offer_response("offer-1", "decline")
    patches = [c for c in fake.calls if c[0] == "PATCH" and "status" in c[1]]
    assert [c[3].get("status") for c in patches] == ["ACCEPTED", "DECLINED"]


def test_counter_offer_patches_price(fake):
    client = Wallapop(token="tk")
    client.counter_offer("offer-1", 37.0)
    matched = [c for c in fake.calls if c[0] == "PATCH" and c[1].endswith("/offer-price")]
    assert len(matched) == 1
    assert matched[0][3] == {"offer_price_amount": 37.0, "offer_price_currency": "EUR"}


# --- chat idempotency -------------------------------------------------------

def test_send_message_skips_when_same_text_present(fake):
    fake.thread_same = True
    client = Wallapop(token="tk")
    client.token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJoYXNoZWRfc3ViIjoid3p5bm92bXYxcGo1In0.xx"
    report = {}
    resp = client.send_message(RAW_ITEM["id"], "Y tienes Super Mario 3D Land?", report=report)
    assert report.get("skipped") is True
    assert report.get("path") == "skipped"
    assert not any("publish" in c[1] for c in fake.calls)


def test_send_message_publishes_via_rest_when_new_text(fake):
    client = Wallapop(token="tk")
    client.token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJoYXNoZWRfc3ViIjoid3p5bm92bXYxcGo1In0.xx"
    report = {}
    resp = client.send_message(RAW_ITEM["id"], "Hola! Viene con stylus?", report=report)
    assert report.get("path") == "rest"
    assert report.get("skipped") is False
    publishes = [c for c in fake.calls if "publish" in c[1]]
    assert len(publishes) == 1
    assert "ps7.pndsn.com" in publishes[0][1]


# --- browser fallback degrades without playwright ---------------------------

def test_send_chat_text_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(client_mod, "load_session", lambda: {
        "accessToken": "a", "device_id": "d", "device_fgp": "f", "__Secure-next-auth.session-token": "st"
    })
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("playwright not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert client_mod.send_chat_text(RAW_ITEM["id"], "hola") is False


# --- make_offer_info --------------------------------------------------------

def test_make_offer_info_reads_restriction(fake):
    client = Wallapop(token="tk")
    resp = client.make_offer_info(RAW_ITEM["id"])
    assert resp.ok
    assert resp.data["restriction"]["max_offer_percentage"] == 30.0


# --- categories -------------------------------------------------------------

def test_categories_hits_context_search(fake):
    client = Wallapop()
    client.categories()
    call = next(c for c in fake.calls if c[1].endswith("/categories"))
    assert call[2] == {"context": "search"}


def test_categories_returns_clean_tree(fake):
    client = Wallapop()
    cats = client.categories()
    assert [c["id"] for c in cats] == [100, 12800]
    assert cats[0] == {"id": 100, "name": "Coches", "subcategories": []}
    mot = cats[1]
    assert mot["name"] == "Motor y accesorios"
    assert mot["subcategories"][1]["name"] == "Recambios de coches y furgonetas"
    assert mot["subcategories"][1]["subcategories"][0]["id"] == 10328


# --- publish (pure API) ----------------------------------------------------

def test_publish_builds_multipart_with_me_location_and_cost_cfg(fake, monkeypatch, tmp_path):
    import json as _json

    calls_raw: list[tuple[str, str, bytes, dict]] = []

    def fake_raw(method, url, raw_body=None, headers=None, retries=3, timeout=60):
        calls_raw.append((method, url, raw_body, dict(headers or {})))
        return 200, {"id": "ab12cd34ef56", "flags": {}}

    monkeypatch.setattr(client_mod, "http_request_raw", fake_raw)
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    client = Wallapop(token="tk")
    resp = client.publish([str(photo)], "GameCube", 40.0, brand="Nintendo", model="GC", color="black")
    assert resp.ok and resp.data["id"] == "ab12cd34ef56"
    method, url, raw, headers = calls_raw[0]
    assert method == "POST"
    assert url.endswith("/api/v3/items")
    assert 'multipart/form-data; boundary=' in headers["Content-Type"]
    assert headers.get("Accept") == "application/vnd.upload-v2+json"
    text = raw.decode("utf-8", "replace")
    assert 'name="image"; filename="foto.jpg"' in text
    body = raw.replace(b"\xff\xd8\xff\xe0fakejpeg", b"")
    import re as _re
    item_part = _re.search(r'name="item"\r\n\r\n(\{.*?\})\r\n--', body.decode("utf-8", "replace"))
    assert item_part is not None
    item = _json.loads(item_part.group(1))
    assert item["attributes"]["price_amount"] == 40
    assert item["attributes"]["brand"] == "Nintendo"
    assert item["location"]["latitude"] == 43.66
    assert item["location"]["longitude"] == -7.60
    assert item["delivery"]["cost_configuration_id"] == "814429d6-0000-0000-0000-000000000000"
    assert item["delivery"]["allowed_by_user"] is True
    assert item["delivery"]["max_weight_kg"] == 1
    assert item["upload_id"]


def test_publish_sends_only_first_photo(fake, monkeypatch, tmp_path):
    calls_raw = []
    monkeypatch.setattr(client_mod, "http_request_raw", lambda *a, **k: calls_raw.append(a) or (200, {"id": "x"}))
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpeg")
    client = Wallapop(token="tk")
    client.user = {"id": "wz0123456789"}
    resp = client.publish([str(photo)], "T", 5.0)
    assert resp.ok
    assert len(calls_raw) == 1


@pytest.mark.parametrize("shipping_allowed", [True, False])
def test_publish_uploads_extra_photos(fake, monkeypatch, tmp_path, shipping_allowed):
    calls_raw = []
    def track_raw(method, url, raw_body=None, headers=None, retries=3, timeout=60):
        calls_raw.append((method, url))
        if "/picture2" in url:
            return 200, "abc123"
        if "/items" in url:
            return 200, {"id": "abc123"}
        return 200, {}
    monkeypatch.setattr(client_mod, "http_request_raw", track_raw)
    photos = []
    for i in range(3):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(b"jpeg")
        photos.append(str(p))
    client = Wallapop(token="tk")
    resp = client.publish(photos, "T", 5.0, shipping_allowed=shipping_allowed)
    assert resp.ok
    raw_urls = [u for _, u in calls_raw]
    assert len([u for u in raw_urls if "/picture2" in u]) == 2
    steps_calls = [c for c in fake.calls if "/steps" in c[1]]
    assert len(steps_calls) == 0
    assert any(u.endswith("/api/v3/items") for u in raw_urls)


# --- helpers for the new regression tests -----------------------------------

JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJoYXNoZWRfc3ViIjoid3p5bm92bXYxcGo1In0.xx"


def published_item_json(raw: bytes) -> dict:
    """Extract the ``item`` document from a publish multipart body."""
    import json as _json
    import re as _re

    text = raw.decode("utf-8", "replace")
    part = _re.search(r'name="item"\r\n\r\n(\{.*?\})\r\n--', text)
    assert part is not None, text[:500]
    return _json.loads(part.group(1))


class NoAckTransport(FakeTransport):
    """Fake transport whose PubNub publish is *not* acknowledged."""

    def __call__(self, method, url, params=None, body=None, headers=None, retry_safe=None):
        if "publish" in url:
            self.calls.append((method, url, dict(params or {}), dict(body or {})))
            self.retry_safe.append(retry_safe)
            return 200, '[0,"Error","denied"]', "text/plain"
        return super().__call__(method, url, params=params, body=body, headers=headers, retry_safe=retry_safe)


# --- offer details: the BFF prefix ------------------------------------------

def test_offer_details_uses_the_bff_prefix(fake):
    client = Wallapop(token="tk")
    resp = client.offer_details("offer-1")
    assert resp.ok
    call = next(c for c in fake.calls if "offer-details" in c[1])
    assert call[0] == "GET"
    assert call[1] == "https://api.wallapop.com/bff/delivery/offer-details?offer_id=offer-1"


def test_offer_details_path_is_not_the_api_v3_one(fake):
    client = Wallapop(token="tk")
    client.offer_details("offer-1")
    call = next(c for c in fake.calls if "offer-details" in c[1])
    assert "/api/v3/" not in call[1]


# --- prices keep their cents ------------------------------------------------

def test_publish_keeps_the_price_cents(fake, monkeypatch, tmp_path):
    calls_raw = []

    def fake_raw(method, url, raw_body=None, headers=None, retries=3, timeout=60, retry_safe=None):
        calls_raw.append((method, url, raw_body))
        return 200, {"id": "abc123"}

    monkeypatch.setattr(client_mod, "http_request_raw", fake_raw)
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpeg")
    client = Wallapop(token="tk")
    resp = client.publish([str(photo)], "T", 19.95, color="black")
    assert resp.ok
    item = published_item_json(calls_raw[0][2])
    assert item["attributes"]["price_amount"] == 19.95
    assert item["attributes"]["price_amount"] != 20


def test_publish_rejects_a_price_with_more_than_two_decimals(fake, monkeypatch, tmp_path):
    """A price with extra precision is refused, never silently rounded."""
    calls_raw = []

    def fake_raw(method, url, raw_body=None, headers=None, retries=3, timeout=60, retry_safe=None):
        calls_raw.append((method, url, raw_body))
        return 200, {"id": "abc123"}

    monkeypatch.setattr(client_mod, "http_request_raw", fake_raw)
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpeg")
    client = Wallapop(token="tk")
    with pytest.raises(client_mod.InvalidPrice):
        client.publish([str(photo)], "T", 19.999)
    assert calls_raw == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0, "abc", None, True])
def test_publish_rejects_invalid_prices(fake, monkeypatch, tmp_path, bad):
    calls_raw = []
    monkeypatch.setattr(
        client_mod,
        "http_request_raw",
        lambda *a, **k: calls_raw.append(a) or (200, {"id": "x"}),
    )
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpeg")
    client = Wallapop(token="tk")
    with pytest.raises(client_mod.InvalidPrice):
        client.publish([str(photo)], "T", bad)
    assert calls_raw == []
    assert fake.calls == []


def test_invalid_price_is_both_an_api_error_and_a_value_error():
    assert issubclass(client_mod.InvalidPrice, client_mod.ApiError)
    assert issubclass(client_mod.InvalidPrice, ValueError)


def test_edit_item_keeps_the_price_cents(fake):
    fake.owned_item = RAW_OWNED_ITEM
    client = Wallapop(token="tk")
    resp = client.edit_item(RAW_ITEM["id"], price=19.95)
    assert resp.ok
    put = next(c for c in fake.calls if c[0] == "PUT")
    assert put[3]["attributes"]["price_amount"] == 19.95
    assert put[3]["attributes"]["price_amount"] != 20


def test_edit_item_rejects_invalid_prices(fake):
    client = Wallapop(token="tk")
    with pytest.raises(client_mod.InvalidPrice):
        client.edit_item(RAW_ITEM["id"], price=float("nan"))
    assert not [c for c in fake.calls if c[0] == "PUT"]


def test_edit_item_keeps_the_current_price_cents(fake):
    """Without an explicit price the current one is kept, cents included."""
    fake.owned_item = {**RAW_OWNED_ITEM, "price": {"cash": {"amount": 19.95, "currency": "EUR"}}}
    client = Wallapop(token="tk")
    resp = client.edit_item(RAW_ITEM["id"], title="nuevo")
    assert resp.ok
    put = next(c for c in fake.calls if c[0] == "PUT")
    assert put[3]["attributes"]["price_amount"] == 19.95
    assert put[3]["attributes"]["title"] == "nuevo"


# --- publish: partial result when an extra photo fails ----------------------

def test_publish_reports_a_partial_result_when_an_extra_photo_fails(fake, monkeypatch, tmp_path):
    calls_raw = []

    def fake_raw(method, url, raw_body=None, headers=None, retries=3, timeout=60, retry_safe=None):
        calls_raw.append((method, url))
        if "/picture2" in url:
            return 500, {"error_message": "storage is on fire"}
        return 200, {"id": "abc123"}

    monkeypatch.setattr(client_mod, "http_request_raw", fake_raw)
    photos = []
    for i in range(3):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(b"jpeg")
        photos.append(str(p))
    client = Wallapop(token="tk")
    resp = client.publish(photos, "T", 5.0)
    assert not resp.ok
    assert resp.partial is True
    data = resp.data
    # the item *was* created: it must not look absent
    assert data["id"] == "abc123"
    assert data["item_id"] == "abc123"
    assert data["created"] is True
    assert data["photos_total"] == 3
    assert data["photos_completed"] == 1
    assert data["failed_photo_index"] == 1
    assert data["failed_photo_order"] == 2
    assert data["failed_photo_path"] == photos[1]
    assert data["outcome"] == "unknown"
    assert "inspect the listing before uploading this photo again" in data["retry"]
    assert "abc123" in data["retry"]


# --- PubNub acknowledgement -------------------------------------------------

def test_parse_pubnub_ack_accepts_a_list_and_a_json_string():
    assert client_mod.parse_pubnub_ack([1, "Sent", 123]) is True
    assert client_mod.parse_pubnub_ack('[1,"Sent",123]') is True
    assert client_mod.parse_pubnub_ack([1, "Sent"]) is True
    assert client_mod.parse_pubnub_ack([0, "Error", "denied"]) is False
    assert client_mod.parse_pubnub_ack("not json") is False
    assert client_mod.parse_pubnub_ack(None) is False
    assert client_mod.parse_pubnub_ack([]) is False


def test_pubnub_publish_accepts_a_json_string_ack(monkeypatch):
    seen = []

    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        seen.append((url, retry_safe))
        return 200, '[1,"Sent",123]', "text/plain"

    monkeypatch.setattr(client_mod, "http_request", fake)
    client = Wallapop(token=JWT)
    assert client.pubnub_publish("chan", "hola", "user", "conv", "auth") is True
    # a publish is a write: it must never be retried automatically
    assert seen[0][1] is False


def test_pubnub_publish_accepts_a_decoded_list_ack(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 200, [1, "Sent", 123], "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    client = Wallapop(token=JWT)
    assert client.pubnub_publish("chan", "hola", "user", "conv", "auth") is True


def test_pubnub_publish_rejects_a_negative_ack(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 200, '[0,"Error","denied"]', "text/plain"

    monkeypatch.setattr(client_mod, "http_request", fake)
    client = Wallapop(token=JWT)
    assert client.pubnub_publish("chan", "hola", "user", "conv", "auth") is False


# --- chat delivery: the browser fallback is opt-in --------------------------

def test_send_message_reports_unknown_delivery_without_browser_fallback(monkeypatch):
    transport = NoAckTransport()
    monkeypatch.setattr(client_mod, "http_request", transport)
    monkeypatch.setattr(
        client_mod,
        "send_chat_text",
        lambda *a, **k: pytest.fail("the browser fallback must never start automatically"),
    )
    client = Wallapop(token=JWT)
    report = {}
    resp = client.send_message(RAW_ITEM["id"], "hola", report=report)
    assert not resp.ok
    assert resp.data["delivery"] == "unknown"
    assert report["path"] == "unknown"
    assert report["delivery"] == "unknown"


def test_send_message_uses_the_browser_only_when_explicitly_allowed(monkeypatch):
    transport = FakeTransport()
    monkeypatch.setattr(client_mod, "http_request", transport)
    sent = []
    monkeypatch.setattr(client_mod, "send_chat_text", lambda ih, text: sent.append(text) or True)
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)
    client = Wallapop(token=JWT)
    monkeypatch.setattr(client, "im_token", lambda: http_mod.Response(503, {}, False, False))
    report = {}
    resp = client.send_message(
        RAW_ITEM["id"], "hola", report=report, allow_browser_fallback=True
    )
    assert sent == ["hola"]
    assert report["path"] == "browser"
    assert report["delivery"] == "confirmed"
    assert not any("publish" in call[1] for call in transport.calls)


@pytest.mark.parametrize("status,payload", [
    (200, '[0,"Error","denied"]'),
    (503, "unavailable"),
    (200, '[1,"Sent"'),
])
def test_send_message_never_falls_back_after_unacknowledged_publish(monkeypatch, status, payload):
    class UnacknowledgedTransport(FakeTransport):
        def __call__(self, method, url, params=None, body=None, headers=None, retry_safe=None):
            if "publish" in url:
                self.calls.append((method, url, dict(params or {}), dict(body or {})))
                return status, payload, "text/plain"
            return super().__call__(method, url, params=params, body=body, headers=headers, retry_safe=retry_safe)

    transport = UnacknowledgedTransport()
    monkeypatch.setattr(client_mod, "http_request", transport)
    browser_calls = []
    monkeypatch.setattr(client_mod, "send_chat_text", lambda *a: browser_calls.append(a) or True)
    report = {}
    response = Wallapop(token=JWT).send_message(RAW_ITEM["id"], "hola", report=report, allow_browser_fallback=True)
    assert not response.ok
    assert response.data["outcome"] == "unknown"
    assert report["path"] == "unknown"
    assert len([call for call in transport.calls if "publish" in call[1]]) == 1
    assert browser_calls == []


# --- 401 handling -----------------------------------------------------------

def test_401_refresh_prefers_the_refresh_token(monkeypatch):
    calls = []

    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        calls.append(url)
        if "/bff/messaging/inbox" in url:
            inbox_calls = [c for c in calls if "/bff/messaging/inbox" in c]
            if len(inbox_calls) == 1:
                return 401, {"error": "expired"}, "application/json"
            return 200, {"conversations": []}, "application/json"
        if url.endswith("/access/refresh"):
            return 200, {"data": {"access_token": "fresh.token"}}, "application/json"
        if "/api/auth/session" in url:
            raise AssertionError("the web session must not be used when the refresh token works")
        return 200, {}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    client = Wallapop(token="old.token", refresh_token="refresh-1")
    resp = client.inbox()
    assert resp.ok
    assert client.token == "fresh.token"
    assert any(c.endswith("/access/refresh") for c in calls)
    assert not any("/api/auth/session" in c for c in calls)


def test_401_falls_back_to_the_web_session(monkeypatch):
    calls = []

    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        calls.append(url)
        if "/bff/messaging/inbox" in url:
            inbox_calls = [c for c in calls if "/bff/messaging/inbox" in c]
            if len(inbox_calls) == 1:
                return 401, {"error": "expired"}, "application/json"
            return 200, {"conversations": []}, "application/json"
        if url.endswith("/access/refresh"):
            return 401, {"error": "invalid refresh token"}, "application/json"
        if "/api/auth/session" in url:
            return 200, {"token": "web.token"}, "application/json"
        return 200, {}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    client = Wallapop(
        token="old.token",
        refresh_token="refresh-1",
        web_cookies={"__Secure-next-auth.session-token": "st"},
    )
    resp = client.inbox()
    assert resp.ok
    assert client.token == "web.token"


def test_401_is_not_replayed_with_the_same_token(monkeypatch):
    calls = []

    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        calls.append(url)
        if "/bff/messaging/inbox" in url:
            return 401, {"error": "expired"}, "application/json"
        if url.endswith("/access/refresh"):
            # the server hands back the very same (dead) token
            return 200, {"data": {"access_token": "old.token"}}, "application/json"
        return 200, {}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    client = Wallapop(token="old.token", refresh_token="refresh-1")
    resp = client.inbox()
    assert resp.status == 401
    assert resp.unauthorized is True
    assert len([c for c in calls if "/bff/messaging/inbox" in c]) == 1


def test_401_is_not_replayed_when_the_refresh_fails(monkeypatch):
    calls = []

    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        calls.append(url)
        if "/bff/messaging/inbox" in url:
            return 401, {"error": "expired"}, "application/json"
        if url.endswith("/access/refresh"):
            return 401, {"error": "nope"}, "application/json"
        return 200, {}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    client = Wallapop(token="old.token", refresh_token="refresh-1")
    resp = client.inbox()
    assert resp.status == 401
    assert len([c for c in calls if "/bff/messaging/inbox" in c]) == 1


# --- payload validation -----------------------------------------------------

def test_seller_raises_on_http_error(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 500, {"error_message": "boom"}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    with pytest.raises(client_mod.ApiError):
        Wallapop().seller("wz0123456789")


def test_seller_raises_when_the_payload_is_not_an_object(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 200, "not a profile", "text/plain"

    monkeypatch.setattr(client_mod, "http_request", fake)
    with pytest.raises(client_mod.ApiError):
        Wallapop().seller("wz0123456789")


def test_seller_reviews_raise_on_http_error(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 503, {"error_message": "boom"}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    with pytest.raises(client_mod.ApiError):
        Wallapop().seller_reviews("wz0123456789")


def test_seller_reviews_raise_when_the_payload_is_not_a_list(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 200, {"reviews": []}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    with pytest.raises(client_mod.ApiError):
        Wallapop().seller_reviews("wz0123456789")


def test_seller_stats_raise_on_http_error(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 404, {"error_message": "boom"}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    with pytest.raises(client_mod.ApiError):
        Wallapop().seller_stats("wz0123456789")


def test_seller_stats_raise_when_the_payload_is_not_an_object(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 200, [], "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    with pytest.raises(client_mod.ApiError):
        Wallapop().seller_stats("wz0123456789")


def test_item_rejects_an_html_response(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 200, "<html><body>anti bot</body></html>", "text/html"

    monkeypatch.setattr(client_mod, "http_request", fake)
    with pytest.raises(client_mod.ApiError):
        Wallapop().item("v6gpwy9d476e")


def test_item_rejects_a_missing_object(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 200, {"flags": {}}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    with pytest.raises(client_mod.ApiError):
        Wallapop().item("v6gpwy9d476e")


def test_search_raises_on_http_error(monkeypatch):
    def fake(method, url, params=None, body=None, headers=None, retry_safe=None):
        return 500, {"error_message": "boom"}, "application/json"

    monkeypatch.setattr(client_mod, "http_request", fake)
    with pytest.raises(client_mod.ApiError):
        Wallapop().search("gamecube")


# --- prices: exact cents, no silent rounding --------------------------------

def test_price_amount_keeps_the_cents():
    assert client_mod.price_amount(19.95) == 19.95
    assert client_mod.price_amount("19.95") == 19.95
    assert client_mod.price_amount(Decimal("19.95")) == 19.95
    assert client_mod.price_amount("19,95") == 19.95
    assert client_mod.price_amount(20) == 20.0
    assert client_mod.price_amount(0) == 0.0


def test_browser_price_keeps_cents_and_validates_before_browser():
    from wallapop_mcp.publish import browser_price

    assert browser_price(19.95) == "19.95"
    assert browser_price(20) == "20.00"
    with pytest.raises(client_mod.InvalidPrice):
        browser_price(19.999)


@pytest.mark.parametrize("bad", [19.999, "19.999", Decimal("19.999"), 0.005])
def test_price_amount_rejects_more_than_two_decimals(bad):
    with pytest.raises(client_mod.InvalidPrice):
        client_mod.price_amount(bad)


def test_price_amount_is_bounded():
    assert client_mod.price_amount(client_mod.MAX_PRICE) == 99999999.99
    with pytest.raises(client_mod.InvalidPrice):
        client_mod.price_amount(100000000)
    with pytest.raises(client_mod.InvalidPrice):
        client_mod.price_amount(Decimal("99999999.995"))
    with pytest.raises(client_mod.InvalidPrice):
        client_mod.price_amount(-0.01)


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity")]
)
def test_price_amount_rejects_non_finite_values(bad):
    with pytest.raises(client_mod.InvalidPrice):
        client_mod.price_amount(bad)


def test_price_amount_never_leaks_a_decimal_error():
    """A huge exponent must not raise a bare ``decimal.InvalidOperation``."""
    for bad in (Decimal("1E+400"), Decimal("-1E+400"), Decimal("1E+4000000")):
        with pytest.raises(client_mod.InvalidPrice):
            client_mod.price_amount(bad)


# --- publish: the creation result is never guessed ----------------------------

def test_publish_accepts_any_2xx(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(client_mod, "http_request_raw", lambda *a, **k: (201, {"id": "abc123"}))
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpeg")
    client = Wallapop(token="tk")
    resp = client.publish([str(photo)], "T", 5.0)
    assert resp.ok
    assert resp.data["id"] == "abc123"


def test_publish_reports_an_unknown_creation_when_the_id_is_missing(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(client_mod, "http_request_raw", lambda *a, **k: (200, {"flags": {}}))
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpeg")
    client = Wallapop(token="tk")
    resp = client.publish([str(photo)], "T", 5.0)
    assert not resp.ok
    assert resp.partial is False
    assert resp.data["created"] == "unknown"
    assert resp.data["outcome"] == "unknown"
    assert "before publishing it again" in resp.data["error_message"]


def test_publish_keeps_the_item_id_when_an_extra_photo_raises(fake, monkeypatch, tmp_path):
    calls_raw = []

    def fake_raw(method, url, raw_body=None, headers=None, retries=3, timeout=60, retry_safe=None):
        calls_raw.append((method, url))
        if "/picture2" in url:
            raise http_mod.ApiError("storage exploded")
        return 200, {"id": "abc123"}

    monkeypatch.setattr(client_mod, "http_request_raw", fake_raw)
    photos = []
    for i in range(3):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(b"jpeg")
        photos.append(str(p))
    client = Wallapop(token="tk")
    resp = client.publish(photos, "T", 5.0)
    assert not resp.ok
    assert resp.partial is True
    data = resp.data
    # the listing *was* created: its id must never be lost
    assert data["id"] == "abc123"
    assert data["item_id"] == "abc123"
    assert data["created"] is True
    assert data["photos_total"] == 3
    assert data["photos_completed"] == 1
    assert data["failed_photo_index"] == 1
    assert data["failed_photo_order"] == 2
    assert "abc123" in data["retry"]


def test_publish_says_the_outcome_is_unknown_after_a_network_error(fake, monkeypatch, tmp_path):
    def fake_raw(method, url, raw_body=None, headers=None, retries=3, timeout=60, retry_safe=None):
        if "/picture2" in url:
            raise http_mod.UnknownOutcomeError("socket timeout while sending the photo")
        return 200, {"id": "abc123"}

    monkeypatch.setattr(client_mod, "http_request_raw", fake_raw)
    photos = []
    for i in range(2):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(b"jpeg")
        photos.append(str(p))
    client = Wallapop(token="tk")
    resp = client.publish(photos, "T", 5.0)
    assert not resp.ok
    assert resp.partial is True
    assert resp.data["item_id"] == "abc123"
    assert resp.data["outcome"] == "unknown"


def test_publish_says_the_photo_was_not_sent_when_it_could_not_be_read(fake, monkeypatch, tmp_path):
    def fake_raw(method, url, raw_body=None, headers=None, retries=3, timeout=60, retry_safe=None):
        if "/picture2" in url:
            raise OSError("cannot read the file")
        return 200, {"id": "abc123"}

    monkeypatch.setattr(client_mod, "http_request_raw", fake_raw)
    photos = []
    for i in range(2):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(b"jpeg")
        photos.append(str(p))
    client = Wallapop(token="tk")
    resp = client.publish(photos, "T", 5.0)
    assert not resp.ok
    assert resp.partial is True
    assert resp.data["item_id"] == "abc123"
    assert resp.data["outcome"] == "not_sent"


def test_publish_refuses_to_guess_the_cost_configuration(fake, monkeypatch, tmp_path):
    fake.published_items = {"data": [{"id": "x", "shipping": {"item_is_shippable": True}}]}
    calls_raw = []
    monkeypatch.setattr(client_mod, "http_request_raw", lambda *a, **k: calls_raw.append(a))
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpeg")
    client = Wallapop(token="tk")
    resp = client.publish([str(photo)], "T", 5.0)
    assert not resp.ok
    assert "cost_configuration_id" in resp.data["error_message"]
    assert calls_raw == []


def test_publish_uses_the_given_cost_configuration(fake, monkeypatch, tmp_path):
    calls_raw = []

    def fake_raw(method, url, raw_body=None, headers=None, retries=3, timeout=60, retry_safe=None):
        calls_raw.append((method, url, raw_body))
        return 200, {"id": "abc123"}

    monkeypatch.setattr(client_mod, "http_request_raw", fake_raw)
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpeg")
    client = Wallapop(token="tk")
    resp = client.publish([str(photo)], "T", 5.0, cost_configuration_id="cfg-1")
    assert resp.ok
    item = published_item_json(calls_raw[0][2])
    assert item["delivery"]["cost_configuration_id"] == "cfg-1"
    # nothing had to be inferred: the account's listings were not even read
    assert not any("/users/wz0123456789/items" in c[1] for c in fake.calls)


@pytest.mark.parametrize("cost_configuration_id", [None, "unused-shipping-config"])
def test_publish_in_person_without_existing_listings(fake, monkeypatch, tmp_path, cost_configuration_id):
    fake.published_items = {"data": []}
    calls_raw = []

    def fake_raw(method, url, raw_body=None, **kwargs):
        calls_raw.append(raw_body)
        return 200, {"id": "local-sale"}

    monkeypatch.setattr(client_mod, "http_request_raw", fake_raw)
    client = Wallapop(token="tk")
    monkeypatch.setattr(
        client, "_cost_configuration_id",
        lambda: pytest.fail("an in-person listing must not look up shipping configuration"),
    )
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"jpeg")
    result = client.publish(
        [str(photo)], "T", 300, shipping_allowed=False,
        max_weight_kg=5, cost_configuration_id=cost_configuration_id,
    )
    assert result.ok and result.data["id"] == "local-sale"
    assert len(calls_raw) == 1
    assert published_item_json(calls_raw[0])["delivery"] == {"allowed_by_user": False}
    assert not any("/users/wz0123456789/items" in call[1] for call in fake.calls)


# --- edit: the listing is preserved, never rewritten -------------------------

def test_edit_item_preserves_the_current_state(fake):
    fake.owned_item = RAW_OWNED_ITEM
    client = Wallapop(token="tk")
    resp = client.edit_item(RAW_ITEM["id"], title="Nintendo DS Lite")
    assert resp.ok
    body = next(c for c in fake.calls if c[0] == "PUT")[3]
    assert body["category_leaf_id"] == "10088"
    assert body["attributes"]["condition"] == "good"
    assert body["attributes"]["price_amount"] == 45.0
    assert body["delivery"]["allowed_by_user"] is True
    assert body["delivery"]["max_weight_kg"] == 5
    assert body["delivery"]["cost_configuration_id"] == "814429d6-0000-0000-0000-000000000000"
    assert body["location"]["latitude"] == 40.4168
    assert body["location"]["longitude"] == -3.7038


def test_edit_item_does_not_enable_shipping(fake):
    fake.owned_item = {**RAW_OWNED_ITEM, "shipping": {"item_is_shippable": False, "user_allows_shipping": False}}
    client = Wallapop(token="tk")
    resp = client.edit_item(RAW_ITEM["id"], title="Nintendo DS Lite")
    assert resp.ok
    body = next(c for c in fake.calls if c[0] == "PUT")[3]
    assert body["delivery"]["allowed_by_user"] is False
    assert "cost_configuration_id" not in body["delivery"]


def test_edit_item_requires_known_shipping_consent(fake):
    fake.owned_item = {**RAW_OWNED_ITEM, "shipping": {"item_is_shippable": True}}
    response = Wallapop(token="tk").edit_item(RAW_ITEM["id"], title="nuevo")
    assert not response.ok
    assert "user allows shipping" in response.error_reason
    assert not [call for call in fake.calls if call[0] == "PUT"]


def test_edit_item_nonshipping_does_not_require_weight(fake):
    fake.owned_item = {
        **RAW_OWNED_ITEM,
        "shipping": {"user_allows_shipping": False},
        "type_attributes": {},
    }
    response = Wallapop(token="tk").edit_item(RAW_ITEM["id"], title="nuevo")
    assert response.ok
    body = next(call for call in fake.calls if call[0] == "PUT")[3]
    assert body["delivery"] == {"allowed_by_user": False}


@pytest.mark.parametrize("images", [None, [], [{"urls": {"small": "https://example.test/a.jpg"}}]])
def test_edit_item_requires_known_picture_ids(fake, images):
    fake.owned_item = {**RAW_OWNED_ITEM, "images": images}
    response = Wallapop(token="tk").edit_item(RAW_ITEM["id"], title="nuevo")
    assert not response.ok
    assert "picture IDs" in response.error_reason
    assert not [call for call in fake.calls if call[0] == "PUT"]


def test_edit_item_rejects_fractional_shipping_weight(fake):
    fake.owned_item = {**RAW_OWNED_ITEM, "type_attributes": {"up_to_kg": "1.5"}}
    response = Wallapop(token="tk").edit_item(RAW_ITEM["id"], title="nuevo")
    assert not response.ok
    assert "whole number" in response.error_reason
    assert not [call for call in fake.calls if call[0] == "PUT"]


def test_edit_item_does_not_move_the_item_to_the_account_location(fake):
    fake.owned_item = RAW_OWNED_ITEM
    client = Wallapop(token="tk")
    resp = client.edit_item(RAW_ITEM["id"], title="Nintendo DS Lite")
    assert resp.ok
    body = next(c for c in fake.calls if c[0] == "PUT")[3]
    assert body["location"]["latitude"] == 40.4168
    assert body["location"]["longitude"] == -3.7038
    # the account location is never used to relocate the listing
    assert not any(c[1].endswith("/users/me") for c in fake.calls)


@pytest.mark.parametrize(
    "missing,expected",
    [
        ("category_leaf_id", "category_leaf_id"),
        ("condition", "condition"),
        ("location", "location"),
        ("weight", "weight"),
    ],
)
def test_edit_item_refuses_when_the_current_state_cannot_be_inferred(fake, missing, expected):
    item = {**RAW_OWNED_ITEM}
    item.pop(missing, None)
    if missing == "weight":
        item["type_attributes"] = {}
    fake.owned_item = item
    client = Wallapop(token="tk")
    resp = client.edit_item(RAW_ITEM["id"], title="nuevo")
    assert not resp.ok
    assert expected in resp.data["error_message"]
    # no default is applied and nothing is sent
    assert not [c for c in fake.calls if c[0] == "PUT"]


def test_edit_item_refuses_when_a_shipping_item_has_no_known_cost_configuration(fake):
    fake.owned_item = {**RAW_OWNED_ITEM, "shipping": {"item_is_shippable": True, "user_allows_shipping": True}}
    fake.published_items = {"data": [{"id": "x", "shipping": {}}]}
    client = Wallapop(token="tk")
    resp = client.edit_item(RAW_ITEM["id"], title="nuevo")
    assert not resp.ok
    assert "cost_configuration_id" in resp.data["error_message"]
    assert not [c for c in fake.calls if c[0] == "PUT"]


# --- chat delivery -----------------------------------------------------------

class UnknownDeliveryTransport(FakeTransport):
    """Fake transport whose PubNub publish dies *after* being sent."""

    def __call__(self, method, url, params=None, body=None, headers=None, retry_safe=None):
        if "publish" in url:
            raise http_mod.UnknownOutcomeError("socket timeout while sending the message")
        return super().__call__(method, url, params=params, body=body, headers=headers, retry_safe=retry_safe)


UNSORTED_THREAD = {
    **RAW_THREAD,
    "messages": {
        "messages": [
            {"timestamp": 1760000002000, "from_self": True, "type": "text", "text": "hola"},
            {"timestamp": 1760000001000, "from_self": True, "type": "text", "text": "otro"},
        ]
    },
}


class UnorderedThreadTransport(FakeTransport):
    """Fake transport whose thread is *not* sorted by timestamp."""

    def __call__(self, method, url, params=None, body=None, headers=None, retry_safe=None):
        if "/bff/messaging/conversation/" in url:
            self.calls.append((method, url, dict(params or {}), dict(body or {})))
            self.retry_safe.append(retry_safe)
            return 200, UNSORTED_THREAD, "application/json"
        return super().__call__(method, url, params=params, body=body, headers=headers, retry_safe=retry_safe)


def test_send_message_returns_a_compact_confirmed_answer(fake, monkeypatch):
    class NoSleepClock:
        """Stand-in for the ``time`` module: it records the sleeps."""

        def __init__(self) -> None:
            self.sleeps: list = []

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)

        def monotonic(self) -> float:
            return 0.0

    clock = NoSleepClock()
    monkeypatch.setattr(client_mod, "time", clock)
    client = Wallapop(token=JWT)
    report = {}
    resp = client.send_message(RAW_ITEM["id"], "Hola!", report=report)
    assert resp.ok
    assert resp.data["delivery"] == "confirmed"
    assert resp.data["outcome"] == "confirmed"
    assert resp.data["conversation_id"] == "wzy050ee4e65"
    assert resp.data["message"] == "Hola!"
    assert resp.data["with_user"]["name"] == "pixelia"
    # the acknowledgement is enough: no extra GET is needed
    thread_calls = [c for c in fake.calls if "/bff/messaging/conversation/" in c[1]]
    assert len(thread_calls) == 1
    # and no fixed wait after the acknowledgement
    assert clock.sleeps == []
    assert report["path"] == "rest"
    assert report["delivery"] == "confirmed"


def test_send_message_reports_an_unknown_delivery_after_an_ambiguous_send(monkeypatch):
    transport = UnknownDeliveryTransport()
    monkeypatch.setattr(client_mod, "http_request", transport)
    monkeypatch.setattr(
        client_mod,
        "send_chat_text",
        lambda *a, **k: pytest.fail("an ambiguous send must never be retransmitted"),
    )
    client = Wallapop(token=JWT)
    report = {}
    resp = client.send_message(RAW_ITEM["id"], "hola", report=report, allow_browser_fallback=True)
    assert not resp.ok
    assert resp.data["delivery"] == "unknown"
    assert resp.data["outcome"] == "unknown"
    assert resp.data["conversation_id"] == "wzy050ee4e65"
    assert "inspect the conversation before sending the message again" in resp.data["error_message"]
    assert report["path"] == "unknown"
    assert report["delivery"] == "unknown"


def test_send_message_error_asks_to_inspect_the_conversation(monkeypatch):
    transport = NoAckTransport()
    monkeypatch.setattr(client_mod, "http_request", transport)
    client = Wallapop(token=JWT)
    resp = client.send_message(RAW_ITEM["id"], "hola")
    assert not resp.ok
    assert "inspect the conversation before sending the message again" in resp.data["error_message"]


def test_send_message_duplicate_guard_compares_the_newest_message(monkeypatch):
    transport = UnorderedThreadTransport()
    monkeypatch.setattr(client_mod, "http_request", transport)
    client = Wallapop(token=JWT)
    report = {}
    resp = client.send_message(RAW_ITEM["id"], "hola", report=report)
    assert report.get("skipped") is True
    assert report.get("path") == "skipped"
    assert not any("publish" in c[1] for c in transport.calls)


def test_send_message_duplicate_guard_ignores_older_messages(monkeypatch):
    transport = UnorderedThreadTransport()
    monkeypatch.setattr(client_mod, "http_request", transport)
    client = Wallapop(token=JWT)
    report = {}
    client.send_message(RAW_ITEM["id"], "otro", report=report)
    assert report.get("skipped") is False
    assert any("publish" in c[1] for c in transport.calls)


# --- CLI: the browser publish keeps the cents --------------------------------

def test_cli_browser_publish_keeps_the_price_cents(monkeypatch):
    import wallapop_mcp.publish as publish_mod
    from wallapop_mcp.cli import commands

    captured: dict = {}

    def fake_publish_item(**kwargs):
        captured.update(kwargs)
        return {"url": "https://www.wallapop.com/item/published", "item_id": "abc123"}

    monkeypatch.setattr(publish_mod, "publish_item", fake_publish_item)
    args = argparse.Namespace(
        mode="browser",
        price=19.95,
        photos_dir="/tmp/photos",
        summary="Nintendo DS Lite",
        condition="good",
        weight_tier="0",
        max_photos=5,
        headless=True,
        capture_body=None,
    )
    assert commands.cmd_publish(args) == 0
    assert captured["price"] == 19.95


def test_cli_message_prints_the_unknown_delivery_guidance(monkeypatch, capsys):
    from wallapop_mcp.cli import commands

    class FakeClient:
        def send_message(self, item_hash, text, report=None, allow_browser_fallback=False):
            if report is not None:
                report.update({"path": "unknown", "skipped": False, "delivery": "unknown"})
            return client_mod.Response(
                0,
                {
                    "error_message": "chat delivery is unknown",
                    "delivery": "unknown",
                    "outcome": "unknown",
                    "conversation_id": "wzy050ee4e65",
                },
                False,
                False,
            )

    monkeypatch.setattr(commands, "resolve_internal_id", lambda x: x)
    monkeypatch.setattr(commands, "authed_client", lambda required=True: (FakeClient(), {}))
    args = argparse.Namespace(
        item_id=RAW_ITEM["id"], text="hola", dry_run=False, browser_fallback=False
    )
    assert commands.cmd_message(args) == 1
    err = capsys.readouterr().err
    assert "inspect the conversation before sending the message again" in err
    assert "wzy050ee4e65" in err


# --- regression tests for the live-API fixes (publish color / plain edit GET) ---

RAW_PLAIN_OWNED_ITEM = {
    "id": "v6gpwy9d476e",
    "title": {"original": "Ratón Trackball Nulea"},
    "price": {"cash": {"amount": 38.0, "currency": "EUR"}},
    "condition": None,  # the plain GET leaves the top level empty...
    "taxonomy": [
        {"id": "24200", "name": "Tecnología y electrónica"},
        {"id": "10311", "name": "Ratones"},  # ...the leaf is the last node
    ],
    "type_attributes": {  # ...and nests the attributes here as {"value": ...}
        "brand": {"value": "Nulea"},
        "model": {"value": ""},
        "color": {"value": "black"},
        "condition": {"value": "as_good_as_new"},
        "up_to_kg": {"value": "1.0"},
    },
    "description": {"original": "Trackball como nuevo"},
    "shipping": {
        "item_is_shippable": True,
        "user_allows_shipping": True,
        "cost_configuration_id": "cfg-plain-123",
    },
    "location": {"latitude": 42.87, "longitude": -8.55, "approximated": True},
    "images": [{"id": "img-1"}, {"id": "img-2"}],
}


def test_edit_item_reads_the_plain_get_shape(fake):
    """A plain GET /items/<id> nests attributes under type_attributes and the
    category under taxonomy; edit_item must still build a correct PUT body."""
    fake.owned_item = RAW_PLAIN_OWNED_ITEM
    resp = Wallapop(token="tk").edit_item(RAW_ITEM["id"], price=35)
    assert resp.ok
    body = next(c for c in fake.calls if c[0] == "PUT")[3]
    assert body["category_leaf_id"] == "10311"            # taxonomy[-1].id
    assert body["attributes"]["condition"] == "as_good_as_new"  # type_attributes
    assert body["attributes"]["brand"] == "Nulea"
    assert body["attributes"]["color"] == "black"
    assert body["attributes"]["description"] == "Trackball como nuevo"  # plain string
    assert body["attributes"]["price_amount"] == 35.0
    assert body["delivery"]["allowed_by_user"] is True
    assert body["delivery"]["max_weight_kg"] == 1          # up_to_kg {"value": "1.0"}
    assert body["delivery"]["cost_configuration_id"] == "cfg-plain-123"
    assert body["location"]["latitude"] == 42.87
    assert [p["id"] for p in body["pictures"]] == ["img-1", "img-2"]


def _publish_item_json(monkeypatch, tmp_path, **kwargs):
    import json as _json
    import re as _re

    calls_raw = []

    def fake_raw(method, url, raw_body=None, headers=None, retries=3, timeout=60):
        calls_raw.append((method, url, raw_body))
        return 200, {"id": "ab12cd34ef56", "flags": {}}

    monkeypatch.setattr(client_mod, "http_request_raw", fake_raw)
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    client = Wallapop(token="tk")
    resp = client.publish([str(photo)], "Trackball", 38.0, category_leaf_id="10311", **kwargs)
    assert resp.ok
    raw = calls_raw[0][2].replace(b"\xff\xd8\xff\xe0fakejpeg", b"")
    item_part = _re.search(r'name="item"\r\n\r\n(\{.*?\})\r\n--', raw.decode("utf-8", "replace"))
    assert item_part is not None
    return _json.loads(item_part.group(1))


def test_publish_omits_color_when_empty(fake, monkeypatch, tmp_path):
    """Categories with no color attribute (e.g. 10311) 400 when color is sent,
    even empty; publish must leave the key out entirely when no color is given."""
    item = _publish_item_json(monkeypatch, tmp_path)  # no color
    assert "color" not in item["attributes"]


def test_publish_keeps_color_when_given(fake, monkeypatch, tmp_path):
    item = _publish_item_json(monkeypatch, tmp_path, color="black")
    assert item["attributes"]["color"] == "black"


# --- seller-safe reply_to_conversation (issue #3: send_message 403s on own item) ---

_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJoYXNoZWRfc3ViIjoid3p5bm92bXYxcGo1In0.xx"


def test_reply_to_conversation_publishes_without_ensure_conversation(fake):
    client = Wallapop(token="tk")
    client.token = _JWT
    report = {}
    resp = client.reply_to_conversation("conv-xyz", "Gracias, te la reservo", report=report)
    assert resp.ok
    assert report.get("path") == "rest"
    # the seller-safe path must NOT POST a conversation (that 403s on your own item)
    assert not any(
        c[0] == "POST" and c[1].endswith("/instant-messaging/conversation") for c in fake.calls
    )


def test_reply_to_conversation_skips_duplicate(fake):
    fake.thread_same = True  # thread already ends with our own identical message
    client = Wallapop(token="tk")
    client.token = _JWT
    report = {}
    resp = client.reply_to_conversation("conv-xyz", "Y tienes Super Mario 3D Land?", report=report)
    assert report.get("skipped") is True


def test_reply_to_conversation_requires_an_id(fake):
    resp = Wallapop(token="tk").reply_to_conversation("", "hola")
    assert not resp.ok
    assert "conversation_id required" in resp.data["error_message"]


# --- suggestions / seller extras --------------------------------------------

def test_suggest_returns_suggestion_texts(fake):
    client = Wallapop()
    suggs = client.suggest("nintendo sw", limit=1)
    assert len(suggs) == 1
    assert suggs[0]["text"] == "switch nintendo"
    assert suggs[0]["highlight"] == "nintendo switch"
    assert suggs[0]["subtitle"] == ""


def test_seller_extra_info_parsed(fake):
    client = Wallapop()
    info = client.seller_extra_info("wz0123456789")
    assert info["response_rate"] == "less_than_one_hour"
    assert info["rating_average"] == 4.9
    assert info["validations"]["level"] == "verified"


def test_other_items_normalized(fake):
    client = Wallapop()
    items = client.other_items(RAW_ITEM["id"])
    assert len(items) == 1
    assert items[0].id == RAW_ITEM["id"]
    assert items[0].title == "Nintendo DS Lite Blanca + Funda"


def test_seller_phone_number(fake):
    client = Wallapop(token="tk")
    assert client.seller_phone_number(RAW_ITEM["id"]) == "+34 600 000 000"


def test_item_actions_allowed(fake):
    client = Wallapop()
    actions = client.item_actions_allowed(RAW_ITEM["id"])
    assert actions[0]["type"] == "inactivate"
    assert actions[0]["allowed"] is True
    assert actions[1]["type"] == "delete"


# --- seller items -----------------------------------------------------------

def test_seller_items_paginates_with_since(fake):
    client = Wallapop()
    items = client.seller_items("wz0123456789", pages=2)
    user_calls = [c for c in fake.calls if "/users/wz0123456789/items" in c[1]]
    assert len(user_calls) == 2
    assert user_calls[0][2].get("page") == 0
    assert user_calls[0][2].get("since") is None
    assert user_calls[1][2].get("since") == "page-cursor-2"
    assert len(items) == 2
    assert items[0].id == RAW_PUBLISHED_ITEM["id"]


def test_seller_items_stops_when_no_next(fake):
    client = Wallapop()
    items = client.seller_items("wz0123456789", pages=5)
    user_calls = [c for c in fake.calls if "/users/wz0123456789/items" in c[1]]
    assert len(user_calls) == 2  # breaks on missing meta.next despite the page budget
    assert len(items) == 2


# --- favorites list -----------------------------------------------------------

def test_favorite_lists_paginates(fake):
    client = Wallapop(token="tk")
    items = client.favorite_lists(pages=2)
    fav_calls = [c for c in fake.calls if c[1].endswith("/favorite-lists") and c[0] == "GET"]
    assert len(fav_calls) == 2
    assert fav_calls[1][2] == {"since": "fav-cursor-2"}
    assert len(items) == 1
    assert items[0].id == RAW_ITEM["id"]


def test_favorite_list_add_sends_items_body(fake):
    client = Wallapop(token="tk")
    r = client.favorite_list_add("list1", RAW_ITEM["id"])
    assert r.ok
    call = fake.calls[-1]
    assert call[0] == "POST"
    assert call[1].endswith("/favorite-lists/list1/items")
    assert call[3] == {"items": [{"id": RAW_ITEM["id"], "is_favorited": True}]}


def test_favorite_list_remove(fake):
    client = Wallapop(token="tk")
    r = client.favorite_list_remove("list1", RAW_ITEM["id"])
    assert r.ok
    call = fake.calls[-1]
    assert call[0] == "DELETE"
    assert call[1].endswith(f"/favorite-lists/list1/items/{RAW_ITEM['id']}")


# --- saved searches (alerts) -------------------------------------------------

def test_save_search_parses_location_header(fake):
    client = Wallapop(token="tk")
    search_id = client.save_search({"keywords": "steam deck", "max_sale_price": 300})
    assert search_id == "ae1a42fb-e6a7-4507-ae3f-3dd3139aa69d"
    call = fake.calls[-1]
    assert call[0] == "POST"
    assert call[1].endswith("/searchalerts/savedsearch")
    assert call[2] == {"keywords": "steam deck", "max_sale_price": 300}


def test_saved_search_exists_returns_id(fake):
    client = Wallapop(token="tk")
    search_id = client.saved_search_exists({"keywords": "ps5"})
    assert search_id == "ae1a42fb-e6a7-4507-ae3f-3dd3139aa69d"


def test_saved_search_exists_missing_returns_none(fake):
    client = Wallapop(token="tk")
    search_id = client.saved_search_exists({"keywords": "not-saved-thing"})
    assert search_id is None


def test_saved_search_hits(fake):
    client = Wallapop(token="tk")
    assert client.saved_search_hits() == 7


def test_remove_saved_search(fake):
    client = Wallapop(token="tk")
    r = client.remove_saved_search("ae1a42fb-e6a7-4507-ae3f-3dd3139aa69d")
    assert r.ok
    call = fake.calls[-1]
    assert call[0] == "DELETE"
    assert call[1].endswith("/searchalerts/savedsearch/ae1a42fb-e6a7-4507-ae3f-3dd3139aa69d")


# --- item lifecycle -----------------------------------------------------------

def test_inactivate_item(fake):
    client = Wallapop(token="tk")
    r = client.inactivate_item(RAW_ITEM["id"])
    assert r.ok
    call = fake.calls[-1]
    assert call[0] == "PUT"
    assert call[1].endswith(f"/items/{RAW_ITEM['id']}/inactivate")


def test_mark_viewed(fake):
    client = Wallapop()
    r = client.mark_viewed(RAW_ITEM["id"])
    assert r.ok
    call = fake.calls[-1]
    assert call[1].endswith(f"/items/{RAW_ITEM['id']}/view")
