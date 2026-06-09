"""Anthropic API cache mode. Cache = explicit cache_control ttl=1h via the anthropic SDK
+ extended-cache-ttl beta header. Implemented by burnless.cached_worker.
"""
MECHANISM = "sdk_cache_control"
KEEPALIVE = True
TTL = "1h"
EXTRA_HEADERS = ["extended-cache-ttl-2025-04-11"]


def worker():
    from .. import cached_worker
    return cached_worker


def warm():
    from .. import warm_session
    return warm_session
