"""
Tests for the ``sandbox`` block of the animation DSL.

Run directly:      python tests/test_animation_sandbox.py
Or via unittest:   python -m unittest discover tests
"""
##pylint: disable=wrong-import-position

import os
import sys
import tempfile
import unittest

## Make the repo root importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyxielib.animation import (
    FullFrame, FullFrameAnimation, TubeAnimation, TubeSequence,
)
from pyxielib.animation_file import FileAnimation
from pyxielib.animation_sandbox import SandboxError, SandboxParser


class ExpressionTest(unittest.TestCase):
    def test_function_call(self):
        p = SandboxParser()
        p.parseLine('greeting = TextAnimation("HI")')
        self.assertIsInstance(p.variables['greeting'], FullFrameAnimation)

    def test_make_prefix_lookup(self):
        ## 'TextAnimation' resolves to 'makeTextAnimation'
        p = SandboxParser()
        p.parseLine('greeting = TextAnimation("HI")')
        p.parseLine('explicit = makeTextAnimation("HI")')
        self.assertIsInstance(p.variables['explicit'], FullFrameAnimation)

    def test_keyword_only_arguments(self):
        ## makeSpinAnimation has keyword-only parameters
        p = SandboxParser()
        p.parseLine('spin = SpinAnimation(rate=3, num_tubes=4)')
        self.assertIsInstance(p.variables['spin'], TubeAnimation)

    def test_variable_as_argument(self):
        p = SandboxParser()
        p.parseLine('rate_var = SpinTubeSequence(3)')
        ## A variable may be passed where a literal would go
        p.parseLine('doubled = rate_var * 2')
        self.assertEqual(len(p.variables['doubled'].frames), 16)

    def test_bare_string_becomes_fullframe(self):
        p = SandboxParser()
        p.parseLine('msg = "HELLO"')
        self.assertIsInstance(p.variables['msg'], FullFrame)

    def test_list_of_strings(self):
        p = SandboxParser()
        p.parseLine('frames = ["AB", "CD", "EF"]')
        value = p.variables['frames']
        self.assertEqual(len(value), 3)
        self.assertTrue(all(isinstance(f, FullFrame) for f in value))

    def test_list_of_function_results(self):
        p = SandboxParser()
        p.parseLine('seqs = [SpinTubeSequence(3), SpinTubeSequence(3, offset=2)]')
        value = p.variables['seqs']
        self.assertEqual(len(value), 2)
        self.assertTrue(all(isinstance(s, TubeSequence) for s in value))


class OperatorOrderTest(unittest.TestCase):
    def test_add_and_multiply(self):
        p = SandboxParser()
        p.parseLine('aa = SpinTubeSequence(3)')   ## 8 frames
        p.parseLine('bb = aa * 2 + aa')           ## 16 + 8
        self.assertEqual(len(p.variables['bb'].frames), 24)

    def test_multiply_binds_before_add(self):
        p = SandboxParser()
        p.parseLine('aa = SpinTubeSequence(3)')   ## 8 frames
        p.parseLine('cc = aa + aa * 2')           ## 8 + (8 * 2)
        self.assertEqual(len(p.variables['cc'].frames), 24)

    def test_tube_animation_multiply(self):
        p = SandboxParser()
        p.parseLine('spin = SpinAnimation(rate=3, num_tubes=4)')
        p.parseLine('tripled = spin * 3')
        self.assertIsInstance(p.variables['tripled'], TubeAnimation)
        original = p.variables['spin'].tubes[0].frameCount()
        self.assertEqual(p.variables['tripled'].tubes[0].frameCount(), original * 3)


