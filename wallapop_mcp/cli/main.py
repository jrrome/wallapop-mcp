"""Argparse wiring: search/deal flags, subcommands and the main() entry."""

from __future__ import annotations

import argparse
from typing import Optional

from .commands import (
    cmd_alert,
    cmd_deals,
    cmd_delete,
    cmd_edit,
    cmd_favorite,
    cmd_favorites,
    cmd_inactivate,
    cmd_login,
    cmd_logout,
    cmd_message,
    cmd_offer,
    cmd_publish,
    cmd_purchases,
    cmd_replies,
    cmd_reserve,
    cmd_search,
    cmd_seller,
    cmd_seller_items,
    cmd_selling,
    cmd_session,
    cmd_show,
    cmd_sold,
    cmd_suggest,
    cmd_watch,
)
from ..config import CATEGORIES, CITIES, ORDER_ALIASES, VERSION
from ..http import ApiError
from .output import eprint, paint

def add_search_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--category", type=int, help=f"category id. common: {', '.join(f'{k}({v})' for k, v in sorted(CATEGORIES.items()))}")
    sp.add_argument("--subcat", help="filter by taxonomy leaf id, e.g. 10088 = consoles, 10092 = console accessories")
    sp.add_argument("--min-price", type=float)
    sp.add_argument("--max-price", type=float)
    sp.add_argument("--city", help=f"city preset: {', '.join(sorted(CITIES))}")
    sp.add_argument("--lat", type=float)
    sp.add_argument("--lon", type=float)
    sp.add_argument("--order", choices=sorted(ORDER_ALIASES), default="relevance")
    sp.add_argument("--pages", type=int, default=1, help="result pages to fetch (default 1)")
    sp.add_argument("--delay", type=float, default=1.5, help="seconds to wait between page fetches")
    sp.add_argument("--ship-only", action="store_true", help="only keep listings that can be shipped")
    sp.add_argument(
        "--condition",
        help="comma-separated item conditions, e.g. 'new,as_good_as_new,good'. values: "
        "un_opened,in_box,new,as_good_as_new,good,fair,has_given_it_all",
    )
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--csv", action="store_true")
    sp.add_argument("--quiet", action="store_true")


