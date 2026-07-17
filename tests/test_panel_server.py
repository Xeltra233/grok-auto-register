# -*- coding: utf-8 -*-
import json
import threading
import time
import unittest
from urllib.request import urlopen

from panel.server import PanelServer, STATE


class PanelServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # bind ephemeral port
        cls.server = PanelServer(host="127.0.0.1", port=18787)
        # avoid auto goproxy by not using run_panel
        STATE.reload()
        cls.server.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_health_and_overview(self):
        with urlopen("http://127.0.0.1:18787/api/health", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        self.assertTrue(data.get("ok"))
        with urlopen("http://127.0.0.1:18787/api/overview", timeout=5) as r:
            ov = json.loads(r.read().decode("utf-8"))
        self.assertTrue(ov.get("ok"))
        self.assertIn("goproxy", ov)
        self.assertIn("ts", ov)

    def test_index_served(self):
        with urlopen("http://127.0.0.1:18787/", timeout=3) as r:
            html = r.read().decode("utf-8", errors="replace")
        self.assertIn("Grok Auto Panel", html)
        self.assertIn("styles.css", html)
        self.assertIn("app.js", html)


if __name__ == "__main__":
    unittest.main()
