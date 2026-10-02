"""Per-symbol parallelism for scans and backtests.

Work is split by symbol across forked worker processes. Inputs are placed in a module
global BEFORE the pool forks, so children inherit them copy-on-write: nothing large (and no
strategy closures) is pickled on the way in; only results come back. Results are returned in
the input order, so parallel and serial runs produce identical output.

Falls back to serial execution when `workers <= 1`, when fork is unavailable (Windows),
on macOS (fork is unsafe with some system frameworks), or inside a daemonic process
(e.g. inside a worker that is itself a daemonic child process).
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
from typing import Any, Callable, Dict, List, Sequence

_DATA: Dict[str, Any] = {}


def _call(args):
    fn, key = args
    return fn(key, _DATA)


def can_fork() -> bool:
    if sys.platform in ("win32", "darwin") or "fork" not in mp.get_all_start_methods():
        return False
    return not mp.current_process().daemon


def effective_workers(requested: int, n_items: int) -> int:
    if requested <= 1 or n_items < 8 or not can_fork():
        return 1
    return max(1, min(requested, os.cpu_count() or 1, n_items))


def map_keys(fn: Callable[[Any, Dict[str, Any]], Any], keys: Sequence, data: Dict[str, Any], workers: int = 1) -> List[Any]:
    """fn(key, data) must be a module-level function (picklable by reference)."""
    w = effective_workers(workers, len(keys))
    if w == 1:
        return [fn(k, data) for k in keys]
    global _DATA
    _DATA = data
    try:
        ctx = mp.get_context("fork")
        with ctx.Pool(w) as pool:
            return pool.map(_call, [(fn, k) for k in keys], chunksize=max(1, len(keys) // (w * 4)))
    finally:
        _DATA = {}
