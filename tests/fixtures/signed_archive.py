"""Build a scratch, GPG-signed mini Debian archive for index-verification
tests (spec 012 security hardening).

The archive carries one `Packages.gz` whose checksum the signed `Release`
lists; `InRelease` (clearsigned) and a detached `Release.gpg` are generated
with a throwaway key. `build_signed_archive` returns the files keyed by URL
path (relative to a mirror root) plus the exported public keyring, so tests
can drive `fetch_index` with an explicit keyring either in-process (fake
fetch) or over real HTTP.
"""

import gzip
import hashlib
import http.server
import io
import os
import subprocess
import threading
import urllib.parse

RELEASE_NAME = "trixie"
INDEX_REL_PATH = "main/binary-amd64/Packages.gz"
KEY_UID = "Test Archive Signing Key <test@example.invalid>"


def _run(cmd, env=None):
    subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)


def build_signed_archive(tmp_dir: str, index_bytes: bytes | None = None) -> tuple[dict, str]:
    """Create a signed archive tree under tmp_dir.

    Returns (files, keyring_path) where files maps an absolute URL path like
    `/dists/trixie/InRelease` to bytes and keyring_path is the armored/binary
    public keyring that verifies the signatures.
    """
    gnupg = os.path.join(tmp_dir, ".gnupg")
    os.makedirs(gnupg, 0o700)
    env = dict(os.environ, GNUPGHOME=gnupg)
    _run(
        ["gpg", "--batch", "--passphrase", "", "--quick-gen-key", KEY_UID, "rsa2048", "sign", "never"],
        env=env,
    )
    keyring = os.path.join(tmp_dir, "keyring.gpg")
    _run(["gpg", "--export", "--output", keyring, KEY_UID], env=env)

    if index_bytes is None:
        index_text = (
            "Package: demo\nVersion: 1.0\nArchitecture: amd64\n"
            "Filename: pool/main/d/demo/demo_1.0_amd64.deb\n"
            f"Size: 10\nSHA256: {hashlib.sha256(b'demo-content').hexdigest()}\n\n"
        )
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(index_text.encode("utf-8"))
        index_bytes = buf.getvalue()

    sha = hashlib.sha256(index_bytes).hexdigest()
    release_text = (
        "Origin: Test\nLabel: Test\nSuite: trixie\nCodename: trixie\n"
        "Architectures: amd64\nComponents: main\n"
        "Date: Thu, 01 Jan 2026 00:00:00 UTC\n"
        f"SHA256:\n {sha} {len(index_bytes)} {INDEX_REL_PATH}\n\n"
    )
    release_path = os.path.join(tmp_dir, "Release")
    with open(release_path, "w") as f:
        f.write(release_text)
    _run(
        ["gpg", "--batch", "--yes", "--clearsign", "--output", os.path.join(tmp_dir, "InRelease"), release_path],
        env=env,
    )
    _run(
        ["gpg", "--batch", "--yes", "--armor", "--detach-sign", "--output", os.path.join(tmp_dir, "Release.gpg"), release_path],
        env=env,
    )

    files = {}
    for name in ("Release", "InRelease", "Release.gpg"):
        with open(os.path.join(tmp_dir, name), "rb") as f:
            files[f"/dists/{RELEASE_NAME}/{name}"] = f.read()
    files[f"/dists/{RELEASE_NAME}/{INDEX_REL_PATH}"] = index_bytes
    return files, keyring


class _SignedPoolHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        files = getattr(self.server, "files", {})
        data = files.get(urllib.parse.urlsplit(self.path).path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


def serve_signed_archive(files: dict, port: int = 0):
    """Serve `files` (URL path -> bytes) over HTTP; returns the server (call
    `shutdown()` and join `thread` when done)."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _SignedPoolHandler)
    server.files = files
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.thread = thread
    return server
