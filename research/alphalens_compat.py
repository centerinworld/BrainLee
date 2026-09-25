"""Compatibility shim so a PRISTINE alphalens-reloaded works with sparse (month-end) factor dates (HANDOFF §10 P2-12).

alphalens.utils.compute_forward_returns ends with `df.index.levels[0].freq = freq`; with month-end factor dates pandas refuses the
business-day/custom-business-day freq ("Inferred frequency None ... does not conform to passed frequency C"). Previously this was
fixed by hand-editing site-packages (lost on every reinstall). Use instead:

    from alphalens_compat import tolerant_freq
    with tolerant_freq():
        data = al.utils.get_clean_factor_and_forward_returns(...)

Inside the block a failing DatetimeIndex.freq assignment is ignored (the freq is only metadata for later turnover/period-length
helpers, which this project does not use on sparse dates); the original setter is restored on exit.
"""
from __future__ import annotations

from contextlib import contextmanager

import pandas as pd


@contextmanager
def tolerant_freq():
    original = pd.DatetimeIndex.freq

    def _set(self, value):
        try:
            original.fset(self, value)
        except ValueError:
            pass

    pd.DatetimeIndex.freq = property(original.fget, _set)
    try:
        yield
    finally:
        pd.DatetimeIndex.freq = original
