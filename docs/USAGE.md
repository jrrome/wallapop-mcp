# CLI usage

Full reference for the `wallapop` command installed with `pip install wallapop-mcp`
(from a checkout you can also run `python3 wallapop.py <command> ...`). Every
CLI action has an MCP tool counterpart; see [AGENTS.md](../AGENTS.md).

## Index

| Command | What it does |
| --- | --- |
| `login` | Sign in (email + password, browser wizard, Firefox import or pasted cookies) |
| `logout` | Delete stored credentials |
| `session` | Show session status |
| `search` | Search listings |
| `deals` | Search and rank bargains (price vs. median) |
| `watch` | Scan loop with auto-offer / auto-message |
| `offer` | Make offers, answer offers, follow negotiations |
| `message` | Message a listing's seller, or reply in a conversation |
| `replies` | Inbox and full threads |
| `show` | Full listing details (description and seller rating included) |
| `favorite` | Favorite / unfavorite a listing |
| `favorites` | List all your favorites (`/favorite-lists`) |
| `purchases` | Your purchases: ongoing or finished |
| `selling` | Your sales: ongoing or finished |
| `alert` | Saved searches (deal sniping): `save` / `check` / `hits` / `remove` |
| `suggest` | Keyword autocomplete (like the web search box) |
| `seller-items` | All listings of a seller (paginated) |
| `edit` | Edit one of your published listings (price, title, description, brand, model, color, condition, weight) |
| `reserve` | Reserve / unreserve one of your items |
| `sold` | Mark one of your items as sold (or undo it) |
| `inactivate` | Pause one of your listings |
| `delete` | Permanently delete one of your items (`DELETE /api/v3/items/{id}`) |
| `seller` | A seller's full profile (rating, stats, reviews; `--extra` for more) |
| `publish` | Publish an item through the pure API (or the Playwright wizard with `--mode browser`) |

---

## Login and session

**Easiest: plain `wallapop login`.** With no arguments it detects what you have
and takes the path with the least friction:

1. A valid stored session → `already logged in` (use `wallapop login --force`
   to switch accounts).
2. Firefox holds an `es.wallapop.com` session → imports it without opening
   anything (works with Google accounts).
3. `playwright` is installed → opens a visible browser for your normal login
   (Google, …) and captures the session.
4. Otherwise → suggests the email login.

Explicit paths:

| Path | Command | Good for |
| --- | --- | --- |
| **Auto** | `wallapop login` | Everything: uses whatever is available |
| Email + password | `wallapop login your@email.com` | Regular accounts |
| Browser wizard | `wallapop login --wizard` | **Google or any browser** |
| Firefox import | `wallapop login --browser firefox` | Google (if already logged in on Firefox) |
| Pasted cookies | `wallapop login --cookies '...'` or `--cookies-file my.txt` | Any browser (F12) |

### 1. Email + password

```
wallapop login your@email.com              # asks for the password securely
wallapop login your@email.com PASSWORD     # or inline
```

> **Note:** accounts registered with Google **have no Wallapop password**; this
> login answers 401. Use the browser wizard or the Firefox import.

### 2. Browser wizard (`--wizard`) — Google login

The universal path for Google accounts (or any session that only exists in a
browser). Google cannot be logged in through the pure API (its OAuth requires
the consent screen), so the wizard opens a browser, you log in, and the CLI
captures the session:

```
wallapop login --wizard            # waits 300 s
wallapop login --wizard --timeout 600
```

It opens a **visible Chromium** on `es.wallapop.com`. Log in normally (Google,
email, …); when you are done the CLI captures the session and mints the token.
`Ctrl+C` aborts without saving anything. Requires Playwright:

```
pip install 'wallapop-mcp[browser]'
playwright install chromium
```

### 3. Firefox import (`--browser firefox`)

```
wallapop login --browser firefox           # imports your Firefox session (works with Google accounts)
```

Reads `cookies.sqlite` from the most recent Firefox profile and stores the
session under `~/.config/wallapop-mcp/session.json`.

### 4. Pasted cookies (any browser / Windows)

Plain `wallapop login` does **not** read `cookies.txt` automatically (to avoid
clashing with auto-detection). Pass `--cookies` or `--cookies-file` explicitly:

```
wallapop login --cookies-file my.txt
wallapop login --cookies 'accessToken=...; __Secure-next-auth.session-token=...; device_id=...; device_fgp=...'
```