def add_deal_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--min-discount", type=float, default=0.15, help="require at least this discount vs median (default 0.15)")
    sp.add_argument("--top", type=int, default=10, help="act on the best N deals only (default 10)")
    sp.add_argument("--offer-pct", type=float, help="auto-send an offer at this fraction of the asking price, e.g. 0.8")
    sp.add_argument(
        "--msg",
        help="auto-message template. placeholders: {title} {price} {link} {city} {offer}",
    )
    sp.add_argument("--dry-run", action="store_true", help="print intent without sending")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallapop",
        description="Wallapop automation CLI: search, hunt deals, send offers and messages.",
        epilog=(
            "examples:\n"
            "  wallapop search 'nintendo switch' --city madrid --order price_asc\n"
            "  wallapop deals 'ps5' --max-price 250 --min-discount 0.2\n"
            "  wallapop offer 123456789 --pct 0.8 --msg 'Hola, aceptarias 200?'\n"
            "  wallapop message nintendo-switch-lite-blu-1295309387 --text 'Hola, funciona bien?'\n"
            "  wallapop show nintendo-switch-lite-blu-1295309387\n"
            "  wallapop replies --conversation nzxp0m9qp5j2\n"
            "  wallapop watch 'steam deck' --max-price 300 --msg 'hola amigo, funciona bien?' --interval 600\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    lp = sub.add_parser(
        "login",
        help="log in. with no arguments it detects an existing session, Firefox, or opens the Google login window (wizard)",
    )
    lp.add_argument("email", nargs="?", default=None, help="email for password login")
    lp.add_argument("password", nargs="?", help="optional; if omitted you are prompted safely")
    lp.add_argument("--browser", choices=["firefox"], help="import your session from the Firefox profile (works with Google accounts)")
    lp.add_argument("--wizard", action="store_true", help="open es.wallapop.com in a real browser and capture the session after you log in (works with Google accounts from any browser)")
    lp.add_argument("--timeout", type=int, default=300, help="seconds to wait for the wizard login (default 300)")
    lp.add_argument("--force", action="store_true", help="re-login even if a valid session is already stored")
    lp.add_argument("--cookies", help="paste cookies as a 'name=value; ...' string (accessToken, __Secure-next-auth.session-token)"
    )
    lp.add_argument("--cookies-file", default=None, help="read cookies from this file instead of --cookies")
    lp.set_defaults(func=cmd_login)

    so = sub.add_parser("logout", help="clear stored credentials")
    so.set_defaults(func=cmd_logout)

    ss = sub.add_parser("session", help="show current session")
    ss.set_defaults(func=cmd_session)

    se = sub.add_parser("search", help="search listings")
    add_search_args(se)
    se.add_argument("keyword", nargs="+")
    se.set_defaults(func=cmd_search)

    dl = sub.add_parser("deals", help="find listings priced below market median")
    add_search_args(dl)
    add_deal_args(dl)
    dl.add_argument("keyword", nargs="+")
    dl.set_defaults(func=cmd_deals)

    wc = sub.add_parser("watch", help="loop a scan and auto-offer/message fresh deals")
    add_search_args(wc)
    add_deal_args(wc)
    wc.add_argument("--interval", type=float, default=300, help="seconds between scans (default 300, min 5)")
    wc.add_argument("--once", action="store_true", help="run a single scan then exit")
    wc.add_argument("keyword", nargs="+")
    wc.set_defaults(func=cmd_watch)

    of = sub.add_parser("offer", help="make/check an offer on an item, or accept/decline/counter an existing offer")
    of.add_argument("what", nargs="?", help="item id/url/slug, offer id, or an action: accept, decline, counter, status")
    of.add_argument("offer_id", nargs="?", help="offer id (required with an action)")
    of.add_argument("--price", type=float, help="exact offer amount")
    of.add_argument("--pct", type=float, help="offer as a fraction of the asking price, e.g. 0.8")
    of.add_argument("--msg", help="optional message attached to the offer")
    of.add_argument("--last", type=int, default=None, help="offer status: only show the N most recent conversations")
    of.add_argument("--dry-run", action="store_true")
    of.set_defaults(func=cmd_offer)

    me = sub.add_parser("message", help="message a seller, or reply as the seller in a conversation")
    me.add_argument("item_id", nargs="?", help="item url, web slug or internal hash (buyer→seller); omit when using --conversation")
    me.add_argument("--conversation", help="reply in this conversation id (seller-safe: avoids the 403 on your own item)")
    me.add_argument("--text", required=True, help="message text")
    me.add_argument("--browser-fallback", action="store_true", help="send through the web app only when PubNub was definitely not sent (opt-in)")
    me.add_argument("--dry-run", action="store_true")
    me.set_defaults(func=cmd_message)

    rp = sub.add_parser("replies", help="list your inbox conversations (or print one full thread)")
    rp.add_argument("--conversation", help="conversation id to dump the full message thread")
    rp.add_argument("--json", action="store_true", help="dump raw JSON (inbox or conversation thread)")
    rp.set_defaults(func=cmd_replies)

    pc = sub.add_parser("purchases", help="list your purchases (Wallapop 'Compras'): ongoing or finished")
    pc.add_argument("--finished", action="store_true", help="show finished purchases instead of ongoing")
    pc.add_argument("--json", action="store_true", help="dump the transactions as JSON")
    pc.set_defaults(func=cmd_purchases)

    sl = sub.add_parser("selling", help="list your selling (Wallapop 'Ventas'): ongoing or finished")
    sl.add_argument("--finished", action="store_true", help="show finished sales instead of ongoing")
    sl.add_argument("--json", action="store_true", help="dump the transactions as JSON")
    sl.set_defaults(func=cmd_selling)

    ed = sub.add_parser("edit", help="edit one of your published items")
    ed.add_argument("item", help="item url, web slug or internal hash (owned item)")
    ed.add_argument("--price", type=float, help="new price in EUR")
    ed.add_argument("--title", help="new title (max 50 chars)")
    ed.add_argument("--description", help="new description")
    ed.add_argument("--condition", help="condition: un_opened,in_box,new,as_good_as_new,good,fair,has_given_it_all")
    ed.add_argument("--brand", help="new brand, e.g. Nintendo")
    ed.add_argument("--model", help="new model, e.g. 3DS")
    ed.add_argument("--color", help="new color, e.g. blue")
    ed.add_argument("--max-weight-kg", type=int, help="shipping weight in kg")
    ed.add_argument("--json", action="store_true", help="dump the updated item as JSON")
    ed.set_defaults(func=cmd_edit)

    rs = sub.add_parser("reserve", help="reserve/unreserve one of your items")
    rs.add_argument("item", help="item url, web slug or internal hash (owned item)")
    rs.add_argument("--remove", action="store_true", help="unreserve instead")
    rs.set_defaults(func=cmd_reserve)

    sd = sub.add_parser("sold", help="mark one of your items as sold (or undo)")
    sd.add_argument("item", help="item url, web slug or internal hash (owned item)")
    sd.add_argument("--undo", action="store_true", help="mark as not sold instead")
    sd.set_defaults(func=cmd_sold)

    dl2 = sub.add_parser("delete", help="permanently delete one of your items")
    dl2.add_argument("item", help="item url, web slug or internal hash (owned item)")
    dl2.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    dl2.set_defaults(func=cmd_delete)

    sh = sub.add_parser("show", help="print a listing's full details (including description)")
    sh.add_argument("item", help="item url, web slug or internal hash")
    sh.add_argument("--json", action="store_true", help="dump the full item as JSON")
    sh.set_defaults(func=cmd_show)

    fav = sub.add_parser("favorite", help="add/remove an item from your favorites")
    fav.add_argument("item", help="item url, web slug or internal hash")
    fav.add_argument("--remove", action="store_true", help="remove from favorites instead of adding")
    fav.set_defaults(func=cmd_favorite)

    seller = sub.add_parser("seller", help="print a seller's full profile (rating, stats, reviews)")
    seller.add_argument("user", help="seller user id (found in 'show' output)")
    seller.add_argument("--json", action="store_true", help="dump the seller profile as JSON")
    seller.add_argument("--extra", action="store_true", help="also fetch extra-info (response rate, verification level)")
    seller.set_defaults(func=cmd_seller)

    favs = sub.add_parser("favorites", help="list the items you favorited (GET /favorite-lists)")
    favs.add_argument("--pages", type=int, default=1, help="result pages to fetch (default 1)")
    favs.add_argument("--json", action="store_true", help="dump favorites as JSON")
    favs.set_defaults(func=cmd_favorites)

    si = sub.add_parser("seller-items", help="list every published item of a seller (public)")
    si.add_argument("user", help="seller user id (found in 'show' output)")
    si.add_argument("--pages", type=int, default=1, help="result pages to fetch (default 1)")
    si.add_argument("--max", type=int, default=25, help="items per request (default 25)")
    si.add_argument("--json", action="store_true", help="dump the items as JSON")
    si.set_defaults(func=cmd_seller_items)

    su = sub.add_parser("suggest", help="autocomplete suggestions for a search keyword")
    su.add_argument("keyword", nargs="+")
    su.add_argument("--top", type=int, default=10, help="max suggestions (default 10)")
    su.add_argument("--json", action="store_true", help="dump suggestions as JSON")
    su.set_defaults(func=cmd_suggest)

    ic = sub.add_parser("inactivate", help="pause one of your published items (leaves the marketplace)")
    ic.add_argument("item", help="item url, web slug or internal hash (owned item)")
    ic.set_defaults(func=cmd_inactivate)

    al = sub.add_parser("alert", help="manage saved searches (search alerts)")
    al.add_argument("action", choices=["save", "check", "remove", "hits"], help="save a search, check if a search exists, remove by id, or show the new-listing counter")
    al.add_argument("keyword", nargs="*", help="search keyword (used by save/check)")
    al.add_argument("--id", dest="search_id", help="saved-search id (remove)")
    add_search_args(al)
    al.set_defaults(func=cmd_alert)

    pub = sub.add_parser("publish", help="publish an item (pure API by default, or the web wizard with --browser)")
    pub.add_argument("photos_dir", help="directory with the item photos (jpg/png); pure-API mode uses the first photo")
    pub.add_argument("--summary", required=True, help="item title (max 50 chars)")
    pub.add_argument("--price", type=float, required=True, help="list price in EUR")
    pub.add_argument(
        "--condition", default="good",
        help="condition: un_opened,in_box,new,as_good_as_new,good,fair,has_given_it_all (default good)",
    )
    pub.add_argument("--mode", choices=["api", "browser"], default="api", help="how to publish (default api)")
    pub.add_argument("--description", default="", help="item description (pure-API mode)")
    pub.add_argument("--brand", default="", help="item brand, e.g. Nintendo (pure-API mode)")
    pub.add_argument("--model", default="", help="item model, e.g. 3DS (pure-API mode)")
    pub.add_argument("--color", default="", help="item color, e.g. blue (pure-API mode)")
    pub.add_argument("--max-weight-kg", type=int, default=1, help="shipping weight in kg (pure-API mode, default 1)")
    pub.add_argument(
        "--no-shipping", dest="shipping_allowed", action="store_false",
        help="publish for in-person sale only, without shipping (API mode only)",
    )
    # browser-only flags (keep compat with the original wizard command)
    pub.add_argument("--weight-tier", default="0", help="shipping weight tier radio name (browser mode only, default 0 = 0-1 kg)")
    pub.add_argument("--max-photos", type=int, default=1, help="browser mode only: photos to upload (default 1, max 10)")
    pub.add_argument("--headless", action="store_true", help="browser mode only: run Chromium headless (invisible)")
    pub.add_argument("--capture-body", help="browser mode only: save the raw POST /api/v3/items body to this file (debug)")
    pub.set_defaults(func=cmd_publish)

    parser.add_argument("--version", action="version", version=f"wallapop {VERSION}")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except ApiError as err:
        eprint(paint.red(f"error: {err}"))
        return 1
