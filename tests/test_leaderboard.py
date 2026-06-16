"""
Tests for the Tap Revolution high-score leaderboard: ranking, top-ten capping,
qualification, persistence, and the dated reset/backup.

Run directly:      python tests/test_leaderboard.py
Or via unittest:   python -m unittest discover tests
"""
##pylint: disable=wrong-import-position

import datetime
import os
import sys
import tempfile
import unittest

## Make the repo root importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyxielib.tap_revolution_leaderboard import Leaderboard


class LeaderboardTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'leaderboard.yaml')

    def _board(self, top_n=10) -> Leaderboard:
        return Leaderboard(self.path, top_n=top_n)

    def test_ranking_descending(self):
        lb = self._board()
        lb.add('a.trl', 'A', 'Mid', 50)
        lb.add('a.trl', 'A', 'High', 100)
        lb.add('a.trl', 'A', 'Low', 10)
        self.assertEqual(lb.top('a.trl'), [('High', 100), ('Mid', 50), ('Low', 10)])

    def test_top_n_caps_and_drops_lowest(self):
        lb = self._board(top_n=3)
        for name, score in [('a', 1), ('b', 2), ('c', 3), ('d', 4)]:
            lb.add('x.trl', 'X', name, score)
        self.assertEqual(lb.top('x.trl'), [('d', 4), ('c', 3), ('b', 2)])

    def test_qualifies(self):
        lb = self._board(top_n=3)
        for name, score in [('a', 1000), ('b', 500), ('c', 100)]:
            lb.add('x.trl', 'X', name, score)
        self.assertFalse(lb.qualifies('x.trl', 100))   # ties lowest -> no
        self.assertTrue(lb.qualifies('x.trl', 101))    # beats lowest -> yes
        self.assertTrue(lb.qualifies('never.trl', 0))  # empty board -> yes

    def test_all_time_merges_levels(self):
        lb = self._board()
        lb.add('a.trl', 'A', 'Ann', 30)
        lb.add('b.trl', 'B', 'Bob', 90)
        lb.add('a.trl', 'A', 'Amy', 60)
        self.assertEqual(lb.all_time(), [('Bob', 90), ('Amy', 60), ('Ann', 30)])

    def test_levels_lists_only_scored(self):
        lb = self._board()
        lb.add('want_you_back.trl', 'Want You Back', 'C', 1)
        lb.add('Demo', 'Demo', 'D', 2)  # builtin keyed by name
        self.assertEqual(lb.levels(),
                         [('Demo', 'Demo'), ('Want You Back', 'want_you_back.trl')])

    def test_persistence_roundtrip(self):
        self._board().add('a.trl', 'A', 'C', 7)
        self.assertEqual(self._board().top('a.trl'), [('C', 7)])

    def test_results_breakdown_stored(self):
        self._board().add('a.trl', 'A', 'C', 16,
                          results={'BEST': 5, 'OK': 1, 'MISS': 0, 'SCORE': 16})
        ## SCORE is dropped (redundant with score); the rest persists to disk.
        leaders = self._board()._find('a.trl')['leaders']
        self.assertEqual(leaders[0]['results'], {'BEST': 5, 'OK': 1, 'MISS': 0})
        self.assertEqual(leaders[0]['score'], 16)

    def test_reset_backs_up_and_blanks(self):
        lb = self._board()
        lb.add('a.trl', 'A', 'C', 7)
        lb.reset()
        date = datetime.datetime.now().strftime('%Y-%m-%d')
        backup = os.path.join(self.dir, f'leaderboard_{date}.yaml')
        self.assertTrue(os.path.exists(backup))
        self.assertEqual(lb.levels(), [])
        self.assertEqual(self._board().levels(), [])


if __name__ == '__main__':
    unittest.main()
