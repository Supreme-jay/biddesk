"""Bid Desk — turn an incoming RFP into a coverage matrix against past proposals.

Extraction, retrieval and scoring are entirely local and deterministic: no API
key, no network, no per-run cost. Drafting language is a separate, optional step
that runs on the client's own credentials.
"""

__version__ = "0.1.0"
