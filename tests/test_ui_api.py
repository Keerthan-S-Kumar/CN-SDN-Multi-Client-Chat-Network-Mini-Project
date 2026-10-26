import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TailEvents(unittest.TestCase):
    def setUp(self):
        try:
            from controller.ui_api import tail_events
        except ImportError as e:
            self.skipTest("Ryu/os-ken not importable: %s" % e)
        self.tail = tail_events

    def test_missing_file(self):
        self.assertEqual(self.tail("/nonexistent/x.jsonl"), [])

    def test_last_n_and_bad_lines(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for i in range(10):
                f.write(json.dumps({"t": i, "kind": "k", "detail": {}}) + "\n")
            f.write("not json\n")
        r = self.tail(f.name, 3)
        os.unlink(f.name)
        self.assertEqual([e["t"] for e in r], [7, 8, 9])


if __name__ == "__main__":
    unittest.main()
