"""Optional client for external MCP servers (ADR-010).

Discovered tools stay disabled until the owner whitelists each one. The
whitelist UI arrives in slice 4. Nothing in this module calls the network.
"""

from __future__ import annotations


def external_tools_enabled() -> bool:
    return False
