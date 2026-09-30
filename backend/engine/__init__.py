"""MarketEdge analysis engine.

A pure-Python (pandas/numpy) package with no web, database or provider
dependencies. It receives OHLCV DataFrames and returns explainable,
reproducible analysis results. Everything here must be free of look-ahead:
any value computed "at bar t" may only use data from bars <= t.
"""

ENGINE_VERSION = "0.1.0"