class ConcatOperatorTest(unittest.TestCase):
    def setUp(self):
        self.p = SandboxParser()

    def test_frame_or_frame_makes_fullframe(self):
        from pyxielib.animation import HexFrame
        result = self.p._applyOp('|', HexFrame(0x1), HexFrame(0x2))
        self.assertIsInstance(result, FullFrame)
        self.assertEqual(result.tubeCount(), 2)

    def test_fullframe_concat_via_strings(self):
        self.p.parseLine('xx = "AB" | "CD"')
        value = self.p.variables['xx']
        self.assertIsInstance(value, FullFrame)
        self.assertEqual(value.tubeCount(), 4)

    def test_tube_sequence_concat_makes_frame_sequence(self):
        self.p.parseLine('aa = SpinTubeSequence(3)')
        self.p.parseLine('bb = aa | aa')
        value = self.p.variables['bb']
        self.assertIsInstance(value, list)
        self.assertTrue(all(isinstance(f, FullFrame) for f in value))
        self.assertEqual(value[0].tubeCount(), 2)

    def test_tube_animation_concat_makes_full_frame_animation(self):
        self.p.parseLine('sa = SpinAnimation(rate=3, num_tubes=2)')
        self.p.parseLine('sb = SpinAnimation(rate=3, num_tubes=3)')
        self.p.parseLine('cc = sa | sb')
        value = self.p.variables['cc']
        self.assertIsInstance(value, FullFrameAnimation)
        self.assertEqual(value.tubeCount(), 5)

    def test_plus_binds_before_concat(self):
        ## aa + aa | aa  parses as  (aa + aa) | aa
        self.p.parseLine('aa = SpinTubeSequence(3)')   ## 8 frames
        self.p.parseLine('bb = aa + aa | aa')
        value = self.p.variables['bb']
        self.assertEqual(len(value), 16)
        self.assertEqual(value[0].tubeCount(), 2)

    def test_mixed_shape_rejected(self):
        self.p.parseLine('aa = SpinTubeSequence(3)')   ## sequence
        self.p.parseLine('ff = "AB"')                  ## instant
        self.assertRaises(SandboxError, self.p.parseLine, 'bad = aa | ff')

    def test_print_concatenated_animations(self):
        self.p.parseLine('sa = SpinAnimation(rate=3, num_tubes=2)')
        self.p.parseLine('sb = SpinAnimation(rate=3, num_tubes=2)')
        self.p.parseLine('cc = sa | sb')
        self.p.parseLine('print cc')
        self.assertGreater(len(self.p.fullFrames()), 0)


class SettingsTest(unittest.TestCase):
    def test_default_delay_is_scale(self):
        p = SandboxParser(scale=0.5)
        self.assertEqual(p.settings, {'delay': 0.5, 'rate': 0.0})

    def test_delay_and_rate_mutually_exclusive(self):
        p = SandboxParser(scale=0.5)
        p.parseLine('set rate = 4')
        self.assertEqual(p.settings['delay'], 0.0)
        self.assertEqual(p.settings['rate'], 4.0)
        p.parseLine('set delay = 2')
        self.assertEqual(p.settings['rate'], 0.0)
        self.assertEqual(p.settings['delay'], 2.0)

    def test_negative_setting_rejected(self):
        p = SandboxParser()
        self.assertRaises(SandboxError, p.parseLine, 'set delay = -1')

    def test_unknown_setting_rejected(self):
        p = SandboxParser()
        self.assertRaises(SandboxError, p.parseLine, 'set bogus = 1')


