"""No-cache mode. Cold every call."""
MECHANISM = "none"
KEEPALIVE = False


def warm():
    return None
