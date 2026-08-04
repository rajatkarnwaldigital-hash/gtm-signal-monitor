"""Source registry.

To add a source: implement sources/base.Source, import it, add it to SOURCES.
Nothing else in the pipeline needs to change.

Deliberately NOT here: Y Combinator. yc-gtm-monitor-actions already covers YC
and runs at 03:30 UTC; duplicating it would just produce two digests for the
same companies.
"""

from .techstars_getro import TechstarsGetro

SOURCES = [
    TechstarsGetro(),
]
