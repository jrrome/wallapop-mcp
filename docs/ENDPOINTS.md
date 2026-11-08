# Endpoints

Catalogue of every endpoint the library uses, how it was found and which
client method calls it. **No call needs a browser**: everything is plain HTTP
through `wallapop_mcp/http.py` (`http_request`), with the session token sent as
`Authorization: Bearer <access_token>`.

## Conventions

- API base: `https://api.wallapop.com` (`API_BASE = /api/v3`).
- `BFF` endpoints (the web's Next.js backend) have no `/api/v3`: they live
  under `/bff/...` at the host root.
- `Auth` = needs `Authorization: Bearer`. The client also sends `X-DeviceID`
  when a `device_id` cookie exists, and identifies as the web app with
  `Origin: https://es.wallapop.com`, `X-Product: wallapop`,
  `X-AppVersion: 826560`, `X-DeviceOS: 0`.
- Client helpers (in `wallapop_mcp/client.py`): `_request()` (anonymous) and
  `_authed()` (auth + automatic web-session refresh on `401`); `_bff()` does
  the same under the host root without `/api/v3`.

### App identifiers (constants in `config.py`)

Defined in `wallapop_mcp/config.py`. **They are not secrets**: the web exposes
them in its JS bundle and every browser uses them, so they can be versioned.

- `API_BASE` (`https://api.wallapop.com`) and `API_ROOT` (host root without
  `/api/v3`, for the Next.js BFF endpoints).
- `WEB_BASE` (`https://es.wallapop.com`): web origin, sent as `Origin`/`Referer`.
- `PUB_KEY` (`pub-c-...`) and `SUB_KEY` (`sub-c-...`): the *publish* and
  *subscribe* keys of Wallapop's PubNub app (its real-time chat). They identify
  the channel space; they do not authenticate. The open REST publish with the
  pub key is what makes sending chat messages without a browser possible.
- `PUB_ORIGIN` (`ps7.pndsn.com`): the PubNub broker messages are published to.
- `PNSDK` (`PubNub-JS-Web/10.2.6`): the SDK's self-identification string sent
  with each request.
- `WEB_APP_VERSION` (`8.2656.0`): the simulated web build; the API uses it to
  identify the client (`X-AppVersion: 826560`).

What is sensitive (tokens and cookies) never lives in the repository: it is
stored in `~/.config/wallapop-mcp/{credentials,session}.json`.

**How the endpoints were found:**

- The current search endpoints (`/search/components` + `/search/section`) were
  found by inspecting the web's XHR requests in the browser and cross-checking
  the JS bundle. `/general/search` is the older flow, kept as a working fallback.
- The **offer** endpoints were recovered from the web app bundle:
  `https://web-static.wallapop.com/angular/es/chunk-*.js` (hashes rotate with
  the app version `8.2656.0`). Specifically:
  - `chunk-WF7BPLHU.js` — the offer service defines the routes:
    `R="bff/delivery/offer-details"`, `h="api/v3/delivery/buyer/offers"`,
    `P="api/v3/delivery/offers"`, `U = .../{id}/status`,
    `v = .../{id}/offer-price`.
  - `chunk-THOUZVB2.js` — the OTS modal component defines
    `submitOfferResponse` (maps to `ACCEPTED`/`DECLINED` on `{id}/status`)
    and `submitCounterOffer` (on `{id}/offer-price`).
  Validated live: a `PATCH status` on an already closed offer returns
  `409 invalid offer status` (the endpoint exists and really validates).
- The `bff/messaging/*` endpoints belong to the Next.js SPA; the inbox returns
  the most recent messages **first** (sort by `timestamp`).
- Chat sending through PubNub REST was taken from observing the web: the
  message is published to `ps7.pndsn.com` with `auth` = the token from
  `/instant-messaging/token`, `pnsdk=PubNub-JS-Web/10.2.6`. Acknowledgment:
  `[1,"Sent",<timetoken>]`.

---

## Authentication

### POST `/api/v3/access/login`

The app's email + password login.

- Body: `{"email": ..., "password": ..., "client_id": "wallapop"}`
- No previous token required.
- Response: `data.access_token`, `data.refresh_token`, `data.user`.
- Code: `Wallapop.login()`.

### POST `/api/v3/access/refresh`

Token renewal.

- Body: `{"refresh_token": ..., "client_id": "wallapop"}`
- Response: `data.access_token` (+ a new `refresh_token`).
- Code: `Wallapop.try_refresh()`.

### GET `https://es.wallapop.com/api/auth/session` [cookie]

Turns the browser session (NextAuth) into a usable JWT.

- Needs the `__Secure-next-auth.session-token` cookie (plus `device_id`,
  `device_fgp`, `accessToken` when available).
- Response: JSON with a `token` field (the API JWT, i.e. `accessToken`).
- Code: `refresh_web_session()`.

---

## Search

### GET `/api/v3/search/components`

Search bootstrap.

- Params: `keywords`, `source=search_box`, `order_by`
  (`most_relevance` | `newest` | `price_low_to_high` | `price_high_to_low` |
  `closest`), `filters_source=search_box`, optional `category_id`,
  `subcategory_ids`, `condition`, `min_sale_price`, `max_sale_price`,
  `is_shippable=true`, `latitude`, `longitude`.
- **`category_id` must be top-level** (100, 12800, 24200...). A mid-level node
  id (e.g. `24203`, child of `24200`) is accepted but silently returns
  **0 results**. The client normalizes it to its root with
  `Wallapop().top_level_category()`.
- **`subcategory_ids` without `category_id` returns 400** on this endpoint.
  The client resolves the root automatically with
  `Wallapop().category_for_subcategory()` (e.g. `10088` → `24200`).
- The response carries the `search_id` needed to paginate.
- Code: `Wallapop.search()`.

### GET `/api/v3/search/section`

A results page (what the web renders after the bootstrap).

- Params: the previous ones + `search_id`, `section_type=organic_search_results`,
  `source=deep_link`; for page 2+ add `next_page` (the cursor the API returns
  in its `next_page` field).
- Response: `...items[]` with raw listings.
- **Page 1 can be empty yet carry `next_page`** — it happens with
  `is_shippable=true` + `price_low_to_high`: the API leaves the first page as a
  placeholder (shippable items start on page 2). `_search_sections()` follows
  the cursor even on empty pages and does not count them against `pages`.
- Code: `Wallapop._search_sections()`.

### GET `/api/v3/categories?context=search`

The full search category catalogue — **the canonical source of the web's
sidebar menu** (`es.wallapop.com/search`). Verified with Playwright against the
real sidebar (see "Subcategory verification" below).

- Params: `context=search` (required).
- Response: `{"categories": [...]}` with nodes `{id, name, vertical_id,
  attributes, subcategories[]}`. `subcategories` is **recursive**, with
  `parent_id` at every level; leaves have `subcategories: []`.
- Names come back **localized** to the request locale (`Accept-Language`):
  without a locale the API answers in Spanish (`Coches`, `Motos`…), while a
  browser in `en-*` shows `Cars`, `Motorbike`… Same `id`.
- Code: `Wallapop.categories()`.
- Validated snapshot of the full tree: `docs/categories.json`.

The 18 **top-level** categories (id | name, Spanish locale):

| id | name |
|----|------|
| 100 | Coches (cars) |
| 14000 | Motos (motorbikes) |
| 12800 | Motor y accesorios (motor & accessories) |
| 12465 | Moda y accesorios (fashion & accessories) |
| 200 | Inmobiliaria (real estate) |
| 24200 | Tecnología y electrónica (tech & electronics) |
| 12579 | Deporte y ocio (sports & leisure) |
| 17000 | Bicicletas (bikes) |
| 12467 | Hogar y jardín (home & garden) |
| 13100 | Electrodomésticos (appliances) |
| 12463 | Cine, libros y música (movies, books & music) |
| 12461 | Niños y bebés (kids & babies) |
| 18000 | Coleccionismo (collectibles) |
| 19000 | Construcción y reformas (construction & renovation) |
| 20000 | Industria y agricultura (industry & agriculture) |
| 21000 | Empleo (jobs) |
| 13200 | Servicios (services) |
| 12485 | Otros (other) |

> Categories without subcategories: 100, 14000, 200, 12485.
> `config.CATEGORIES` (the offline name table) uses these real ids; older ids
> (12545, 16000, 15000, 12900…) were obsolete.

### Subcategory verification (Playwright)

Clicking the sidebar tree opens **one submenu per level** and every selection
produces a URL with `subcategory_ids=<id>` **equal to the `id` in the API
tree**. A real capture for category `12800` (Motor y accesorios):

| Menu subcategory (EN) | `subcategory_ids` | API node (ES) |
|---|---|---|
| GPS & electronics | `10164` | GPS y electrónica |
| Car & van spare parts | `10167` | Recambios de coches y furgonetas |
| Mechanical tools | `10165` | Herramientas |
| Motorbike and four-wheeler spare parts | `10168` | Recambios de motos y cuatriciclos |
| Others | `10166` | Otros |

And **two levels deep** — category `24200`, subcategory `Sound` → `Headphones`
(`24149` → `10395`):

```
...search?category_id=24200&subcategory_ids=24149   # Sound
...search?category_id=24200&subcategory_ids=10395   # Headphones (grandchild)
```

Both match the ids of the recursive `/categories` tree. Rule of thumb: **a
sidebar `name` = the localized `name` of the tree node with the same `id`**, so
the CLI's `--subcat` and any web URL's `subcategory_ids` point at the same tree.

### GET `/api/v3/general/search` (legacy)

Older alternative used when `/search/components` returns no `search_id`.

- Params: `keywords`, `order_by`, `filters_source`, `source`, optional
  `category_ids` (plural), `subcategory_ids`, `condition`, `min_sale_price`,
  `max_sale_price`, `is_shippable=true`, `latitude`, `longitude`,
  `distance=1000000`.
- Response: `search_objects[]`.
- Code: `Wallapop._search_legacy()`.

### Geographic behaviour (how country/scope is decided)

Found by reproducing the real browser flow (`es.wallapop.com/search`); it
matters to anyone calling `search()`:

- **Without `latitude`/`longitude` the API returns 0 results.** The backend has
  no default: it needs a geographic anchor. The web "does not notice" because
  the browser **geolocates by IP/session** and always sends coordinates +
  `search_country`.
- **`search_country` weighs the most.** The country of the returned items
  depends mainly on this param (+ coordinates). Verified:
  - `PL coords + search_country=PL` → 0 items (no GameCubes anchored to PL).
  - `PL coords + search_country=ES` → 40 items **from Spain** (country wins
    over coordinates).
  - `ES coords + search_country=ES` → 40 items (39 ES + 1 PT near the border).
- **The country barely filters content**; it only affects ordering (with
  `order_by=closest`) and shipping/currency flags. With
  `order_by=most_relevance` items come from the `search_country`.
- **`order_by=newest` ignores the country** and returns a more global set
  (Spain mixed with other countries), as the app sometimes does.
- **`distance` does not widen the scope** in the `/search/components` +
  `/search/section` flow (tried 1M and 50M: same result). The legacy
  `/general/search` accepts `distance` but currently returns 0 items.

### Library default

`Wallapop.search()` defaults to **Spain** (`CITIES["espana"]` = 40.0, -3.5)
when no `latitude`/`longitude` are given. Verified to return **exactly** the
same items (same ids and order) as `search_country=ES` with Spanish
coordinates, i.e. it reproduces what you would see on `es.wallapop.com` in
Spain. It is not a restriction: coordinates from another country (e.g.
`CITIES["italia"]`) return that country's items.

---

## Listings

### GET `/api/v3/items/{id}`

Public listing details (no auth).

- Response: the item's flat JSON (structured price, `user`, `web_slug`, …).
- `images`: array of `{id, average_color, urls: {small, medium, big}}` with
  `cdn.wallapop.com` URLs (one width per size: W320 / W640 / W800).
  `Item.images` keeps the `medium` URLs (W640).
- Code: `Wallapop.item()`, `Wallapop.download_images()` (`item()` is also used
  to find the currency when sending an offer).

### GET `https://es.wallapop.com/item/{slug}`  [page]

Reference → internal id resolution. The Next.js page embeds its first payload
in `<script id="__NEXT_DATA__" type="application/json">`.

- Reads `data.props.pageProps.item.id` (the literal slug can carry a suffix
  that does not match the id).
- Code: `resolve_internal_id()` (also accepts a URL or a 12-char hash).

### GET `/api/v3/items/{id}/seller/other-items`

More listings from the same seller (the web's "other items" rail).

- Response: `data.items[]` (same item shape as `/items`).
- Code: `Wallapop.other_items()`.

### GET `/api/v3/items/{id}/seller-phone-number`

The seller's phone number when it is visible to you.

- Response: `{"phone_number": "..."}`; `""` when not applicable.
- Code: `Wallapop.seller_phone_number()`.

### GET `/api/v3/items/{id}/actions-allowed`

Which actions you can take on a listing (the web's item page).

- Response: array `[{type, allowed, cause}]` (e.g. `buy`, `send_offer`,
  `message`, `phone`).
- Code: `Wallapop.item_actions_allowed()`.

### PUT `/api/v3/items/{id}/view`

Registers a view (the web calls it when opening an item page). No body.

- Code: `Wallapop.mark_viewed()`.

### PUT `/api/v3/items/{id}/inactivate`

The web profile's "Pause listing" switch. Empty body.

- 204 when it pauses one of your listings; editing the listing
  (`PUT /items/{id}`) puts it back online.
- Returns 403 if the item is not yours (not 404: the service checks ownership,
  not existence).
- Code: `Wallapop.inactivate_item(item_id)`; CLI `wallapop inactivate <item>`;
  MCP `pause_item`. Verified live (403 with someone else's item).

---

## Users and ratings

### GET `/api/v3/users/{user_id}`

The seller's public profile (no auth).

- Key fields: `micro_name` (name), `is_top_profile`, `location`,
  `register_date`, `seller_type`.
- Does NOT include the rating (stars/reviews): that is `/stats` (average and
  total) and `/reviews` (details).
- Code: `Wallapop.seller()`.

### GET `/api/v3/users/{user_id}/reviews`

Detailed seller reviews (no auth).

- Response: array (one per transaction). Each entry has
  `review.rating_over_five` (1-5 stars), `review.scoring` (0-100),
  `review.comments` (text) and `item.title` (what was sold).
- Paginated, without the exact total; use `/stats` for the real average and total.
- Code: `Wallapop.seller_reviews()`.

### GET `/api/v3/users/{user_id}/stats`

Aggregated seller stats (no auth) — **the values the web shows on the item
page** (e.g. "★ 4.9 · 465 reviews").

- `rating_average`: average stars (e.g. `4.9`).
- `counters[]`: list of `{type, value}` including `reviews` (total reviews),
  `publish`, `buys`, `sells`, `sold`.
- Plus `shipping`, `deliveries_on_time_in_days`.
- Code: `Wallapop.seller_stats()`.

### GET `/api/v3/users/{user_id}/extra-info`

Extra seller data the web uses on the item page.

- Response: `response_rate` ("< 1 h"), `response_time`,
  `scoring_stars`/`rating_average`, `validations.level` (e.g. `verified`),
  `activity_level`. Verified live (200).
- Code: `Wallapop.seller_extra_info()`; CLI `wallapop seller <id> --extra`.

### GET `/api/v3/users/{user_id}/seller-info`

Live it answers `404 Public info not found` for profiles without extra public
data. Low priority: the relevant data is already in `/stats`, `/reviews` and
`/extra-info`.

### GET `/api/v3/users/{user_id}/items`

All listings of a seller (public; the profile's listing grid).

- Query: `page=0`, `max_items=25`.
- Response: `{data: [items...], meta: {next: <cursor string>}}`. Paginate with
  `since=<meta.next>`; every item carries its `web_slug` (→ `Item.url`).
- Code: `Wallapop.seller_items(user_id, pages=...)`; CLI
  `wallapop seller-items <id> [--max N]`; MCP `seller_items`.

### GET `/api/v3/suggesters/nonlocated`

Search box autocomplete (the web calls it while typing).

- Query: `keyword=...`. Response:
  `data.suggestions[].regular_suggestion.suggestion.{text, highlight, subtitle}`.
- Code: `Wallapop.suggest(keyword, limit=...)`; CLI `wallapop suggest`; MCP `suggest`.
- Verified live: `suggest "nintendo sw"` → 5 suggestions.

---

## Messaging

### POST `/api/v3/instant-messaging/conversation`

Creates (or returns) the conversation for a listing. Idempotent. Answers 403
on your own listing (sellers reply by conversation id instead).

- Body: `{"item_hash_id": <item hash>}`
- The response carries `conversation_id`.
- Code: `Wallapop.ensure_conversation()`.

### GET `/bff/messaging/conversation/{conversation_id}`  [BFF]

A conversation's full thread.

- Response: `with_user`, `item`, `channel`, `sale`, and
  `messages.messages[]` (text, or a "delivery" payload for offers).
- Messages arrive newest-first; sort by `timestamp`.
- Code: `Wallapop.conversation_thread()`.

### GET `/bff/messaging/inbox?page_size=30&max_messages=30`  [BFF]

Inbox; each conversation includes its last N messages (newest-first) and the
related `item`.

- Code: `Wallapop.inbox()`.

### GET `/api/v3/instant-messaging/messages/unread`

Unread counter (`unread_counter`).

- Code: `Wallapop.unread_count()`.

### GET `/api/v3/instant-messaging/token`

PubNub token (used as `auth` when publishing).

- Response: `data.token`.
- Code: `Wallapop.im_token()`.

### GET `https://ps7.pndsn.com/publish/{pub}/.../{channel}/0/{msg}`  [PubNub REST]

The actual delivery of a chat message (what the web does).

- Path: `publish/{PUB_KEY}/{SUB_KEY}/0/{channel}/0/{json_url_encoded}`.
- Params: `meta` (JSON with the message type, `to_user_hash`,
  `from_user_hash` [= the JWT's `hashed_sub`], `conversation_hash`,
  platform), `uuid` (= user hash), `requestid`, `pnsdk`, `auth`.
- Success ack: `[1,"Sent",<timetoken>]`. Without an ack the outcome is
  unknown; the transport never retries this write-shaped GET.
- Code: `Wallapop.pubnub_publish()`.

---

## Offers

### POST `/api/v3/delivery/buyer/offers`

Send an offer (the web's current flow; the old `/items/{id}/offers` is dead: 404).

- Body: `{"offer_id": <client-generated uuid>, "offer_price_amount":
  <float>, "offer_price_currency": "EUR", "item_ids": [<item id>],
  "message": "..." (optional)}`.
- If it fails with `400/422` and a message was attached, it is retried without it.
- Code: `Wallapop.send_offer()`.

### PATCH `/api/v3/delivery/offers/{offer_id}/status`

Accept or decline an offer (e.g. a seller's counter-offer).

- Body: `{"status": "ACCEPTED" | "DECLINED"}`.
- Verified behaviour: on an already closed offer it returns
  `409 invalid offer status` (service validation, not anti-bot).
- After accepting, the thread receives *"Oferta aceptada, ¡cómpralo ahora!
  Esta oferta caduca en 24h."* ("Offer accepted, buy it now! This offer
  expires in 24h."); after declining, *"Has rechazado la oferta."*.
- Code: `Wallapop.offer_response()`.

### PATCH `/api/v3/delivery/offers/{offer_id}/offer-price`

Counter with a new price on an open offer (the web's "make a new offer" flow).

- Body: `{"offer_price_amount": <float>, "offer_price_currency": "EUR"}`.
- Code: `Wallapop.counter_offer()`.

### GET `/bff/delivery/offer-details?offer_id=`  [BFF]

Payload of the "Offer Tracking Status" modal.

- Only meaningful when **we** are the party that must answer (the payload is
  built for the current responder).
- ⚠️ **Blocked by anti-bot for `urllib`**: it returns 302 → `/notAvailable`;
  with `Sec-Fetch-*`/`mpid`/`trackinguserid` headers it becomes 200 but with
  the SPA's HTML shell (not JSON). In a browser (Playwright + session cookies)
  it does return JSON.
- The library does not depend on it: it rebuilds the state from the chat
  thread (`parse_offer_events`). Documented for completeness.
- Code: `Wallapop.offer_details()` (diagnostics only).

### GET `/bff/delivery/make-an-offer?item_id=`  [BFF]

Offer editor configuration.

- Response: `item.price.amount`, and inside `restriction`:
  `max_offer_percentage` (discount limit) and `remaining_offers_per_day`.
- Code: `Wallapop.make_offer_info()`; MCP `get_offer_constraints`.

---

## Offer ledger model (no extra API)

A negotiation's state is derived **offline** from the thread's messages:

- An offer message arrives as `type=delivery_generic` with a `payload` whose
  `text` (or `buttons[].action` with `app/chat/offer/<uuid>`) identifies the
  event; kinds: `our-offer`, `seller-counter`, `our-counter`,
  `seller-accepted`, `accepted-buy`, `seller-declined`, `our-declined`,
  `expired`. The texts are Wallapop's own (Spanish), so the classifier matches
  Spanish phrases.
- Terminal messages (accepted/declined/expired) carry no amount: `amount` and
  `offer_id` are forward-filled from the latest earlier event that has them.
- Code: `parse_offer_events()` + `offer_state_summary()` (`models.py`); MCP
  `get_offer_status`.

---

## Publishing listings (upload / publish)

> Status: the full flow is mapped and publishing works through the **pure
> API** (`wallapop publish`, MCP `publish_item`, no browser) and also **through
> the browser** (`wallapop publish --mode browser`, `wallapop_mcp/publish.py`).
> The `POST /api/v3/items` multipart body was captured with `page.route` +
> `request.post_data_buffer` (CDP's `post_data` was `null`, but the binary
> buffer is exposed) — that is the blueprint `Wallapop.publish()` replicates
> (see "Final step: publish").

### POST `/api/v3/steps`  [state machine]

The publishing wizard's engine. A stateless state machine: every request sends
the full draft and returns the next `step.id` to resolve.

**Common headers**: `Authorization: Bearer <token>`, `Content-Type: application/json`,
`Origin: https://es.wallapop.com`, `X-AppVersion: 826590`, `X-DeviceOS: 0`,
`X-DeviceID: <device_id>`.

#### 1. Init → title

```http
POST /api/v3/steps
{"mode": {"action": "upload", "id": "<upload_id>"}}
```

- `upload_id` = client-generated UUID (identifies the draft).
- Response: `{"step": {"id": "title", "components": [], "metadata": {}}}`.

#### 2. title → photo

```http
POST /api/v3/steps
{
  "mode": {"action": "upload", "id": "<upload_id>"},
  "current_step": "title",
  "draft": {"summary": "Nintendo 3DS XL Azul Yveltal Edicion Pokemon"}
}
```

- `summary` = title (max 50 chars; validated: no links, not only spaces, no emoji).
- Response: `step.id: "photo"` with `picture_upload.path` = `/api/v3/upload/<upload_id>/pictures`,
  `max_number_of_pictures: 1` (one photo per call), `timeout_ms: 20000`, `max_size_px: 768`,
  and `pictures_item.max_number_of_pictures: 10` (max 10 photos per listing).

#### 3. photo → category

```http
POST /api/v3/steps
{
  "mode": {"action": "upload", "id": "<upload_id>"},
  "current_step": "photo",
  "draft": {"summary": "Nintendo 3DS XL Azul Yveltal Edicion Pokemon"}
}
```

- Same draft as the title step (no photo ids are sent; uploading is separate).
- Response: `step.id: "category"` with `components: [{"id": "category", "type": "custom", ...}]`.

#### 4. category → listing

```http
POST /api/v3/steps
{
  "mode": {"action": "upload", "id": "<upload_id>"},
  "current_step": "category",
  "draft": {"category_leaf_id": "10088", "root_category_id": "24200"}
}
```

- **`category_leaf_id` and `root_category_id` are STRINGS** ("10088", "24200"), not integers.
- `category_leaf_id` = leaf (e.g. 10088 = consoles), `root_category_id` = root (24200 = tech & electronics).
- Response: `step.id: "listing"`, `components: []`.

#### 5. listing → (shipping → publish)

The `listing` step is the attributes/price/description/condition/shipping form.
Validated draft shape:

```json
{
  "summary": "Nintendo 3DS XL Azul Yveltal Edicion Pokemon",
  "description": "Nintendo 3DS XL in good condition. Pokemon Yveltal blue edition. Charger included. Shipping available.",
  "price_amount": "400",
  "category_id": "10088",
  "brand": "Nintendo",
  "model": "Nintendo 3DS XL",
  "condition": "good",
  "pictures": []
}
```

**Critical fields:**
- `price_amount` = **string** "400" (not an int, not a `price: {amount, currency}` object).
- `category_id` = string "10088".
- `condition` = string "good" (values: `un_opened`, `in_box`, `new`, `as_good_as_new`, `good`, `fair`, `has_given_it_all`).
- `color` = string "blue" (not an array).
- `brand`, `model` = optional strings.
- `description` = string (max 640).

Sending the `draft` with every field → 200 but it **does not advance** (the next step is still `listing`).

### Photo upload: `POST /api/v3/upload/<upload_id>/pictures`

One call per photo (max 10). Multipart `file` (image/jpeg).
- Response: **204 No Content** (no photo ids in the body or headers).
- Limit: `pictures_item.max_number_of_pictures: 10`.

### POST `/api/v3/items/upload/components`  [BDUI form schema]

Serves the `listing` form schema (components) for the current draft. Called
whenever a field with `reloads_when_changed: true` changes (e.g. `condition`).

Real request (**format confirmed from the browser**):
```json
{
  "fields": {
    "summary": "Nintendo 3DS Azul Cobalto",
    "category_leaf_id": "10088",
    "root_category_id": "24200",
    "brand": "Nintendo",
    "model": "3DS",
    "color": "blue",
    "title": "Nintendo 3DS Azul Cobalto",
    "description": "Nintendo 3DS console in cobalt blue...",
    "condition": "good",
    "price_amount": "310"
  },
  "mode": {"action": "upload", "id": "<upload_id>"}
}
```

- The body goes in **`fields`** + `mode` (same structure as `/steps`).
- Every value is a **string** (`category_leaf_id` "10088", `price_amount` "310").
- A first POST with only `summary`/`category` is sent on reaching `listing`,
  and a second one **with every field** (price, condition, brand, model…) right
  before publishing. Both return `200`.
- Brand/model/color/title/description are autocompleted after the photo upload
  (suggested-item-data); the user only edits price + condition + shipping.

200 response with an array of components (`text_input`, `combo_box`, `dropdown`, `custom`, `feature_banner`).
Fields observed, in order:
1. `suggested_data_banner` (feature_banner, not editable)
2. `photo` (custom, `max_number_of_pictures: 10`)
3. `brand` (combo_box, required, options via `/api/v3/attributes/brand/options?category_leaf_id=...`)
4. `model` (combo_box, optional, options via `/api/v3/attributes/model/options?brand=...&category_leaf_id=...`)
5. `color` (dropdown, optional)
6. `title` (text_input, required, max_length: 50, validated: no links)
7. `description` (text_area, required, max_length: 640)
8. `condition` (dropdown, required, `reloads_when_changed: true`)
9. `price_amount` (text_input, required, shipping range 1–2500 €)
10. `lock_stocks` (lock, optional, "add more units", `root_category_id: 24200`)

### Shipping configuration (used by the listing form)

**GET `/api/v3/delivery/weight/tiers/with-suggestion`**
```json
{"weight_tiers": [
  {"min_weight_in_kg": 0, "max_weight_in_kg": 1, "min_cost": {"amount": 2.99, "currency": "EUR"}, "max_cost": {"amount": 6.49, "currency": "EUR"}, "suggested": false},
  ...
]}
```

**GET `/bff/delivery/rules`**
```json
{"categories_with_shipping_not_allowed": [100, 14000, ...],
 "subcategories_with_shipping_not_allowed": [10119, 10120, ...],
 "price_range_allowed": {"min_price": 1.0, "max_price": 2500.0}}
```

### Final step: publish

The POST that creates the final listing (confirmed with real listings):

```http
POST /api/v3/items
```

**Verified live**: 200 and the item is created. After the POST the web calls
`GET /api/v3/items/<id>?language=es` (the new id) and uploads the remaining
photos with `POST /api/v3/items/<id>/picture2` (one per photo, all 200), then
redirects to `/app/catalog/published`.

**Real POST headers** (browser CDP capture):
- The OPTIONS preflight asks for `Access-Control-Request-Headers: authorization,x-deviceos`.
- The POST sends `Authorization: Bearer <token>`, `Content-Type: multipart/form-data;
  boundary=...`, `Accept: application/vnd.upload-v2+json`, `x-deviceos: 0`,
  `Origin/Referer: https://es.wallapop.com`.

**Full multipart body** (captured with `page.route('**/api/v3/items')` +
`request.post_data_buffer`; 966 B without the photo):

```
------WebKitFormBoundary<...>
Content-Disposition: form-data; name="image"; filename="photo.jpg"
Content-Type: image/jpeg

<photo bytes>
------WebKitFormBoundary<...>
Content-Disposition: form-data; name="item"

{"attributes":{"suggested_data_banner":null,"brand":"Nintendo","model":"3DS","color":"blue",
  "title":"Nintendo 3DS Azul Cobalto","description":"...","condition":"good",
  "price_amount":330},"category_leaf_id":"10088","apply_discount":false,
  "location":{"latitude":43.66844411761024,"longitude":-7.605464501519192,"approximated":false},
  "delivery":{"allowed_by_user":true,"max_weight_kg":1,
    "cost_configuration_id":"814429d6-..."},"upload_id":"<uuid>"}
------WebKitFormBoundary<...>--
```

Only two fields: **`image`** (the photo, generic filename) and **`item`** (JSON):
- `attributes.price_amount` is a **number** (330, unquoted), unlike the
  `"310"` string of the `components` schema. Cents are allowed.
- `location` = the seller's **non-approximated** lat/lon, taken from
  `GET /api/v3/users/me` (`location.approximated_latitude`, `.approximated_longitude`,
  `.approximated_location`).
- `delivery.cost_configuration_id` is a **per-account constant UUID** (the same
  as `shipping.cost_configuration_id` of any listing the user published).
- `upload_id` = client-generated UUID (the same `mode.upload.id`).
- `color` must be omitted on color-less categories (see
  `docs/multi-photo-upload.md`).
- In-person-only publishing uses `"delivery": {"allowed_by_user": false}`,
  omitting `max_weight_kg` and `cost_configuration_id`. The library and MCP expose
  this as `shipping_allowed=False`; CLI API mode exposes `--no-shipping`.
- 200 response: `{"id":"<item id>","flags":{}}` — **the listing id comes back
  in the POST response**.

**Implementation — pure API, no browser**: `Wallapop.publish()` (`client.py`)
replicates that multipart (photo + `item` JSON) over plain HTTP. It fills
`location` from `GET /users/me`, `cost_configuration_id` from any published item
of the account (with a known fallback) and `upload_id` with a fresh UUID. Extra
photos go through `picture2`. Verified live: 200 `{"id":...}`, the listing shows
up published with its photos. It is the default mode of `wallapop publish`;
`--mode browser` keeps the Playwright wizard.

The created listing (published item shape) reveals:
- `price: {cash: {amount, currency: "EUR"}}`
- `shipping: {item_is_shippable: true, user_allows_shipping: true, cost_configuration_id: "..."}`
- `location: {lat, lon, city, postal_code}`
- `type_attributes` includes `up_to_kg: "1.0"` (the chosen weight tier)
- `condition`, `brand`, `model`, `color` (the same as the form)

Reference endpoints in the JS bundle:
- `chunk-B5DKKJDO.js` — payload builder `ce` → POST `/api/v3/items`
- `chunk-IQHJVPZ3.js` — wizard steps (`POST /api/v3/steps`, `suggested-item-data`, constants `SUMMARY, CATEGORY, PHOTO, LOADING, LISTING, ISBN`)
- `chunk-3WKBKU6R.js` — `api/v3/items/upload/components`

## Editing a published listing

### PUT `/api/v3/items/{id}`  [edit / update]

Edits a published listing (what the web does on "Update" in the editor).
Captured with `page.on("request")` during a real edit (2026-09-06).

**Verified live**: every editable attribute returns **204** and the change shows
up in `GET /items/<id>`.

**Real PUT headers** (required — without them it is 404 `null for uri`):
```http
PUT /api/v3/items/<id>
Accept: application/vnd.upload-v2+json       # the MOST important one
Accept-Language: es,es-ES;q=0.9
Authorization: Bearer <token>
Content-Type: application/json
Origin: https://es.wallapop.com
Referer: https://es.wallapop.com/
User-Agent: Mozilla/5.0
X-AppVersion: 826800
X-DeviceID: <device_id>
X-DeviceOS: 0
mpid: <mpid>
trackinguserid: <mpid>
```
- `PATCH /items/<id>`, or this PUT **without** `Accept: application/vnd.upload-v2+json`
  → `404 null for uri` (not CORS nor auth: that header selects the handler).
- `pictures[].id` are the item's photo ids (from `GET /items/<id>` → `images[].id`),
  not the item id.
- Reading the current state: the versioned GET (`Accept:
  application/vnd.upload-v2+json`) now answers `406`, so `edit_item` reads the
  plain `GET /items/<id>` and maps its live shape (attributes under
  `type_attributes`, category under `taxonomy[-1].id`, price under
  `price.cash.amount`).

**Full JSON body** (the web ALWAYS sends every field):
```json
{
  "attributes": {
    "brand": "Nintendo",
    "model": "3DS",
    "color": "blue",
    "title": "Nintendo 3DS Azul Cobalto",
    "description": "Console in good condition...",   // plain STRING, not the {original} dict
    "condition": "good",
    "price_amount": 520
  },
  "category_leaf_id": "10088",
  "apply_discount": false,
  "location": {"latitude": 43.66, "longitude": -7.60, "approximated": false},
  "delivery": {"allowed_by_user": true, "max_weight_kg": 1,
               "cost_configuration_id": "<the item's uuid>"},
  "pictures": [{"id": "<photo_id>", "order": 0}]
}
```
- `description` must be a **plain string**. `GET /items/<id>` returns it as
  `{"original": "..."}`; sending that dict back gets a `400`.
- `location` and `cost_configuration_id` come from `GET /users/me` and the item.

**Attributes verified editable one by one (all 204, 2026-09-06)**:
`brand`, `model`, `color`, `title`, `description`, `condition`, `price_amount`,
`max_weight_kg`, `category_leaf_id` (moves the listing to another category).
The rest of the body must be sent unchanged (the web re-sends the whole form).
Code: `Wallapop.edit_item()`; CLI `wallapop edit`; MCP `edit_item`.

### Adding photos and changing the cover

- **Add**: `POST /api/v3/items/<id>/picture2` (multipart `image` = full JPEG
  bytes + `order` as a string) → 200 with the **new photo id**. One image per
  call. (Same recipe as multi-photo publishing; see `docs/multi-photo-upload.md`.)
- **Change the cover / reorder**: `PUT /api/v3/items/<id>` with `pictures`
  listing the photos in the wanted order: `order: 0` = cover. A photo left out
  disappears from the listing (verified live: replacing an item's image and
  adding 2 more, leaving 3 photos with the new one as cover).

### Reserve an item: `PUT /api/v3/items/<id>/reserve`

```http
PUT /api/v3/items/<id>/reserve
Content-Type: application/json
Body: {"reserved": true}     # to unreserve: {"reserved": false}
```

- Returns **204**. Check with `GET /items/<id>` → `reserved` becomes
  `{"flag": true}`, or `null` after unreserving.
- Validated through the pure API with the client's base headers (2026-09-06),
  and the web's seller "Reserve" button sends exactly the same request.
- Does not need the edit PUT's `Accept: application/vnd.upload-v2+json`.
- Code: `Wallapop.reserve()`; CLI `wallapop reserve`; MCP `reserve_item`.

### Mark an item as sold: `PUT /api/v3/items/<id>/sold`

```http
PUT /api/v3/items/<id>/sold
Content-Type: application/json
Body: {}                                  # empty
```

- Returns **204**, no body. `GET /items/<id>` → `sold` becomes
  `{"flag": true}`, both through the web flow and the pure API.
- Captured live from the web: the "Mark as sold" button opens a modal whose
  final confirm fires exactly this `PUT /items/<id>/sold`.
- Unlike `reserve`, the body is empty and the resulting flag is `sold`. The
  client undoes it with `{"sold": false}` (the web has no "unsell" button).
- Code: `Wallapop.mark_sold()`; CLI `wallapop sold`; MCP `mark_sold`.

### Favorite / unfavorite: `PUT /api/v3/items/{id}/favorite`

```http
PUT /api/v3/items/{id}/favorite
Content-Type: application/json
Body: {"favorited": true}     # to unfavorite: {"favorited": false}
```

- Returns **204**, no body. `GET /items/{id}` → `favorited` becomes
  `{"flag": true}` (or `false`/`null` after unfavoriting).
- Captured from the web (HAR): the heart button on the item page fires this PUT.
- Needs only the client's base headers (`Authorization`, `Content-Type`,
  `Origin`, `X-DeviceID`, `X-AppVersion`, `X-DeviceOS`).
- The web bundle defines the route as `api/v3/items/{id}/favorite`
  (exported as `itemFavoriteUrl`).
- Code: `Wallapop.favorite(item_id, favorited)`; MCP `set_favorite`.

### GET `/api/v3/favorite-lists`

The full favorites list of **your account** (the web's "Favorites" page,
`/favorites`). Authenticated.

- Response: `{data: [items...], meta: {next: <cursor string>}}`; as with
  `GET /users/{id}/items`, paginate with `since=<meta.next>`.
- Verified live: 200 → `{data: [], meta: {}}` with no favorites.
- Code: `Wallapop.favorite_lists(pages=...)`; CLI `wallapop favorites`; MCP
  `list_favorites`.

### POST/DELETE `/api/v3/favorite-lists/{list_id}/items`

Add/remove an item to/from a favorites list.

- `POST /favorite-lists/{list_id}/items` → body
  `{"items": [{"id": "<item_id>", "is_favorited": true}]}`.
- `DELETE /favorite-lists/{list_id}/items/{item_id}`.
- Code: `Wallapop.favorite_list_add(list_id, item_id)` /
  `favorite_list_remove(list_id, item_id)`.

### Saved searches / alerts: `/api/v3/searchalerts/savedsearch`

The web's **search alerts** (the saved searches badge). Authenticated. Four
resources:

`POST /api/v3/searchalerts/savedsearch` with the search filters as query
params (`keywords`, `category_id`, `min_sale_price`, `max_sale_price`,
`latitude`, `longitude`, `max_radius_kms`, `order_by`, `filters_source`...).

- → **201**, empty body, with the search **UUID** in the header
  `Location: /api/v3/searchalerts/savedsearch/<uuid>` (a UUID string, **not**
  an int). Code: `Wallapop.save_search(params) → str`; MCP `save_search`.
- `HEAD /api/v3/searchalerts/savedsearch/exists?<same params>` →
  **200** + `Location` with the UUID when it exists; **404** otherwise.
  Code: `Wallapop.saved_search_exists(params) → Optional[str]`; MCP
  `check_saved_search`.
- `GET /api/v3/searchalerts/savedsearch/alerts/total-hits` → 200,
  `{hits: <n>, savedSearchCount: <n>}` (new-match counter).
  Code: `Wallapop.saved_search_hits() → int`; MCP `saved_search_hits`.
- `DELETE /api/v3/searchalerts/savedsearch/{uuid}` → **200**, empty body.
  Code: `Wallapop.remove_saved_search(search_id)`; MCP `remove_saved_search`.

All verified live against a real account (full save → check → remove,
including the `exists` 404 after deleting).

### Delete an item: `DELETE /api/v3/items/{id}`

```http
DELETE /api/v3/items/{id}
```

- Returns **204**. Permanent: the listing leaves the marketplace and stops
  existing (a later GET returns 404). Unlike `PUT /items/{id}/reserve` (still
  visible but reserved), `PUT /items/{id}/sold` (off the marketplace, kept in
  the sales history) and `PUT /items/{id}/inactivate` (temporarily hidden).
- Verified live (2026-09-23): publish a test item → 200, `DELETE /items/{id}`
  → 204, `GET /items/{id}` → 404.
- Base headers only, with the internal `item_id` (12-char hash), like the other
  item endpoints.
- Also documented in Wallapop's public developer API (`developers.wallapop.com`,
  the official `deleteItem` method on `DELETE /items/{itemId}`).
- Code: `Wallapop.delete_item(item_id)`; CLI `wallapop delete <item> [--yes]`;
  MCP `delete_item`.

---

## Purchases and sales hubs (SDUI)

The web's **Purchases** (`/app/purchases?selectedTab=...`) and **Sales**
(`/app/selling?selectedTab=...`) pages are *server-driven UI*: the endpoint
returns a widget tree and the frontend renders it as is.

### GET `/sdui/purchases/{ongoing|finished}`  [root, auth]
### GET `/sdui/selling/{ongoing|finished}`   [root, auth]

- **They live at the host root** (`https://api.wallapop.com/sdui/...`),
  **without** `/api/v3` — like the `bff` ones. With `/api/v3` in front they
  return `404 null for uri .../api/v3/sdui/...`.
- Need `Authorization: Bearer`. No extra query params (captured live from the
  browser): a plain GET with the base headers.
- Response structure (widget tree):
  - `main.data.content[0]` → `tab_container` with both `tabs`
    (`id: ongoing`/`finished`, `label` localized per `Accept-Language`) and
    `content` (spacer + `directory`).
  - `directory.data.content[]` is the list: `text` nodes (year/month
    headers), `list_item_transaction` (order cards) or `empty_state` (empty
    tab, with title/subtitle/button).
- Each `list_item_transaction`:
  - `id` (order uuid); the real link is in `events.on_click.data.url`
    (tracking `app/delivery/tracking/<uuid>` for shipped purchases, or the item
    URL for in-person sales).
  - `data.main.data.content` (item title),
    `data.detail.data.content` (e.g. "Transacción completada 25 abr 2025"),
    `data.detail_price.data.content` ("14,88 €").
  - `data.extra_details[]`: `list_item_transaction_plain_detail` details
    (label/value: order date, shipping code, delivery method) and
    `list_item_transaction_contact_detail` (avatar + `label` = the other party).
  - `data.avatar.data.main_image_url` (product photo).
- The library **does not render the tree**: `parse_sdui_transactions()`
  (`models.py`) flattens only the cards (`id`, `title`, `detail`, `price`,
  `details[]`, `url`), dropping grouping headers and `empty_state`.
- Code: `Wallapop.purchases(tab)`, `Wallapop.selling(tab)`; CLI
  `wallapop purchases [--finished]` / `wallapop selling [--finished]`; MCP
  `list_purchases` / `list_sales`.

## MCP server coverage

This catalogue keeps the project's original endpoint research. The tools the
MCP server exposes (`wallapop_mcp/mcp_server.py`) are documented in
[AGENTS.md](../AGENTS.md#mcp-tool-reference). Unofficial endpoints can change;
the offline tests do not verify live availability.