class PrintConversionTest(unittest.TestCase):
    def test_print_fullframe_animation_passthrough(self):
        p = SandboxParser()
        p.parseLine('greeting = TextAnimation("HI")')
        p.parseLine('print greeting')
        self.assertIsInstance(p.printed[-1], FullFrameAnimation)

    def test_print_string_as_single_frame(self):
        p = SandboxParser(scale=0.2)
        p.parseLine('msg = "HELLO"')
        p.parseLine('print msg')
        self.assertIsInstance(p.printed[-1], FullFrameAnimation)
        self.assertEqual(len(p.fullFrames()), 1)

    def test_single_frame_uses_delay_setting(self):
        ## delay defaults to the file scale, and is overridable
        p = SandboxParser(scale=0.2)
        p.parseLine('msg = "HELLO"')
        p.parseLine('print msg')
        self.assertEqual(p.fullFrames()[0][0], 0.2)
        p.parseLine('set delay = 0.75')
        p.parseLine('print msg')
        self.assertEqual(p.fullFrames()[1][0], 0.75)

    def test_print_list_of_fullframes_uses_rate(self):
        p = SandboxParser()
        p.parseLine('set rate = 2')
        p.parseLine('frames = ["AB", "CD", "EF"]')
        p.parseLine('print frames')
        self.assertEqual(len(p.fullFrames()), 3)

    def test_print_tube_sequence_list_merges_to_full_frames(self):
        p = SandboxParser()
        p.parseLine('seqs = [SpinTubeSequence(3), SpinTubeSequence(3, offset=2)]')
        p.parseLine('print seqs')
        self.assertIsInstance(p.printed[-1], TubeAnimation)
        self.assertGreater(len(p.fullFrames()), 0)

    def test_print_tube_animation_merges_to_full_frames(self):
        p = SandboxParser()
        p.parseLine('spin = SpinAnimation(rate=4, num_tubes=16)')
        p.parseLine('print spin')
        self.assertIsInstance(p.printed[-1], TubeAnimation)
        self.assertGreater(len(p.fullFrames()), 0)


class ErrorTest(unittest.TestCase):
    def setUp(self):
        self.p = SandboxParser()

    def assertSandboxError(self, line):
        self.assertRaises(SandboxError, self.p.parseLine, line)

    def test_single_char_name_rejected(self):
        self.assertSandboxError('a = SpinTubeSequence(3)')

    def test_reserved_keyword_name(self):
        self.assertSandboxError('print = 3')

    def test_animation_class_name(self):
        self.assertSandboxError('Frame = TextAnimation("A")')

    def test_unknown_function(self):
        self.assertSandboxError('xx = NotARealFunc(1)')

    def test_non_animation_value(self):
        self.assertSandboxError('xx = 5')

    def test_function_call_as_argument(self):
        self.assertSandboxError('xx = TextAnimation(TextAnimation("A"))')

    def test_print_undefined_variable(self):
        self.assertSandboxError('print missing')

    def test_trailing_operator(self):
        self.assertSandboxError('xx = aa +')

    def test_mixed_type_list(self):
        self.assertSandboxError('xx = ["a", SpinTubeSequence(3)]')

    def test_mixed_type_list_via_concatenation(self):
        ## Each list is internally same-typed, but the concatenation is not
        self.p.parseLine('greeting = TextAnimation("X")')
        self.assertSandboxError('combined = ["AB"] + [greeting]')

    def test_empty_list(self):
        self.assertSandboxError('xx = []')

    def test_unparseable_line(self):
        self.assertSandboxError('this is not valid')


class FileIntegrationTest(unittest.TestCase):
    def _load(self, body):
        with tempfile.NamedTemporaryFile('w', suffix='.ani', delete=False) as f:
            f.write(body)
            path = f.name
        try:
            return FileAnimation(path)
        finally:
            os.unlink(path)

    def test_sandbox_appends_full_frames(self):
        ani = self._load(
            "scale|0.25\n"
            "frame|1|BOOT\n"
            "sandbox|start\n"
            'greeting = TextAnimation("HELLO")\n'
            "set delay = 0.3\n"
            'scroll = ["ONE", "TWO", "THREE"]\n'
            "print greeting\n"
            "print scroll\n"
            "sandbox|end\n"
            "frame|1|DONE\n"
        )
        ## BOOT + greeting + 3 scroll frames + DONE
        self.assertEqual(len(ani.fullframes), 6)
        self.assertEqual(ani.tubeCount(), 16)

    def test_errors_report_line_numbers(self):
        with self.assertRaises(Exception) as ctx:
            self._load(
                "sandbox|start\n"
                'good = TextAnimation("OK")\n'
                "bad = NotARealFunc(1)\n"
                "xx = 5\n"
                "print missing\n"
                "sandbox|end\n"
            )
        message = str(ctx.exception)
        self.assertIn("Line 3", message)
        self.assertIn("Line 4", message)
        self.assertIn("Line 5", message)

    def test_unclosed_block_reported(self):
        with self.assertRaises(Exception) as ctx:
            self._load(
                "sandbox|start\n"
                'gg = TextAnimation("HI")\n'
                "print gg\n"
            )
        self.assertIn("not closed", str(ctx.exception))