1. Open `https://es.wallapop.com` logged in → `F12` → **Network** tab.
2. Click a request to `es.wallapop.com` / `api.wallapop.com` → **Headers** →
   **Request Headers** → **Cookie:** → right click → **Copy value**.
3. Paste the value into a file (e.g. `cookies.txt`, see `cookies.txt.example`)
   and run `wallapop login --cookies-file cookies.txt`.

The CLI only keeps the cookies it needs (`accessToken`,
`__Secure-next-auth.session-token`, `device_id`, `device_fgp`).

### Session status

```
wallapop session                           # session source and token expiry
wallapop logout                            # delete the credential files
```

---

## Search

```
wallapop search 'nintendo switch' --city madrid
wallapop search 'ps5' --max-price 250 --order price_asc
wallapop search 'steam deck' --pages 2 --ship-only --json
```

Common flags:

| Flag | Description |
| --- | --- |
| `--category ID` | Filter by category (e.g. `24200` Tecnología y electrónica) |
| `--subcat ID` | Filter by taxonomy leaf (e.g. `10088` consoles, `10092` accessories) |
| `--condition LIST` | Filter by condition (comma separated: `un_opened,in_box,new,as_good_as_new,good,fair,has_given_it_all`) |
| `--min-price` / `--max-price` | Price range |
| `--city NAME` | City/country/region preset (`madrid`, `barcelona`, `italia`, `portugal`, `galicia`, …). Without `--city`/`--lat` it searches all of Spain |
| `--lat L` / `--lon L` | Exact coordinates (used together) |
| `--order` | `relevance`, `newest`, `price_asc`, `price_desc`, `closest` |
| `--pages N` | Result pages (default 1) |
| `--delay S` | Pause between pages (1.5 s) |
| `--ship-only` | Shippable items only |
| `--json` / `--csv` | Structured output |
| `--quiet` | Hide notices |

> **`price_asc` hides prices**: Wallapop returns `0.00 €` (hidden price) in
> results ordered by ascending price. Combined with `--ship-only` the first API
> page comes back empty (0 € items have no shipping); the CLI follows the
> `next_page` cursor and fetches the rest anyway. Adding `--min-price` avoids
> the placeholder and returns real prices.

> **Category ids**: the full catalogue (18 top categories + all their
> subcategories with ids) is in `docs/categories.json`, or live via
> `Wallapop.categories()`.
>
> - `--subcat` **without `--category` works**: the CLI climbs to the root node
>   automatically (e.g. `--subcat 10088` → `category_id=24200`).
> - `--category` with a **mid-level node** (e.g. `24203`, child of `24200`) is
>   also normalized to its root. The API accepts those ids but returns 0
>   results, which is why the CLI corrects them.

### Choosing a country / region

The geographic scope is set with `--city` (or `--lat`/`--lon`). Without either
the search covers all of Spain, which is exactly what `es.wallapop.com` returns
for a user in Spain. Ask for another country explicitly:

```
wallapop search "gamecube" --city italia      # listings from Italy only
wallapop search "gamecube" --city portugal    # Portugal (mixed with the Spanish border area)
wallapop search "gamecube" --city galicia     # anchor to Galicia
```

Available presets (besides cities): countries (`espana`, `portugal`, `italia`,
`francia`, `marruecos`, `andorra`, `reino-unido`, `alemania`, `belgica`) and
Spanish regions (`galicia`, `andalucia`, `cataluna`, `pais-vasco`,
`comunidad-valenciana`, …).

Note: `relevance` ordering respects the chosen country; `newest` tends to
return a more global set (Spain mixed with other countries), just like the app.

Technical details (why the API returns 0 without coordinates, the role of
`search_country`, …) are in `docs/ENDPOINTS.md`.

### Category catalogue

```python
from wallapop_mcp import Wallapop
cats = Wallapop().categories()   # [{"id", "name", "subcategories": [...]}]
```

Use it to guide users through the categories when building a search instead
of hardcoding ids. Endpoint details: `docs/ENDPOINTS.md` (`GET /api/v3/categories`).

---

## Deals and watch

```
wallapop deals 'nintendo 3ds' --city madrid --min-discount 0.2 --max-price 100
wallapop deals 'nintendo ds' --msg 'Hi! Would you sell it for {offer}?' --offer-pct 0.8 --dry-run
wallapop watch 'steam deck' --max-price 300 --interval 600
wallapop watch 'mario' --once                        # a single scan, then exit
```

