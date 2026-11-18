"""``claire plugin run claire.hoyolab wiki-crawl`` (was ``claire hoyowiki``): list menus, search, get one entry, or crawl a menu of HoYoWiki."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from claire.plugin_sdk import ingest_session

from .hoyowiki import _GAME_NAMES, HoYoWikiClient, crawl_hoyowiki, fetch_hoyowiki

SOURCE = "cli-hoyowiki"


def build_wiki_crawl_parser(parser: Any) -> None:
    parser.description = "Crawl and collect structured game entries from HoYoWiki (wiki.hoyolab.com) with optional direct ingestion."
    parser.add_argument("-g", "--game", default="genshin", choices=["genshin", "hsr", "zzz", "honkai3rd", "tot", "all"], help="game identifier (default: genshin)")
    parser.add_argument("-m", "--menu", default=None, help="menu category name or numeric ID (e.g. 캐릭터, 2, 장비 도감)")
    parser.add_argument("-s", "--search", default=None, help="search entries by keyword")
    parser.add_argument("-e", "--entry-id", default=None, help="specific entry page ID to fetch")
    parser.add_argument("-u", "--url", default=None, help="specific HoYoWiki URL to fetch")
    parser.add_argument("-l", "--limit", type=int, default=None, help="maximum entries to collect")
    parser.add_argument("-d", "--delay", type=float, default=0.5, help="delay in seconds between HTTP requests (default: 0.5)")
    parser.add_argument("--lang", default="ko-kr", help="language code: ko-kr, en-us, ja-jp, zh-cn (default: ko-kr)")
    parser.add_argument("--list-menus", action="store_true", help="list available menus and categories for the selected game")
    parser.add_argument("--ingest", action="store_true", help="directly ingest crawled entries into the knowledge base")
    parser.add_argument("-t", "--theme", default=None, help="knowledge base (theme) for --ingest")
    parser.add_argument("-o", "--output-dir", default=None, help="directory to save the AsciiDoc text of each entry")


def _safe_name(text: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", text)


def _print_menu(menus: list[dict[str, Any]], indent: str = "  ") -> None:
    for menu in menus:
        page_flag = " (has a page)" if menu.get("has_page") else ""
        print(f"{indent}- [{menu.get('id')}] {menu.get('name')}{page_flag}")
        if menu.get("sub_menus"):
            _print_menu(menu["sub_menus"], indent + "    ")


def run_wiki_crawl(args: Any) -> int:
    game = args.game or "genshin"
    lang = args.lang or "ko-kr"
    client = HoYoWikiClient(default_lang=lang)

    if args.list_menus:
        games = ["genshin", "hsr", "zzz", "honkai3rd"] if game == "all" else [game]
        for g in games:
            print(f"\n[{_GAME_NAMES.get(g, g)} menus]")
            try:
                _print_menu(client.get_menus(g, lang=lang))
            except Exception as exc:  # noqa: BLE001
                print(f"  Menu request failed: {exc}", file=sys.stderr)
        return 0

    if args.search:
        keyword = args.search.strip()
        print(f"[{_GAME_NAMES.get(game, game)}] Search results for '{keyword}':")
        try:
            results = client.search(keyword, game=game, lang=lang)
        except Exception as exc:  # noqa: BLE001
            print(f"Search failed: {exc}", file=sys.stderr)
            return 1
        if not results:
            print("  No search result.")
            return 0
        for item in results:
            entry_id = item.get("entry_page_id")
            desc = (item.get("desc") or "").replace("\n", " ")[:60]
            print(f"  - [{entry_id}] {item.get('name')} (https://wiki.hoyolab.com/pc/{game}/entry/{entry_id})")
            if desc:
                print(f"      {desc}…")
        return 0

    out_dir = Path(args.output_dir) if args.output_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    target_url = args.url.strip() if args.url else (f"https://wiki.hoyolab.com/pc/{game}/entry/{args.entry_id.strip()}" if args.entry_id else None)
    if target_url:
        print(f"HoYoWiki collection of one page: {target_url}")
        try:
            doc = fetch_hoyowiki(target_url, full_content=True, lang=lang)
        except Exception as exc:  # noqa: BLE001
            print(f"Collection failed: {exc}", file=sys.stderr)
            return 1
        if out_dir:
            out_file = out_dir / f"{doc.meta.get('entry_page_id') or 'entry'}_{_safe_name(doc.title or 'entry')}.adoc"
            out_file.write_text(doc.raw_text, encoding="utf-8")
            print(f"Saved: {out_file}")
        if args.ingest:
            with ingest_session(args) as session:
                report = session.ingest(target_url, source=SOURCE)
            print(report.telegram_summary())
            return 0 if report.error is None else 1
        if getattr(args, "json", False):
            print(json.dumps(doc.model_dump(), ensure_ascii=False, indent=2))
        else:
            print(f"\nTitle: {doc.title}\nURL: {doc.canonical_url}\nBody length: {len(doc.raw_text)} chars")
            print("-" * 40)
            print(doc.raw_text[:1000] + ("\n…" if len(doc.raw_text) > 1000 else ""))
        return 0

    print(f"HoYoWiki batch crawl starts (game={game}, menu={args.menu or 'all'}, limit={args.limit or 'no limit'}, delay={args.delay}s)")
    count = errors = 0

    def _crawl(session: Any) -> None:
        nonlocal count, errors
        for doc in crawl_hoyowiki(game=game, menu_filter=args.menu, limit=args.limit, delay=args.delay, lang=lang):
            count += 1
            print(f"[{count}] {doc.title} ({doc.canonical_url}) - {len(doc.raw_text)} chars")
            if out_dir:
                (out_dir / f"{doc.meta.get('entry_page_id') or count}_{_safe_name(doc.title or 'entry')}.adoc").write_text(doc.raw_text, encoding="utf-8")
            if session is not None:
                try:
                    report = session.ingest(doc.canonical_url, source=SOURCE)
                    if report.error:
                        print(f"    -> Ingest failed: {report.error}")
                        errors += 1
                    else:
                        print(f"    -> {report.telegram_summary().splitlines()[0]}")
                except Exception as exc:  # noqa: BLE001
                    print(f"    -> Ingest exception: {exc}", file=sys.stderr)
                    errors += 1

    if args.ingest:
        with ingest_session(args) as session:
            _crawl(session)
    else:
        _crawl(None)
    print(f"\nCrawl done: {count} collected (ingest failures: {errors})")
    return 0 if errors == 0 else 1
