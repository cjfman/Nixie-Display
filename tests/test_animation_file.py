"""
Tests for FileAnimation frame handling: deferred truncation and the
``shift_step`` swept insert (with reverse-aware behavior).

Run directly:      python tests/test_animation_file.py
Or via unittest:   python -m unittest discover tests
"""
##pylint: disable=wrong-import-position

import os
import sys
import tempfile
import unittest

## Make the repo root importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyxielib.animation_file import FileAnimation


class _AniTest(unittest.TestCase):
    def _load(self, body):
        with tempfile.NamedTemporaryFile('w', suffix='.ani', delete=False) as f:
            f.write(body)
            path = f.name
        try:
            return FileAnimation(path)
        finally:
            os.unlink(path)

    @staticmethod
    def _codes(ani):
        """Each frame as its joined per-tube code string."""
        return [''.join(fr.getCode() for fr in ff.getFrames()) for _, ff in ani.fullframes]


class DeferredTruncationTest(_AniTest):
    def test_wide_frame_cropped_to_size(self):
        ani = self._load("frame|1|ABCDEFGHIJKLMNOPQRSTUVWXYZ\n")
        self.assertEqual(ani.tubeCount(), 16)
        self.assertEqual(self._codes(ani), ['ABCDEFGHIJKLMNOP'])

    def test_short_frame_padded_to_size(self):
        ani = self._load("frame|1|ABC\n")
        self.assertEqual(ani.tubeCount(), 16)
        self.assertEqual(self._codes(ani), ['ABC' + ' ' * 13])

    def test_wide_profile_wraps_when_shifted(self):
        ## A doubled 16-wide profile stays 32 tubes wide until load end, so a
        ## negative shift reads a wrapped window before the final crop to 16.
        ani = self._load(
            "segment|p|ABCDEFGHIJKLMNOP\n"
            "sequence|start|win\n"
            "frame|1|{p}{p}\n"
            "sequence|end\n"
            "sequence|insert|win|shift=-1\n"
        )
        ## window starting at tube 1 of ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP
        self.assertEqual(self._codes(ani), ['BCDEFGHIJKLMNOPA'])


class ShiftStepTest(_AniTest):
    BODY = (
        "segment|p|ABCDEFGHIJKLMNOP\n"
        "sequence|start|win\n"
        "frame|1|{p}\n"
        "sequence|end\n"
    )

    def test_sweep_advances_shift_per_copy(self):
        ani = self._load(self.BODY + "sequence|insert|win|repeat=4|shift_step=-1\n")
        codes = self._codes(ani)
        self.assertEqual(len(codes), 4)
        ## copy i is shifted left by i: its first tube is the (i)th letter
        self.assertEqual([c[0] for c in codes], ['A', 'B', 'C', 'D'])
        ## ...and a left shift blank-pads the right (cropped/padded at load end)
        self.assertEqual(codes[1], 'BCDEFGHIJKLMNOP ')
        self.assertEqual(codes[3], 'DEFGHIJKLMNOP   ')

    def test_base_shift_adds_to_step(self):
        ani = self._load(self.BODY + "sequence|insert|win|shift=-1|repeat=3|shift_step=-1\n")
        ## copy i shifted by -1 + -i  ->  first tubes B, C, D
        self.assertEqual([c[0] for c in self._codes(ani)], ['B', 'C', 'D'])

    def test_reverse_flips_sweep_order(self):
        fwd = self._codes(self._load(self.BODY + "sequence|insert|win|repeat=4|shift_step=-1\n"))
        rev = self._codes(self._load(self.BODY + "sequence|insert|win|repeat=4|shift_step=-1|reverse=true\n"))
        ## reverse runs the sweep the other way: copy order is reversed
        self.assertEqual(rev, list(reversed(fwd)))
        self.assertEqual([c[0] for c in rev], ['D', 'C', 'B', 'A'])

    def test_step_zero_matches_plain_repeat(self):
        plain = self._codes(self._load(self.BODY + "sequence|insert|win|repeat=3|shift=-2\n"))
        swept = self._codes(self._load(self.BODY + "sequence|insert|win|repeat=3|shift=-2|shift_step=0\n"))
        self.assertEqual(plain, swept)
        self.assertEqual(len(plain), 3)
        self.assertTrue(all(c == plain[0] for c in plain))


class ReverseBackCompatTest(_AniTest):
    def test_reverse_without_step_reverses_frame_order(self):
        ## Pre-existing behavior: reverse flips the inserted frames' order.
        ani = self._load(
            "sequence|start|s\n"
            "frame|1|A\n"
            "frame|1|B\n"
            "frame|1|C\n"
            "sequence|end\n"
            "sequence|insert|s|reverse=true\n"
        )
        self.assertEqual([c[0] for c in self._codes(ani)], ['C', 'B', 'A'])


if __name__ == '__main__':
    unittest.main()