`deals` computes the **median** of the results and each listing's discount; it
only shows/acts on those above `--min-discount` (0.15 by default). Extra flags:

| Flag | Description |
| --- | --- |
| `--min-discount F` | Minimum discount over the median (0.15) |
| `--top N` | Act on / show the N best (10) |
| `--offer-pct F` | Auto-offer at that fraction of the price (e.g. `0.8`) |
| `--msg TMPL` | Auto-message template: `{title} {price} {offer} {link} {city}` |
| `--dry-run` | Print the intent without sending |
| `--interval S` | Seconds between scans in `watch` (300, minimum 5) |
| `--once` | Single scan in `watch` |

`contacted.json` stores the ids already contacted so they are not contacted
twice. Each deals row shows its discount badge (`-32%` in green) and the
`MEDIAN` column with the market reference.

---

## Offers

```
wallapop offer <item> --price 60 --msg 'Hi, would you take 60?'   # make an offer
wallapop offer <item> --pct 0.8                                    # or as a fraction
wallapop offer status                # negotiation dashboard
wallapop offer status <conv|item>    # one conversation in detail
wallapop offer status <offer_id>     # resolves the uuid -> conversation
wallapop offer status --last 5       # only the 5 most recent conversations
wallapop offer accept <offer_id>     # accept the seller's counter-offer
wallapop offer decline <offer_id>    # decline it
wallapop offer counter <offer_id> --price N   # counter
wallapop offer <item> --price 60 --dry-run     # simulate without sending
```

- `<item>` accepts an internal id, a URL or a web slug; `<offer_id>` is the uuid
  the dashboard shows (`offer=xxxx-...`).
- `offer <item>` queries the web's real offer editor and warns when the amount
  is below the discount limit (`max_offer_percentage`) and how many offers are
  left today.
- Accepting an offer **commits you to buy within 24h** at that price.

### The `offer status` dashboard

With no arguments, `offer status` shows the conversation dashboard, most
recent activity first:

```
  asking = listing price · "actual" = amount of the latest offer in play
  [ACCEPTED]    Juegos Mario Nintendo 3DS   28.0 EUR [published]  ·  pixelia  accepted by you — buy within 24h  actual 25.00€  offer=72f36...
      conv wzy050ee4e65  ->  offer status wzy050ee4e65
  [PENDING]     Nintendo Switch Lite Amarilla  95.0 EUR  ·  Fran     waiting for your reply  actual 90.00€  offer=ea034...
```

State legend (badge + label):

| Badge | Label | Meaning |
| --- | --- | --- |
| `[ACCEPTED]` green | `accepted by you / by the seller (buy within 24h)` | Offer closed |
| `[PENDING]` yellow | `waiting for your reply` | Seller counter-offer that **you** must answer |
| `[WAITING]` cyan | `waiting for the seller's reply` | Your offer/counter-offer is standing |
| `[DECLINED]` red | `declined by you / by the seller` | Closed without a purchase |
| `[EXPIRED]` grey | `expired` | Expired |
| (no badge) | `no offers` | No offer activity |

`actual X€` is always the **latest offer amount in play** (a counter-offer you
can accept, the accepted amount, or your last pending offer). The per-conversation
view (`offer status <conv>`) prints the full chronological ledger
(63.00 → 80.00 → 70.00 €, for example) and the final state, with the command to
run when it is your turn.

---

## Messaging

```
wallapop message <item> --text 'Hi, does it work fine?'
wallapop message <item> --text 'Hello!' --dry-run
wallapop message --conversation <conv> --text 'Shipped today'   # reply as the seller
wallapop replies                          # inbox with unread counts
wallapop replies --conversation <conv>    # a full chat thread
wallapop replies --json                   # raw dump (inbox or conversation)
wallapop replies --conversation <conv> --json
```

- Messages are delivered through PubNub's REST API (what the web really does).
  Without an ack the delivery is unknown: check the thread before re-sending.
  The browser fallback needs `--browser-fallback` (Playwright + Firefox) and is
  never used for ambiguous sends.
- The CLI never re-sends a text identical to your last message in the thread.
- On your own listing, reply **by conversation id**: messaging by item id
  answers 403 there.

