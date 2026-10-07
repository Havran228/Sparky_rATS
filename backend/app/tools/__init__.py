"""Tool registry shared by the agent loop and FastMCP.

Tools are added in slice 4. Each one is defined once, with Pydantic parameter
and result models and a side_effects flag (ADR-010).
"""
