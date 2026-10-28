"""Tool registry shared by the agent loop and FastMCP (ADR-010).

Each tool is defined once, with Pydantic parameter and result models and a
side_effects flag. See ``app.tools.registry``.
"""
