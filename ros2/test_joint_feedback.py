"""Synthetic transport fixtures for the passive bench feedback observer."""

import hashlib
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from check_joint_feedback import (
    MAX_GAP_NS,
    NANOSECONDS,
    WINDOW_NS,
    FeedbackWindow,
    read_model,
)


def message(stamp=NANOSECONDS, names=("shoulder", "slide"), positions=(0.25, 0.01)):
    return SimpleNamespace(
        name=names,
        position=positions,
        velocity=[],
        effort=[],
        header=SimpleNamespace(
            stamp=SimpleNamespace(sec=stamp // NANOSECONDS, nanosec=stamp % NANOSECONDS)
        ),
    )


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.window = FeedbackWindow({"shoulder": (-0.5, 0.5), "slide": (0, 0.08)})

    def test_complete_window_and_reordered_axes(self):
        for delta in range(0, WINDOW_NS + 1, MAX_GAP_NS):
            stamp = NANOSECONDS + delta
            sample = message(stamp, names=("slide", "shoulder"), positions=(0.02, -0.3))
            sample.velocity = [0.0, 0.0]
            self.window.receive(sample, b"publisher", delta, stamp + 1000)
            self.assertEqual(self.window.complete, delta == WINDOW_NS)
        self.assertEqual(self.window.max_age, 1000)
        self.assertEqual(self.window.max_gap, MAX_GAP_NS)

    def test_rejects_incomplete_duplicate_unknown_and_nonfinite_fields(self):
        for names, positions in [
            (("shoulder",), (0,)),
            (("shoulder", "shoulder"), (0, 0)),
            (("other", "slide"), (0, 0)),
            (("shoulder", "slide"), (0,)),
            (("shoulder", "slide"), (math.nan, 0)),
            (("shoulder", "slide"), (0, 0.09)),
        ]:
            with (
                self.subTest(names=names, positions=positions),
                self.assertRaises(ValueError),
            ):
                self.window.receive(
                    message(names=names, positions=positions), b"a", 0, NANOSECONDS
                )
        for field in ("velocity", "effort"):
            for invalid in ([0], [0, math.inf]):
                sample = message()
                setattr(sample, field, invalid)
                with (
                    self.subTest(field=field, invalid=invalid),
                    self.assertRaises(ValueError),
                ):
                    self.window.receive(sample, b"a", 0, NANOSECONDS)
        self.assertEqual(self.window.samples, 0)

    def test_missing_stale_future_or_invalid_source_stamp(self):
        for stamp in (0, 1, NANOSECONDS + 1):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                self.window.receive(message(stamp), b"a", 0, NANOSECONDS)
        sample = message()
        sample.header.stamp.nanosec = NANOSECONDS
        with self.assertRaises(ValueError):
            self.window.receive(sample, b"a", 0, NANOSECONDS)

    def test_replay_source_and_arrival_gaps_and_publisher_change(self):
        self.window.receive(message(), b"a", 0, NANOSECONDS)
        for stamp, received, publisher in [
            (NANOSECONDS, 1, b"a"),
            (NANOSECONDS - 1, 2, b"a"),
            (NANOSECONDS + MAX_GAP_NS + 1, 3, b"a"),
            (NANOSECONDS + 4, MAX_GAP_NS + 1, b"a"),
            (NANOSECONDS + 5, 5, b"b"),
            (NANOSECONDS + 6, -1, b"a"),
        ]:
            with (
                self.subTest(stamp=stamp, received=received),
                self.assertRaises(ValueError),
            ):
                self.window.receive(message(stamp), publisher, received, stamp)
        with self.assertRaisesRegex(ValueError, "delivery gap"):
            self.window.check_gap(MAX_GAP_NS + 1)
        self.assertEqual(self.window.samples, 1)
        self.assertFalse(self.window.complete)

    def test_receipt_window_alone_cannot_complete(self):
        for delta in range(0, WINDOW_NS + 1, MAX_GAP_NS):
            stamp = NANOSECONDS + delta // 2
            self.window.receive(message(stamp), b"a", delta, stamp)
        self.assertFalse(self.window.complete)

    def test_model_is_bound_to_file_and_rejects_unbounded_or_ambiguous_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rig.urdf"
            valid = (
                '<joint type="revolute" name="a"><limit lower="-1" upper="1"/></joint>'
            )
            path.write_text(
                "<robot>" + valid + '<joint name="base" type="fixed"/></robot>'
            )
            limits, digest = read_model(path)
            self.assertEqual(limits, {"a": (-1, 1)})
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
            for invalid in (
                "",
                valid + valid,
                valid.replace("revolute", "continuous"),
                valid.replace('lower="-1"', ""),
                valid.replace('upper="1"', 'upper="nan"'),
                valid.replace('lower="-1"', 'lower="2"'),
            ):
                path.write_text("<robot>" + invalid + "</robot>")
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    read_model(path)


if __name__ == "__main__":
    unittest.main()