---

## Listing details

```
wallapop show <item>                 # full details + description
wallapop show <item> --json          # raw dump of the whole listing as JSON
```

- `<item>` accepts a URL, web slug or internal hash (like `message`/`offer`).
- Prints title, price, seller (id, name, average star rating and number of
  reviews, the same values the web shows), city with coordinates, category,
  brand, genre, condition, shipping, views/favorites, URL and the full
  description.
- Details come from `GET /items/{id}` (`description.original`,
  `type_attributes`, `counters`, `location`, …); the seller rating from
  `GET /users/{id}` (name) and `GET /users/{id}/stats` (average and total reviews).
- With `--json` the output also includes `seller_name`, `seller_rating`,
  `seller_reviews` and `images` (medium-size photo URLs).
- For the seller's full profile use `seller` (below).

---

## Favorites

```
wallapop favorite <item>          # add to favorites
wallapop favorite <item> --remove # remove from favorites
```

- `<item>` accepts a URL, web slug or internal hash.
- Uses the web's `PUT /items/{id}/favorite` with body `{"favorited": true|false}`
  (204, no body).

### `favorites` (all your favorites)

```
wallapop favorites                # your favorites (/favorite-lists)
wallapop favorites --pages 3      # more pages
wallapop favorites --json         # raw dump
```

- The web's `GET /favorite-lists`; paginates automatically with the
  `meta.next` → `since` cursor. Each item URL uses its web slug.
- Dump them to disk: `wallapop favorites --json > favs.json`.

---

## Saved searches / alerts (deal sniping)

```
wallapop alert save 'nintendo switch' --min-price 100 --max-price 300
wallapop alert check 'nintendo switch' --max-price 300
wallapop alert hits
wallapop alert remove --id 87b2e4fc-b3c9-4351-81af-43e6788aafa5
```

- `save` → `POST /searchalerts/savedsearch`. The server answers with the
  search **UUID** (`Location` header); it is printed so you can `remove` it later.
- `check` → `HEAD /searchalerts/savedsearch/exists` with the same filters:
  prints `saved (id <uuid>): '<keywords>'` or `not saved: '<keywords>'`.
- `hits` → `GET /searchalerts/savedsearch/alerts/total-hits`: the web's
  new-match counter (the badge next to saved searches).
- `remove --id <uuid>` → `DELETE /searchalerts/savedsearch/{id}` (200).
- Filters are the same as `search`: `--min-price`, `--max-price`, `--category`,
  `--subcat`, `--city`/`--lat`/`--lon`, `--ship-only`, `--condition`, `--order`.
- Needs a logged-in session. The full `save → check → remove` cycle was
  verified live on a real search.

---

## Autocomplete (suggest)

```
wallapop suggest 'nintendo sw'     # the web search box suggestions
wallapop suggest 'ps' --top 5 --json
```

- `GET /suggesters/nonlocated?keyword=...`; prints `text [subtitle]` per line.

---

## Managing your listings

```
wallapop edit <item> --price 200 --title 'Nintendo 3DS Red' --condition as_good_as_new
wallapop edit <item> --brand Nintendo --model 3DS --color red --max-weight-kg 1
wallapop reserve <item>            # reserve (hides it on the marketplace)
wallapop reserve <item> --remove   # unreserve
wallapop sold <item>               # mark as sold
wallapop sold <item> --undo        # undo
wallapop inactivate <item>         # pause (off the marketplace, not deleted)
wallapop delete <item>             # delete permanently (asks for confirmation)
wallapop delete <item> --yes       # delete without confirmation
```

- `<item>` accepts a URL, web slug or internal hash and must be **one of your
  listings** (the API answers 403/404 otherwise).
- `edit` replicates the web's "Update" form (`PUT /api/v3/items/{id}`). Only
  what you pass changes; everything else is filled from the listing's current
  state. Lowering the price notifies users who favorited the item.
- `reserve` → `PUT /items/{id}/reserve` (body `{"reserved": true|false}`, 204);
  `sold` → `PUT /items/{id}/sold` (empty body to mark, `{"sold": false}` to
  undo, 204).
- `inactivate` → `PUT /items/{id}/inactivate` (204), the web profile's "Pause
  listing" switch (403 live when the item is not yours). Editing the listing
  puts it back online.
