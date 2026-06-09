"""Anthropic subscription (Claude Code monthly plan) cache mode.
Cache = automatic prompt caching via `claude -p` with --setting-sources project,local
--exclude-dynamic (TTL 1h). Warm pool reuse via burnless.warm_session.
"""
MECHANISM = "cli_setting_sources"
KEEPALIVE = True
TTL = "1h"
EXTRA_FLAGS = ["--setting-sources", "project,local", "--exclude-dynamic"]


def warm():
    from .. import warm_session
    return warm_session
