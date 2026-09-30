"""Machine-learning layer: probability that T1 is reached before the stop.

ML is an additional, separately validated estimate shown NEXT TO the empirical
hit rate — never instead of it. Every model is trained and scored with purged,
embargoed walk-forward splits, calibrated on a separate later window, and must
beat the empirical baseline out of sample before it can be activated.
"""