- `delete` → `DELETE /api/v3/items/{id}` (204). **Permanent**: the listing
  disappears (a later `show` gives 404). Unlike `reserve`/`sold`/`inactivate`,
  which keep it in your history. Asks for confirmation unless `--yes`.

---

## Purchases and sales

```
wallapop purchases                  # ongoing purchases
wallapop purchases --finished       # finished purchases
wallapop purchases --json           # raw transactions
wallapop selling                    # ongoing sales
wallapop selling --finished         # finished sales
wallapop selling --finished --json  # raw dump
```

- Replicate the web's **Purchases** (`/app/purchases?selectedTab=ongoing|finished`)
  and **Sales** (`/app/selling?selectedTab=ongoing|finished`) hubs through the
  pure API (`GET /sdui/purchases/{ongoing|finished}` and `GET /sdui/selling/...`).
- Each card shows title, price, status detail (e.g. "Transacción completada
  25 abr 2025", as Wallapop words it), extra fields (order date, shipping code,
  delivery method, contact) and the order/listing link.
- An empty tab prints `(nothing here)`.
- `--json` returns the flattened transactions (`id`, `title`, `detail`,
  `price`, `details[]`, `url`).

---

## Seller profiles

```
wallapop seller <user_id>             # rating, sales stats and reviews
wallapop seller <user_id> --json      # raw dump (profile + stats + reviews)
wallapop seller <user_id> --extra     # + extra info (response rate, verification, activity)
wallapop seller-items <user_id>       # all their listings (paginated)
wallapop seller-items <user_id> --max 4 --json
```

- `<user_id>` is the seller id shown on the `seller:` line of `show`.
- Shows name, average rating and total reviews, account stats (sales,
  shipments, average delivery days) and detailed reviews.
- `--extra` adds `GET /users/{id}/extra-info` (response speed, verifications
  and activity level).
- `seller-items` lists every listing with `GET /users/{id}/items` (cursor
  `meta.next` → `since`).

---

## Publishing an item

```
wallapop publish /path/to/photos --summary 'Nintendo 3DS Blue' --price 310
wallapop publish ./photos --summary '...' --price 200 --condition good --brand Nintendo --model 3DS --color blue
wallapop publish ./photos --summary 'Local pickup only' --price 200 --no-shipping
wallapop publish ./photos --summary '...' --price 331 --mode browser     # Playwright wizard
```

- **Pure API by default** (`--mode api`), no browser: `Wallapop.publish()`
  replicates the exact multipart of the final `POST /api/v3/items` (an `image`
  field with the photo + an `item` field with the attributes/price/location/
  shipping JSON). Body reference: `docs/ENDPOINTS.md`.
- `--mode browser` drives the real `es.wallapop.com` upload wizard with your
  stored session (`wallapop_mcp/publish.py`, Playwright).
- `photos_dir`: folder with the item's jpg/png photos. `--max-photos` uploads up
  to 10; a failed extra photo returns a partial result that keeps the created
  listing id.
- `--summary`: title (max 50 characters).
- In API mode you set brand/model/color/description yourself (`--brand`,
  `--model`, `--color`, `--description`); in browser mode the web autocompletes
  them from the photos. Pass `--color` only where the category has a color
  attribute.
- `--condition`: `un_opened,in_box,new,as_good_as_new,good,fair,has_given_it_all`
  (default `good`).
- `--price`: price in EUR; cents are allowed (e.g. `19.95`).
- `--max-weight-kg`: shipping weight in kg (default 1, API mode).
- `--no-shipping`: in-person sale only (API mode). Omits shipping weight and
  cost configuration, and does not require a previous listing from the account.
  Shipping remains enabled when this flag is omitted. Combining it with
  `--mode browser` is rejected before the wizard starts.
- `--weight-tier`: the wizard's shipping weight tier (default `0` = 0–1 kg,
  browser mode only).
- `--headless`: browser mode only, run without a window.

---

## General notes

- Rate guard: `WALLAPOP_RATE_PACE` (seconds between requests, 1.0 by default).
- Colored output turns itself off when stdout is not a TTY.
- Errors go to stderr prefixed with `error:`; successful actions print
  `[ok] ...`, simulated ones `[dry-run] would ...`.
- All transport is plain HTTP (`urllib`) except the optional browser paths
  (`login --wizard`, `publish --mode browser`, `--browser-fallback`).
