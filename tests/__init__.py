# Test-package setup. `package_mgmt/__init__.py` attaches a NullHandler to the
# `package_mgmt` logger so `logging.lastResort` never writes WARNING+ records
# to stderr during tests, regardless of the runner. That makes the suite behave
# identically under `pytest` (which installs a capture handler) and
# `python -m unittest discover -s tests` (which would otherwise rely on
# lastResort).
#
# Note: `unittest discover -s tests` imports test modules as top-level modules,
# so this file is not loaded on that path; the cross-runner guarantee comes
# from `src/package_mgmt/__init__.py`, which every test module imports.
