"""Client address behind load balancers.

Behind a cloud load balancer every request arrives from the balancer, so per address limits (sign in
codes) would count the whole country as one client. When the direct peer is a trusted proxy
(`RINGSAYS_TRUSTED_PROXIES`, CIDRs: the load balancer's proxy subnet), the client is the rightmost
X-Forwarded-For address that is not itself trusted. Anything a client writes further left is ignored,
so a forged header cannot pick an address. With no trusted proxies the header is ignored entirely.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from starlette.types import ASGIApp, Receive, Scope, Send

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(cidrs: Sequence[str]) -> list[Network]:
    return [ipaddress.ip_network(c.strip(), strict=False) for c in cidrs if c.strip()]


def _trusted(addr: str, nets: Sequence[Network]) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in n for n in nets)


def client_from(peer: str, forwarded_for: str | None, nets: Sequence[Network]) -> str:
    if not nets or not _trusted(peer, nets) or not forwarded_for:
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _trusted(hop, nets):
            try:
                return str(ipaddress.ip_address(hop))
            except ValueError:
                return peer  # garbage in the header: fall back to the proxy, never to client text
    return peer


class TrustedProxyMiddleware:
    def __init__(self, app: ASGIApp, trusted: Sequence[str]) -> None:
        self.app = app
        self.nets = parse_networks(trusted)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.nets and scope.get("client"):
            peer, port = scope["client"]
            xff = None
            for k, v in scope.get("headers", []):
                if k == b"x-forwarded-for":
                    xff = v.decode("latin-1") if xff is None else f"{xff},{v.decode('latin-1')}"
            client = client_from(peer, xff, self.nets)
            if client != peer:
                scope = {**scope, "client": (client, port)}
        await self.app(scope, receive, send)
