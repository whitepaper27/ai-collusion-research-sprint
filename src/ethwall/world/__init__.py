"""Seeded synthetic world (spec docs/specs/world-scoring.md, R-WLD).

Exports exactly the contract's fixed names: `build_world(seed) -> World`,
`packet(world, role) -> Packet` and `prices(seed) -> tuple[float, ...]`. The generator, its
price model and value precision are documented in `ethwall.world._build`.
"""

from ethwall.world._build import build_world, packet, prices

__all__ = ["build_world", "packet", "prices"]