class MultiplyFixTest(unittest.TestCase):
    """Regression tests for the __mul__ fixes in animation.py"""

    def test_tube_sequence_int_multiply(self):
        from pyxielib.animation import HexFrame
        base = TubeSequence.makeTimed([HexFrame(0x1), HexFrame(0x2)], rate=1)
        result = base * 3
        self.assertIsInstance(result, TubeSequence)
        self.assertEqual(len(result.frames), 6)

    def test_full_frame_animation_int_multiply(self):
        anim = FullFrameAnimation.makeTimed([FullFrame(), FullFrame()], rate=1)
        result = anim * 2
        self.assertIsInstance(result, FullFrameAnimation)
        self.assertEqual(len(result.frames), 4)

    def test_tube_animation_int_multiply(self):
        from pyxielib.animation import HexFrame
        tube = TubeSequence.makeTimed([HexFrame(0x1), HexFrame(0x2)], rate=1)
        anim = TubeAnimation([tube, tube.clone()])
        result = anim * 3
        self.assertIsInstance(result, TubeAnimation)
        self.assertEqual(result.tubes[0].frameCount(), 6)
        self.assertEqual(result.tubes[1].frameCount(), 6)


class OrOperatorNativeTest(unittest.TestCase):
    """The '|' tube-concatenation operators defined directly on animation.py classes"""

    def setUp(self):
        from pyxielib.animation import HexFrame
        self.HexFrame = HexFrame

    def test_frame_or_frame(self):
        result = self.HexFrame(0x1) | self.HexFrame(0x2)
        self.assertIsInstance(result, FullFrame)
        self.assertEqual(result.tubeCount(), 2)

    def test_fullframe_or_frame_and_fullframe(self):
        from pyxielib.animation import textToFrames
        ff = FullFrame(textToFrames("AB"))
        self.assertEqual((ff | FullFrame(textToFrames("CD"))).tubeCount(), 4)
        self.assertEqual((ff | self.HexFrame(0x4)).tubeCount(), 3)

    def test_tube_sequence_or(self):
        ts = TubeSequence.makeTimed([self.HexFrame(0x1), self.HexFrame(0x2)], rate=1)
        rows = ts | ts
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].tubeCount(), 2)

    def test_tube_animation_or(self):
        ts = TubeSequence.makeTimed([self.HexFrame(0x1), self.HexFrame(0x2)], rate=1)
        left = TubeAnimation([ts.clone(), ts.clone()])
        right = TubeAnimation([ts.clone()])
        result = left | right
        self.assertIsInstance(result, FullFrameAnimation)
        self.assertEqual(result.tubeCount(), 3)

    def test_full_frame_animation_or_tube_animation(self):
        from pyxielib.animation import textToFrames
        ts = TubeSequence.makeTimed([self.HexFrame(0x1), self.HexFrame(0x2)], rate=1)
        ffa = FullFrameAnimation.makeTimed([FullFrame(textToFrames("XY"))], rate=1)
        result = ffa | TubeAnimation([ts])
        self.assertIsInstance(result, FullFrameAnimation)
        self.assertEqual(result.tubeCount(), 3)

    def test_to_full_frame_animation(self):
        ts = TubeSequence.makeTimed([self.HexFrame(0x1), self.HexFrame(0x2)], rate=1)
        result = TubeAnimation([ts.clone(), ts.clone()]).toFullFrameAnimation()
        self.assertIsInstance(result, FullFrameAnimation)
        self.assertEqual(result.tubeCount(), 2)

    def test_incompatible_or_raises_type_error(self):
        ts = TubeSequence.makeTimed([self.HexFrame(0x1)], rate=1)
        self.assertRaises(TypeError, lambda: self.HexFrame(0x1) | TubeAnimation([ts]))


if __name__ == '__main__':
    unittest.main()
