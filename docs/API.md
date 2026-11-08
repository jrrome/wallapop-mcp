# wallapop_mcp — library API (v1 contract)

Talks to Wallapop over plain HTTP (no browser, standard library only) and
backs both the MCP server (`wallapop-mcp`) and the CLI (`wallapop`). Local
development install:

```
pip install -e '.[test]'
```

The `[browser]` extra (Playwright) enables browser login and publishing, plus an
explicit chat fallback. A send with an unknown delivery outcome is never retried
automatically. Normal communication adds no dependencies beyond the package.

## Quickstart

```python
from wallapop_mcp import Wallapop, load_credentials, load_session

ses = load_session()  # or load_credentials() for an email login
client = Wallapop(
    token=ses.get("accessToken"),
    web_cookies=ses if ses.get("__Secure-next-auth.session-token") else None,
)

# Search and read typed results (Spain by default)
items = client.search("nintendo switch lite", pages=2, delay=1.0)
for it in items:
    print(it.title, it.price, it.url, it.shippable)

# Make an offer
resp = client.send_offer(items[0].id, 90.0, message="Hi, would you take 90?")
print(resp.ok, resp.data)

# Handle received offers (accept / decline / counter)
client.offer_response(offer_id, "accept")          # ACCEPTED
client.offer_response(offer_id, "decline")         # DECLINED
client.counter_offer(offer_id, 75.0)               # PATCH offer-price 75.00

# Chat (PubNub REST; idempotent: never re-sends the same text)
client.send_message(items[0].id, "Hi! Does it come with the charger?")

# Favorite / unfavorite
client.favorite(items[0].id)            # favorite (PUT .../favorite → 204)
client.favorite(items[0].id, False)     # unfavorite

# Sniper: save a search and check for new matches (ids are UUIDs)
search_id = client.save_search({"keywords": "nintendo switch", "max_sale_price": 300})
print(search_id)                        # 87b2e4fc-b3c9-4351-81af-43e6788aafa5
client.saved_search_exists({"keywords": "nintendo switch", "max_sale_price": 300})
client.saved_search_hits()              # 0 (pending new matches)
client.remove_saved_search(search_id)

# A seller's / an item's extras
client.seller_items("9jdxxdy1v2zk", pages=2)   # all their listings (cursor: meta.next)
client.suggest("nintendo sw")                  # the web's autocomplete

# Purchases and sales (SDUI API behind the web's hubs)
from wallapop_mcp import parse_sdui_transactions

raw = client.purchases("finished")      # GET /sdui/purchases/finished (widget tree)
rows = parse_sdui_transactions(raw)     # list[dict]: id, title, detail, price, ...
sales = client.selling("ongoing")       # GET /sdui/selling/ongoing
```

### Item images

```python
it = client.item(items[0].id)      # it.images -> medium-size photo URLs
print(it.images[0])                # https://cdn.wallapop.com/images/...?pictureSize=W640

# Download them (standard library only)
paths = client.download_images(it, "downloads")
print(paths)                       # ['downloads/abc_1.jpg', ...]
```

## Public dataclasses

See `wallapop_mcp/models.py`.

| Type | Fields | Use |
| --- | --- | --- |
| `Item` | `id, title, price: float, currency, city, user_id, user_name, url, shippable: bool`, `reserved`, `sold`, `category + category_name`, `created_at`, `images: list[str]` | A normalized listing (`images` = medium photo URLs) |
| `Deal` | `item: Item, discount: float, median: float` | An `Item` priced below the market median |
| `OfferEvent` | `kind, amount: float\|None, offer_id: str\|None, date, ts, text` | One negotiation ledger entry (one per offer event in the thread) |
| `OfferState` | `kind, label, amount, offer_id, actionable: bool` | Current state derived from the latest event |

`OfferEvent`/`OfferState` kinds: `our-offer` · `seller-counter` · `our-counter`
· `seller-accepted` · `accepted-buy` · `seller-declined` · `our-declined` ·
`expired`.

## Domain functions (with or without raw data)

```python
from wallapop_mcp import parse_offer_events, offer_state_summary, rank_deals

events = parse_offer_events(thread_messages)   # list[OfferEvent], sorted by timestamp
state = offer_state_summary(events)            # OfferState | None
deals, median = rank_deals(items, min_discount=0.15, max_price=None)
```

- `parse_offer_events`: rebuilds the thread's ledger and forward-fills
  `amount`/`offer_id` into terminal events (which do not carry them).
- `offer_state_summary`: pure state (no terminal colors); `actionable` is only
  `True` when a counter-offer is waiting for your answer.
- `rank_deals`: median of the candidate set + ranking by discount.
- `parse_sdui_transactions`: flattens the `list_item_transaction` cards of the
  Purchases/Sales SDUI tree (ignores grouping headers and `empty_state`).

