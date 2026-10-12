"""The local portal finds its stylesheets and footer data whatever the working directory, and
refuses to start without a valid footer table."""

import os
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
FOOTER = """[project.extra.footer]
contact_url = "https://example.test/contact"
agencies = [{ label = "Agency", url = "https://example.test/agency" }]
policies = [{ label = "Policy", url = "https://example.test/policy" }]
"""

BROKEN_FOOTERS = {
    "no footer table": "[project]\nsite_name = 'x'\n",
    "not toml": "[project.extra.footer\n",
    "no contact": FOOTER.replace("contact_url", "contact"),
    "empty agencies": FOOTER.replace(
        '[{ label = "Agency", url = "https://example.test/agency" }]', "[]"
    ),
    "link without url": FOOTER.replace(', url = "https://example.test/policy"', ""),
    "link url not https": FOOTER.replace("https://example.test/policy", "javascript:alert(1)"),
}


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _get(port, path):
    """(status, content type, text), or None while nothing answers."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
            return response.status, response.headers["Content-Type"], response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers["Content-Type"], ""
    except OSError:
        return None


def _wait_for(server, port, path):
    """The answer to `path` once the portal is up; None when it exited or never answered."""
    deadline = time.monotonic() + 15
    while server.poll() is None and time.monotonic() < deadline:
        if answer := _get(port, path):
            return answer
        time.sleep(0.1)
    return None


def _portal(root, directory, port=0):
    return subprocess.Popen(  # noqa: S603 - the interpreter and module are fixed
        [sys.executable, "-m", "scripts.portal", "serve", "--port", str(port)],
        cwd=directory,
        env={**os.environ, "PYTHONPATH": str(root)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


class PortalLaunchTest(unittest.TestCase):
    def test_styled_page_with_footer_is_served_when_started_from_another_directory(self):
        port = _free_port()
        project = tomllib.loads((ROOT / "docs/site-assets/zensical.toml").read_text())["project"]
        with TemporaryDirectory() as directory, _portal(ROOT, directory, port) as server:
            try:
                page = _wait_for(server, port, "/")
                styles = [_wait_for(server, port, f"/assets/{n}.css") for n in ("ncids", "portal")]
            finally:
                server.terminate()
        self.assertIn("footer", project.get("extra", {}))
        contact = project["extra"]["footer"]["contact_url"]
        self.assertIn(f'href="{contact}"', page[2] if page else "")
        self.assertEqual(
            [(style[0], style[1]) if style else None for style in styles],
            [(200, "text/css; charset=utf-8")] * 2,
        )


class FooterStartupTest(unittest.TestCase):
    """A copy of the code and assets with a broken configuration, started as `pdm run portal`."""

    def copy_with(self, directory, text):
        root = Path(directory)
        shutil.copytree(
            ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__")
        )
        assets = root / "docs/site-assets"
        shutil.copytree(ROOT / "docs/site-assets/fonts", assets / "fonts")
        for name in ("ncids.css", "portal.css"):
            shutil.copyfile(ROOT / "docs/site-assets" / name, assets / name)
        (assets / "zensical.toml").write_text(text)
        return root

    def test_a_missing_or_malformed_footer_table_stops_startup_naming_the_file(self):
        with TemporaryDirectory() as directory:
            servers = {}
            for name, text in BROKEN_FOOTERS.items():
                root = self.copy_with(Path(directory) / name.replace(" ", "-"), text)
                servers[name] = _portal(root, root)
            deadline = time.monotonic() + 20
            outcomes = {}
            for name, server in servers.items():
                try:
                    _, stderr = server.communicate(timeout=max(0.1, deadline - time.monotonic()))
                    outcomes[name] = (server.returncode != 0, "zensical.toml" in stderr)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.communicate()
                    outcomes[name] = ("still serving", "")
        self.assertEqual(outcomes, dict.fromkeys(BROKEN_FOOTERS, (True, True)))
