"""Reuse Statool from the same distribution; no second implementation or store."""
from content_reference_toolkit import analytics, features, profiles

BASELINE = 'f88a0ef3cc1fdf9b9cf840893dc2b11c13e4d9f9'
def load():
    return analytics, features, profiles

def provenance():
    return {'name':'Statool content_reference_toolkit', 'baseline_commit':BASELINE,
            'source':'https://github.com/eugeniatel/content-analysis',
            'mode':'same-package analytics and feature functions; shared evidence store'}