## Client (`Wallapop`)

Constructor: `Wallapop(token=None, refresh_token=None, email=None, persist_fn=None, web_cookies=None)`.

| Method | Endpoint |
| --- | --- |
| `item(item_id)` → `Item` | `GET /items/{id}` |
| `seller(user_id)` → `dict` | `GET /users/{id}` |
| `seller_stats(user_id)` → `dict` | `GET /users/{id}/stats` (rating_average + review counters) |
| `seller_reviews(user_id)` → `list[dict]` | `GET /users/{id}/reviews` |
| `seller_extra_info(user_id)` → `dict` | `GET /users/{id}/extra-info` (response_rate, scoring_stars, validations, activity_level) |
| `seller_items(user_id, pages=1, max_items=25)` → `list[Item]` | `GET /users/{id}/items` (public; cursor pagination `meta.next` → `since`) |
| `other_items(item_id)` → `list[Item]` | `GET /items/{id}/seller/other-items` (more listings from the same seller) |
| `seller_phone_number(item_id)` → `str` | `GET /items/{id}/seller-phone-number` (`""` when unavailable) |
| `item_actions_allowed(item_id)` → `list[dict]` | `GET /items/{id}/actions-allowed` (`[{type, allowed, cause}]`) |
| `suggest(keyword, limit=10)` → `list[dict]` | `GET /suggesters/nonlocated` (autocomplete: `[{text, highlight, subtitle}]`) |
| `download_images(item, dest="images")` → `list[str]` | downloads `item.images` into `dest` and returns the saved paths |
| `search(keywords, category_id?, subcategory_ids?, order_by?, latitude?, longitude?, pages?, delay?, condition?, min_price?, max_price?, ship_only?)` → `list[Item]` | `GET /search/components` + `/search/section` (or the `/general/search` fallback). Without `latitude`/`longitude` it defaults to Spain (40.0, -3.5); pass both to anchor elsewhere. With `subcategory_ids` and no `category_id`, the tree root is resolved (`category_for_subcategory`); a mid-level `category_id` is normalized to its top level. Skips empty pages that still carry `next_page` (e.g. `ship_only` + `price_asc`). |
| `category_for_subcategory(subcategory_id)` → `Optional[int]` | top-level root of the node in `/categories` (e.g. `10088` → `24200`) |
| `top_level_category(category_id)` → `int` | the id itself if top-level; a mid-level node climbs to its root; unknown ids are returned unchanged |
| `send_offer(item_id, amount, message?)` | `POST /api/v3/delivery/buyer/offers` (client-generated uuid offer_id) |
| `offer_response(offer_id, action)` — `accept`/`decline` | `PATCH /delivery/offers/{id}/status` |
| `counter_offer(offer_id, amount, currency="EUR")` | `PATCH /delivery/offers/{id}/offer-price` |
| `offer_details(offer_id)` / `make_offer_info(item_id)` | BFF (subject to anti-bot checks) |
| `ensure_conversation(item_hash)` / `conversation_thread(conv_id)` / `inbox()` / `unread_count()` | messaging |
| `send_message(item_hash, message, report?, allow_browser_fallback=False)` | PubNub REST publish; browser fallback only when enabled and the send was not ambiguous |
| `reply_to_conversation(conv_id, message, report?)` | publish straight to an existing conversation (seller side) |
| `publish(photo_paths, title, price, category_leaf_id?, condition?, description?, brand?, model?, color?, max_weight_kg?, cost_configuration_id?, shipping_allowed=True)` → `Response` | publishes a listing through the pure API, replicating the `POST /api/v3/items` multipart (photo + `item` JSON). Set `shipping_allowed=False` for in-person sale only; shipping weight and cost configuration are then ignored. `Response.data` = `{"id": <item id>, "flags": {}}` |
| `favorite(item_id, favorited=True)` → `Response` | favorite/unfavorite an item (`PUT /items/{id}/favorite`, body `{"favorited": <bool>}`, 204) |
| `favorite_lists(pages=1)` → `list[Item]` | `GET /favorite-lists` (your favorites; cursor `meta.next` → `since`) |
| `favorite_list_add(list_id, item_id)` / `favorite_list_remove(list_id, item_id)` → `Response` | `POST /favorite-lists/{id}/items` (body `{"items": [{"id", "is_favorited"}]}`) / `DELETE /favorite-lists/{id}/items/{item_id}` |
| `mark_viewed(item_id)` → `Response` | `PUT /items/{id}/view` (registers a view) |
| `inactivate_item(item_id)` → `Response` | pauses one of your listings (`PUT /items/{id}/inactivate`, 403 if it is not yours); editing it puts it back online |
| `reserve(item_id, reserved=True)` → `Response` | reserve/unreserve one of your items (`PUT /items/{id}/reserve`, body `{"reserved": <bool>}`, 204) |
| `mark_sold(item_id, sold=True)` → `Response` | mark one of your items as sold/unsold (`PUT /items/{id}/sold`, 204) |
| `edit_item(item_id, price?, title?, description?, condition?, brand?, model?, color?, category_leaf_id?, max_weight_kg?)` → `Response` | edits a published listing, replicating the web's "Update" form (`PUT /api/v3/items/{id}`) and preserving the fields you do not pass |
| `delete_item(item_id)` → `Response` | **permanently deletes** one of your items (`DELETE /api/v3/items/{id}`, 204) |
| `purchases(tab)` / `selling(tab)` → `dict` | the web's **Purchases/Sales** hubs via SDUI (`GET /sdui/{purchases\|selling}/{ongoing\|finished}`); tab = `"ongoing"`/`"finished"`. Parse the tree with `parse_sdui_transactions` |
| `save_search(params)` → `str` | saves a search (`POST /searchalerts/savedsearch`); returns the **UUID** from the `Location` header |
| `saved_search_exists(params)` → `Optional[str]` | id of the saved search for those filters, or `None` (`HEAD /searchalerts/savedsearch/exists`, 200+`Location` / 404) |
| `saved_search_hits()` → `int` | new-match counter across your saved searches (`GET /searchalerts/savedsearch/alerts/total-hits` → `data.hits`) |
| `remove_saved_search(search_id)` → `Response` | deletes a saved search by UUID (`DELETE /searchalerts/savedsearch/{id}`, 200) |
| `login(email, password)` / `try_refresh()` / `refresh_web()` | session |

