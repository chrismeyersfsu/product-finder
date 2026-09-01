"""product-finder backtest: does waiting longer get a better deal?

Owns the pure backtest engine only: observation dicts in, result dict
out. Never touches the database, the network, or the clock beyond an
injectable `now` (packages/core stores observations and results;
packages/mcp wires them together). Callers rely on: results are
JSON-serializable, deterministic for a given seed, and always carry
coverage info and caveats rather than silently overstating certainty.
"""
