# Universe files for the Twelve Data adapter

Copy these into `${CSV_DATA_DIR}/universe/{MARKET}.csv` (the `marketdata` volume in Docker) and set
`GLOBAL_DATA_PROVIDER=twelvedata` / `FX_DATA_PROVIDER=twelvedata`.

These files list symbols only; they contain no market data. Before relying on it, check that your
Twelve Data plan covers each exchange and the index/ETF you use as the benchmark, and check its
redistribution terms. Set the benchmarks to symbols in the file, e.g. `BENCHMARK_US=SPY` and `BENCHMARK_FX=DXY`.

For the FX market, `base` and `quote` are required: they drive the INR conversion of every non-INR setup.
