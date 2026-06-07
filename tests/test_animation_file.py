"""
Tests for FileAnimation frame handling: deferred truncation and the ``scroll``
command.

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


class ScrollTest(_AniTest):
    ## 16-wide segments so each window is a clean, identifiable block.
    DEFS = (
        "scale|1\n"
        "segment|IN|iiiiiiiiiiiiiiii\n"
        "segment|MN|mmmmmmmmmmmmmmmm\n"
        "segment|OT|oooooooooooooooo\n"
    )

    def test_segment_slides_left(self):
        ## TRACK = IN ++ MN*4 ++ OT, windowed forward: first IN, last OT.
        ani = self._load(self.DEFS + "scroll|anon|4|MN|slide_in=IN|slide_out=OT|direction=left\n")
        codes = self._codes(ani)
        self.assertEqual(codes[0], 'i' * 16)
        self.assertEqual(codes[-1], 'o' * 16)

    def test_direction_right_reverses(self):
        ## right is the reverse traversal: first frame is the track's tail (OT).
        ani = self._load(self.DEFS + "scroll|anon|3|MN|slide_in=IN|slide_out=OT|direction=right\n")
        codes = self._codes(ani)
        self.assertEqual(codes[0], 'o' * 16)
        self.assertEqual(codes[-1], 'i' * 16)

    def test_integer_slices_build_track(self):
        ## first=A[:slide_out], middles=A[slide_in:slide_out], last=A[slide_in:].
        a = "abcdefghijklmnopqrst"  # 20 distinct tubes
        ani = self._load(f"scale|1\nsegment|A|{a}\nscroll|anon|5|A|slide_in=3|slide_out=-2|direction=left\n")
        track = a[:-2] + a[3:-2] * 3 + a[3:]
        expected = [track[o:o + 16] for o in range(0, len(track) - 16 + 1)]
        self.assertEqual(self._codes(ani), expected)

    def test_blanking_brackets_both_ends(self):
        ## Blanking puts a blank lead-in AND lead-out on both directions, so a
        ## forever loop wraps blank->blank.
        for direction in ('right', 'left'):
            codes = self._codes(self._load(
                self.DEFS + f"scroll|anon|2|MN|direction={direction}|blanking=true\n"))
            self.assertTrue(all(c == ' ' for c in codes[0]), direction)
            self.assertTrue(all(c == ' ' for c in codes[-1]), direction)
            self.assertTrue(any('m' in c for c in codes), direction)  # content appears

    def test_named_scroll_defines_sequence(self):
        ani = self._load(
            self.DEFS
            + "scroll|create|sc|3|MN|direction=left\n"
            + "sequence|insert|sc\n"
        )
        self.assertIn('sc', ani.sequences)
        self.assertTrue(len(ani.fullframes) > 0)

    def test_plain_scroll_loops_seamlessly(self):
        ## No slides/blanking -> cyclic windowing: the frame after the last wraps
        ## continuously to the first (a clean one-step right shift across the seam).
        a = "abcdefghijklmnopqrst"  # period 20 > width 16
        ani = self._load(f"scale|1\nsegment|A|{a}\nscroll|anon|2|A|direction=right\n")
        codes = self._codes(ani)
        n = len(codes)
        for i in range(n):
            cur, nxt = codes[i], codes[(i + 1) % n]
            self.assertEqual(nxt[1:], cur[:-1])  # right shift by one, incl. wrap

    def test_blanking_is_not_cyclic(self):
        ## blanking forces a linear pass (so it can have an empty lead).
        a = "abcdefghijklmnopqrst"
        plain = self._codes(self._load(f"scale|1\nsegment|A|{a}\nscroll|anon|2|A|direction=left\n"))
        blanked = self._codes(self._load(f"scale|1\nsegment|A|{a}\nscroll|anon|2|A|direction=left|blanking=true\n"))
        self.assertNotEqual(len(plain), len(blanked))
        self.assertTrue(all(c == ' ' for c in blanked[-1]))

    def test_loop_one_is_allowed(self):
        ## loop=1 is a single seamless period (cyclic) — no duplication.
        ani = self._load(self.DEFS + "scroll|anon|1|MN|direction=left\n")
        self.assertTrue(len(ani.fullframes) > 0)

    def test_loop_zero_rejected(self):
        with self.assertRaises(Exception) as ctx:
            self._load(self.DEFS + "scroll|anon|0|MN|direction=left\n")
        self.assertIn('at least 1', str(ctx.exception))

    def test_cyclic_requires_step_to_divide_length(self):
        ## period 20, step=3 does not divide -> cannot loop seamlessly -> error.
        with self.assertRaises(Exception) as ctx:
            self._load("scale|1\nsegment|A|abcdefghijklmnopqrst\nscroll|anon|1|A|step=3\n")
        self.assertIn('divide', str(ctx.exception))

    def test_empty_segment_errors_cleanly(self):
        with self.assertRaises(Exception) as ctx:
            self._load("segment|E|\nscroll|anon|2|E|direction=left\n")
        msg = str(ctx.exception)
        self.assertIn('empty', msg)
        self.assertNotIn('tubeCount', msg)  # not the opaque downstream crash

    def test_out_of_range_integer_slide_errors(self):
        ## slide_in past the segment end empties the middle loop -> clean error.
        with self.assertRaises(Exception) as ctx:
            self._load(self.DEFS + "scroll|anon|3|MN|slide_in=99|direction=left\n")
        self.assertIn('no content', str(ctx.exception))


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