`report` (optional dict for `send_message`) receives `{"path": "rest"|"browser"|"skipped", "skipped": bool, "browser_s": float}`.

An in-person-only `publish(..., shipping_allowed=False)` sends
`"delivery": {"allowed_by_user": false}` without weight or cost configuration.
It does not look up existing listings to infer a shipping configuration, so it
also works for accounts without a previously published item. The MCP
`publish_item` tool exposes the same `shipping_allowed` flag.

## Errors

Network operations raise `ApiError` only for transport failures or unusable
responses; everything else returns a `Response`:

```python
from wallapop_mcp import ApiError, Response

resp: Response = client.send_offer("id", 10.0)
if not resp.ok:
    print(resp.status, resp.error_reason)
```

`NotSentError` and `UnknownOutcomeError` (both `ApiError` subclasses) tell a
write that never left the machine from one whose outcome is unknown. Check the
state before retrying the latter.

## Session

- `build_client(required=True)` returns `(client, credentials)` and raises
  `SessionError` when there is no session (it never exits the process);
  `required=False` gives an anonymous client instead.
- `load_credentials()` — dict with `email`, `access_token`, `refresh_token`, `user` (email login).
- `load_session()` — browser cookie dict; `__Secure-next-auth.session-token` enables cookie-based refresh.
- `save_session(data)` — persists the browser session.

Data lives in `~/.config/wallapop-mcp/` (mode `600`), outside any repository.
Override it with `WALLAPOP_CONFIG_DIR`. An existing pre-rename
`~/.config/wallapop_cli/` is still picked up when the new directory does not
exist.

## Geography and country selection

`wallapop_mcp.config.CITIES` maps places → `(lat, lon)`; it backs the default of
`search()` and the CLI's `--city`. It includes Spanish cities and regions plus
countries (`italia`, `portugal`, `francia`, `galicia`, `andalucia`, …).

```python
from wallapop_mcp.config import CITIES, DEFAULT_GEO
lat, lon = CITIES["italia"]          # anchor to Italy
```

- Without `latitude`/`longitude`, `search()` uses `DEFAULT_GEO` (Spain, 40.0,
  -3.5), which reproduces the `es.wallapop.com` search **exactly**. It is not a
  restriction: coordinates from another country return that country's items.
- The country of the results is mostly decided by `search_country` (not sent
  by the library; the backend infers it from the coordinates). With relevance
  ordering the listings come from the anchored country; with `newest` the API
  returns a more global mix, as the app sometimes does.

The full geographic behaviour is described in `docs/ENDPOINTS.md` ("Geographic
behaviour").

## Item references

- `resolve_internal_id(ref)` — turns a URL / slug / 12-char hash into the internal id (`v6gpwy9d476e`).

## Compatibility and reliability

- Python `>= 3.10`. The library itself only uses the standard library; the
  `mcp` SDK is needed for the MCP server.
- Prices keep their cents; a price with more than two decimals raises
  `InvalidPrice` instead of being rounded.
- A `Response.partial` keeps the created id when a later photo upload fails;
  do not recreate the listing.
- HTTP failures on profile endpoints surface as `ApiError` instead of empty
  data. Writes are never retried by the transport.
