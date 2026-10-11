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


if __name__ == "__main__":
    unittest.main()
