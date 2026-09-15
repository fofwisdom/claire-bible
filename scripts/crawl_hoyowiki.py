#!/usr/bin/env python3
"""wiki.hoyolab.com (HoYoWiki) 수집기 및 크롤러 스크립트.

사용 예시:
  # 1. 원신 메뉴 목록 조회
  python scripts/crawl_hoyowiki.py -g genshin --list-menus

  # 2. 키워드 검색
  python scripts/crawl_hoyowiki.py -g genshin -s "라이덴"

  # 3. 단건 URL 수집 및 마크다운 저장
  python scripts/crawl_hoyowiki.py -u "https://wiki.hoyolab.com/pc/genshin/entry/49" -o ./hoyowiki_data

  # 4. 붕괴: 스타레일 인물 도감 상위 10건 수집 및 저장
  python scripts/crawl_hoyowiki.py -g hsr -m "인물 도감" -l 10 -o ./hoyowiki_data/hsr

  # 5. 젠레스 존 제로 인물 도감 수집 및 Claire 지식 베이스 직접 적재
  python scripts/crawl_hoyowiki.py -g zzz -m "인물 도감" -l 5 --ingest
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 프로젝트 루트 경로 확보
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from claire.cli import cmd_hoyowiki


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HoYoWiki (wiki.hoyolab.com) Crawler & Ingestion Tool",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-g", "--game",
        default="genshin",
        choices=["genshin", "hsr", "zzz", "honkai3rd", "tot", "all"],
        help="game code (genshin, hsr, zzz, honkai3rd, tot, all)",
    )
    parser.add_argument("-m", "--menu", default=None, help="menu category name or ID (e.g. 캐릭터, 2, 장비 도감)")
    parser.add_argument("-s", "--search", default=None, help="search entries by keyword")
    parser.add_argument("-e", "--entry-id", default=None, help="specific entry page ID")
    parser.add_argument("-u", "--url", default=None, help="specific HoYoWiki URL to fetch")
    parser.add_argument("-l", "--limit", type=int, default=None, help="maximum entries to collect")
    parser.add_argument("-d", "--delay", type=float, default=0.5, help="delay in seconds between requests")
    parser.add_argument("--lang", default="ko-kr", help="language code (ko-kr, en-us, ja-jp, zh-cn)")
    parser.add_argument("--list-menus", action="store_true", help="list available menus and categories for the game")
    parser.add_argument("--ingest", action="store_true", help="directly ingest crawled entries into Claire database and vault")
    parser.add_argument("-o", "--output-dir", default=None, help="directory to save markdown documents")
    parser.add_argument("--json", action="store_true", help="output result in JSON format")

    args = parser.parse_args()
    return cmd_hoyowiki(args)


if __name__ == "__main__":
    raise SystemExit(main())
