"""
Scraper service package.

Exposes the shared :class:`ScraperError` exception so route code can narrow its
``except`` clauses to scraper-level failures (network, parse, timeout) and let
logic errors propagate instead of being silently swallowed as a generic scrape
"failed" status.
"""


class ScraperError(Exception):
    """Raised when a scraper fails for a recoverable, externally-caused reason.

    Distinguishes scraper-level failures (network errors, empty results,
    parse errors, rate-limit hits, captcha requirements the solver could not
    meet) from programming errors (NameError, TypeError, AttributeError,
    KeyError, ImportError). Routes that orchestrate multiple scrapers should
    ``except ScraperError`` (and *only* ScraperError) at the top level so
    logic bugs surface instead of being hidden under a "scrape failed"
    status that looks identical to a legitimate scraper outage.
    """


__all__ = ["ScraperError"]
