import os
import tomllib
import unittest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class TestPackagingKeyring(unittest.TestCase):
    """The archive-signing keyring ships in the built wheel/sdist: the build
    metadata declares the package-data glob and the keyring file exists at the
    path the runtime resolves (`package_mgmt/data/debian-archive-keyring.gpg`)."""

    def test_pyproject_declares_keyring_as_package_data(self):
        with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as f:
            data = tomllib.load(f)
        package_data = data.get("tool", {}).get("setuptools", {}).get("package-data", {})
        self.assertIn("package_mgmt", package_data)
        self.assertIn("data/*.gpg", package_data["package_mgmt"])

    def test_keyring_file_exists_in_source_tree(self):
        keyring = os.path.join(
            REPO_ROOT, "src", "package_mgmt", "data", "debian-archive-keyring.gpg"
        )
        self.assertTrue(
            os.path.isfile(keyring),
            "the keyring must exist at the path the runtime resolves",
        )


if __name__ == "__main__":
    unittest.main()
