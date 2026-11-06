"""Browser origin checks shared by HTTP mutations and both WebSocket routes."""

from urllib.parse import urlsplit


def same_origin(origin, url):
    """Non-browser clients omit Origin; browsers must match the served origin.

    Token authentication remains required by the CLI, including localhost.
    Host-based comparison alone is not authentication or DNS-rebinding protection.
    """
    if origin is None:
        return True
    try:
        source, target = urlsplit(origin), urlsplit(str(url))
        target_scheme = {"ws": "http", "wss": "https"}.get(target.scheme, target.scheme)
        def port(u, scheme):
            return u.port or (443 if scheme == "https" else 80)
        return (source.scheme in ("http", "https") and source.scheme == target_scheme
                and source.hostname == target.hostname and port(source, source.scheme) == port(target, target_scheme)
                and not source.username and not source.password and source.path in ("", "/")
                and not source.query and not source.fragment)
    except ValueError:
        return False
