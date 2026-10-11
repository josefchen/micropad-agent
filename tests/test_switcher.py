"""Run: ./run.sh -m unittest discover -s tests"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daemon_perkey as d


class HoldToOpenTest(unittest.TestCase):
    def setUp(self):
        self.opened = []
        self._open, self._thread = d.open_session, d.threading.Thread
        d.open_session = self.opened.append
        d.threading.Thread = lambda target, args, daemon: type("T", (), {"start": lambda _: target(*args)})()
        d._slots.clear()
        d._view.clear()
        d._switch.update(sid=None, until=0.0, mode="wheel")
        d._slots["A"] = 1
        d._view["A"] = d.Session("A", "claude", "working", time.time())

    def tearDown(self):
        d.open_session, d.threading.Thread = self._open, self._thread

    def expire(self):
        d._switch["until"] = 0.0
        d.switcher_tick()

    def test_press_previews_and_highlights_without_opening(self):
        d.handle_press(1)
        self.assertEqual((d._switch["sid"], d._switch["mode"]), ("A", "hold"))
        self.assertTrue(d.frame()[1] & d.SEL_BIT)
        self.assertEqual(self.opened, [])

    def test_release_before_hold_cancels(self):
        d.handle_press(1)
        d.handle_release(1)
        self.expire()
        self.assertEqual(self.opened, [])

    def test_full_hold_opens(self):
        d.handle_press(1)
        self.expire()
        self.assertEqual(self.opened, ["A"])

    def test_unlit_key_is_ignored(self):
        d.handle_press(7)
        self.assertIsNone(d._switch["sid"])


class DemoOpenTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._hook, self._links = d._run_hook, d.DEMO_LINKS
        d._run_hook = lambda name, args: self.calls.append(args)
        d._demo = {"demo-0": d.Session("demo-0", "claude", "working", time.time())}

    def tearDown(self):
        d._run_hook, d.DEMO_LINKS, d._demo = self._hook, self._links, None

    def test_demo_key_opens_its_app_without_a_link(self):
        d.DEMO_LINKS = "/nonexistent/demo.json"
        self.assertTrue(d.open_session("demo-0"))
        self.assertEqual(self.calls, [["focus", "", "", "claude", "", ""]])

    def test_demo_key_opens_the_linked_session(self):
        import json, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"claude": "local_staged"}, f)
        d.DEMO_LINKS = f.name
        d.open_session("demo-0")
        self.assertEqual(self.calls[0][2:4], ["local_staged", "claude"])


if __name__ == "__main__":
    unittest.main()
