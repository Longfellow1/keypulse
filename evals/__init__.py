"""Penpal evaluation harness (E3 retrieval eval, V0).

Lives outside the ``penpal`` package on purpose: it consumes the product through
its public seams (source plugins, registry, MCP server) and must not add edges to
the import-linter contracts that protect the core.
"""
