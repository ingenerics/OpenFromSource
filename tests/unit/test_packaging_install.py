import os
import tomllib
import unittest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class TestPackagingRuntimeDependencies(unittest.TestCase):
    """A standard install (`pip install .`, no extras) must import and run
    every command. The ISO reader imports ``pycdlib`` at module top, so it is
    a runtime requirement — not a dev-only extra."""

    def test_pyproject_declares_pycdlib_as_runtime_dependency(self):
        with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as f:
            data = tomllib.load(f)
        dependencies = data.get("project", {}).get("dependencies", [])
        self.assertIn("pycdlib", dependencies)

    def test_pycdlib_is_not_limited_to_the_dev_extra(self):
        with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as f:
            data = tomllib.load(f)
        dependencies = data.get("project", {}).get("dependencies", [])
        dev = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
        self.assertIn("pycdlib", dependencies)
        self.assertNotIn("pycdlib", dev)


if __name__ == "__main__":
    unittest.main()
