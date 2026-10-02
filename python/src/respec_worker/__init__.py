"""Respec's stateless extraction worker.

Rust spawns one worker subprocess per job; the worker never opens the store.
"""
