import logging

# Standard library-logging hygiene: a library package attaches a NullHandler
# to its own logger so records never reach `logging.lastResort` (which would
# print WARNING+ records to stderr when no handler is configured — e.g. when a
# CLI handler is exercised directly under `python -m unittest discover`). The
# shipped CLI still configures real handlers (stderr + log file) via
# `cli.main._setup_logging`, and records propagate to them as before, so this
# only silences the "no handler configured" fallback.
logging.getLogger(__name__).addHandler(logging.NullHandler())
