# AGENTS.md — driving wallapop-mcp from an AI agent

MCP server + CLI + Python library for Wallapop, **one codebase**
(`pip install wallapop-mcp`): the MCP server and the CLI both call the
`wallapop_mcp` package, so a fix lands once. Two jobs:

- **[Flow A — buy & negotiate](#flow-a--buy--negotiate)**: search, make offers, haggle a price down.
- **[Flow B — sell](#flow-b--sell)**: publish, edit, reply to buyers, accept/decline offers.

Read **[Login](#login--session)** first, then the flow you need, then the
**[tool reference](#mcp-tool-reference)**.

## Interfaces

| | MCP server | CLI | Library |
|---|---|---|---|
| Entry | `wallapop-mcp` / `python -m wallapop_mcp.mcp_server` | `wallapop` | `import wallapop_mcp` |
| Transport | stdio MCP (tools) | shell commands | Python calls |
| Writes | need `--allow-writes` | always available | always available |

All three share `wallapop_mcp`. Public reads (search, item, seller, categories)
need **no account**; inbox, offers, purchases/sales and every write need a
**session**. Ad-hoc API call:
`client, _ = wallapop_mcp.build_client(); client._authed("GET"|"PUT"|…, path, body=?, extra_headers=?)`.

## Login / session

The tools never log in; you give them an existing session (stored outside the
repo in `~/.config/wallapop-mcp/`). Create one with the CLI:

| Method | Command | Use when |
|---|---|---|
| Email + password | `wallapop login you@mail.com` (prompts) | normal account |
| Browser wizard | `wallapop login --wizard` (needs `pip install 'wallapop-mcp[browser]' && playwright install chromium`) | Google login / browser-only session |
| Import Firefox | `wallapop login --browser firefox` | already logged in to wallapop.com in Firefox |
| Paste cookies | `wallapop login --cookies-file cookies.txt` | any browser (copy the `Cookie:` header from DevTools) |

**Headless agents:** `login you@mail.com` and `--wizard` need a TTY/GUI. Use
**`--cookies-file`** or set **`WALLAPOP_ACCESS_TOKEN`** (+ optional
`WALLAPOP_REFRESH_TOKEN`). The MCP server takes `--session <dir|file>` (default
`~/.config/wallapop-mcp`, or `WALLAPOP_CONFIG_DIR`) or the same env var; writes need `--allow-writes`.
Check state with no network: `wallapop session` / the `session_status` tool.

## Flow A — buy & negotiate

1. **Search** — `wallapop search "nintendo switch" --city galicia --max-price 200 --order price_low_to_high`
   (MCP: `search`; anchor precisely with `latitude`+`longitude`). `wallapop deals "<kw>"` ranks bargains vs the median.
2. **Inspect** — `wallapop show <item-id|url|slug>` (`get_item`) and `wallapop show <seller-id>` (`get_seller`) before committing.
3. **Offer** — `wallapop offer <item> --price 30` (MCP `send_offer`; `send_offer` does not buy or pay).
4. **Negotiate** — watch with `wallapop replies --conversation <id>` (`get_conversation`) and `wallapop offer status [<conv>]` (`get_offer_status`); respond with `wallapop offer accept|decline|counter <offer_id> [--price 34]` (`respond_offer` / `counter_offer`).
5. **Close** — accept at the agreed price; Wallapop then drives checkout/shipping (no payment step here).

Anchor your first offer below target and move up in small steps; `get_offer_status` shows the current amount in play.

## Flow B — sell

1. **Publish** — `wallapop publish <photos_dir> --summary "<title ≤50>" --price 42 --condition as_good_as_new [--brand Nulea --model M512]`.
   Library/MCP let you set the category: `client.publish(photo_paths=[…], title=…, price=42, category_leaf_id="10311", condition="as_good_as_new", description=…)` / `publish_item`. **Use the right `category_leaf_id`** (mice `10311`, keyboards `10312`; find it from a comparable item's `taxonomy[-1].id`). `color` is optional — only pass it where the category has a color attribute.
   For an **in-person-only sale**, pass `shipping_allowed=False` to the library or
   MCP `publish_item`, or use CLI `--no-shipping` (API mode only). Shipping stays
   enabled by default. In-person listings do not need a shipping weight or cost
   configuration, including when the account has no existing listings.
2. **Edit** — `wallapop edit <item> --price 169 --title "…"` (`edit_item`). Lowering the price notifies users who favourited the item. Success is silent.
3. **Reply to a buyer** (seller side) — reply **by conversation id**; messaging by `item_id` 403s on your own item: `wallapop message --conversation <id> --text "…"` (MCP `send_message` with `conversation_id`).
4. **Handle offers** — `wallapop offer status`, then `wallapop offer accept <offer_id>` / `decline` / `counter --price 34`. Accept commits the sale (buyer then pays).
5. **Lifecycle** — `wallapop reserve|sold|delete <item>` (`reserve_item` / `mark_sold` / `delete_item`). A public listing URL is the slug (`.../item/<slug>-<numericid>`), not the bare internal id (which 404s in a browser).

## MCP tool reference

30 tools. The 13 writes appear only with `--allow-writes`.

**Reads:** `session_status`, `search`, `suggest`, `get_item`, `get_seller`,
`seller_items`, `list_categories`, `list_inbox`, `get_conversation`,
`unread_count`, `list_purchases` (`tab`), `list_sales` (`tab`),
`get_offer_constraints`, `get_offer_status`, `list_favorites`,
`saved_search_hits`, `check_saved_search`.

**Writes:** `set_favorite`, `send_message` (`item_id` **or** `conversation_id`),
`send_offer`, `respond_offer`, `counter_offer`, `publish_item`, `edit_item`,
`reserve_item`, `mark_sold`, `pause_item`, `delete_item`, `save_search`,
`remove_saved_search`.

**Sniping / alerts:** `save_search` stores a search (same filters as `search`)
so Wallapop notifies new matches; `saved_search_hits` returns the unseen-match
counter, `check_saved_search` finds the id of an existing one and
`remove_saved_search` deletes it. CLI: `wallapop alert save|check|hits|remove`.

Results are JSON. Writes may be partial or unknown — inspect the returned state
before retrying; there is no checkout or payment.

## API quirks (handled by the library — good to know)

- **`publish` + `color`.** Color-less categories (e.g. mice `10311`) answer `400
  INVALID_ATTRIBUTE` when `color` is present, even empty. `publish` omits it when
  empty; pass a color only where the category needs one.
- **`edit` state read.** The versioned edit GET (`Accept:
  application/vnd.upload-v2+json`) now returns `406`; `edit_item` reads a plain
  `GET /items/<id>` and maps the live shape (attributes under `type_attributes`,
  category under `taxonomy[-1].id`, price under `price.cash.amount`,
  `description` as `{"original": …}` — re-sent as a plain string).
- **Seller replies.** `send_message`/`ensure_conversation` POST a buyer→seller
  conversation and 403 on your own item; `reply_to_conversation` (used by
  `send_message` + `conversation_id`) publishes straight to the existing channel.
- **No "offer to favouriters".** Only buyer→seller offers exist; the API exposes
  the favourite count, never who favourited. Lowering the price is the nudge.

## File map

- `wallapop_mcp/client.py` — API client (`search`, `item`, `publish`, `edit_item`, `send_offer`, `reply_to_conversation`, …).
- `wallapop_mcp/mcp_server.py` — the MCP server (all tools delegate here).
- `wallapop_mcp/cli/` — the `wallapop` CLI. `wallapop_mcp/{config,http,models,ids,session}.py` — supporting modules.
- `tests/` — pytest suite (library, CLI, MCP). `docs/ENDPOINTS.md` — raw endpoint research.
