"""Registration of claire.hoyolab: HoYoLAB articles, HoYoWiki entries, and the command wiki-crawl."""

from __future__ import annotations

from claire.plugin_sdk import PluginContext

from . import _net
from .commands import build_wiki_crawl_parser, run_wiki_crawl
from .hoyolab import HoYoLabAdapter, HoYoLabFetcher
from .hoyowiki import HoyowikiAdapter, HoyowikiFetcher

HOYOLAB_HOSTS = ("hoyolab.com", "*.hoyolab.com")


def register(ctx: PluginContext) -> None:
    _net.set_context(ctx)
    ctx.fetcher(HoyowikiFetcher, name="hoyowiki", priority=120, hosts=HOYOLAB_HOSTS)
    ctx.fetcher(HoYoLabFetcher, name="hoyolab", priority=115, hosts=HOYOLAB_HOSTS)
    ctx.web_adapter(HoyowikiAdapter, name="hoyowiki", priority=40, hosts=("wiki.hoyolab.com", "*.hoyolab.com"))
    ctx.web_adapter(HoYoLabAdapter, name="hoyolab", priority=38, hosts=HOYOLAB_HOSTS)
    ctx.cli_command("wiki-crawl", build_wiki_crawl_parser, run_wiki_crawl)
