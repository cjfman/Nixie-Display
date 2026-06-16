"""Persistent per-level high-score leaderboard for Tap Revolution.

Kept beside the game's other support modules. Stored as a YAML list of level
records, each with a display ``name``, a ``file`` key (the ``.trl`` basename, or the
level name for built-in charts) used to disambiguate same-named levels, and a
``leaders`` list of ``{name, score}``:

    - name: Want You Back
      file: want_you_back.trl
      leaders:
        - {name: Charles, score: 1000}

``file`` is the lookup key; ``name`` is for display. The file path defaults to
``~/.nixie/leaderboard.yaml`` (see ``TapRevolutionConfig.leaderboard_path``).
"""

import datetime
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 10


class Leaderboard:
    """Loads/persists Tap Revolution high scores, keyed by level file."""
    def __init__(self, path, *, top_n=DEFAULT_TOP_N):
        self.path = os.path.expanduser(path) if path else path
        self.top_n = top_n
        self._levels: Optional[List[Dict[str, Any]]] = None

    ## ------------------------------------------------------------------ ##
    ## Persistence                                                          ##
    ## ------------------------------------------------------------------ ##

    def _ensure_loaded(self):
        if self._levels is None:
            self._load()

    def _load(self):
        """Read the YAML file into a list of level records; empty if absent."""
        self._levels = []
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            logger.warning("Could not read leaderboard %s: %s", self.path, e)
            return
        if isinstance(data, list):
            self._levels = data

    def _save(self):
        """Write the current records to the YAML file (creating parent dirs)."""
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
            with open(self.path, 'w') as f:
                yaml.safe_dump(self._levels, f, default_flow_style=False, sort_keys=False)
        except OSError as e:
            logger.warning("Could not write leaderboard %s: %s", self.path, e)

    ## ------------------------------------------------------------------ ##
    ## Lookups                                                              ##
    ## ------------------------------------------------------------------ ##

    def _find(self, level_file) -> Optional[Dict[str, Any]]:
        """The record for a level file, or None."""
        self._ensure_loaded()
        for record in self._levels:
            if record.get('file') == level_file:
                return record
        return None

    @staticmethod
    def _sorted_leaders(leaders) -> List[Dict[str, Any]]:
        """Leaders sorted by score descending (stable, so ties keep order)."""
        return sorted(leaders, key=lambda leader: int(leader.get('score', 0)), reverse=True)

    @staticmethod
    def _as_rows(leaders) -> List[Tuple[str, int]]:
        return [(str(leader.get('name', '')), int(leader.get('score', 0))) for leader in leaders]

    def top(self, level_file) -> List[Tuple[str, int]]:
        """Top scores for one level, highest first."""
        record = self._find(level_file)
        if record is None:
            return []
        return self._as_rows(self._sorted_leaders(record.get('leaders', []))[:self.top_n])

    def all_time(self) -> List[Tuple[str, int]]:
        """Top scores across every level, highest first."""
        self._ensure_loaded()
        leaders: List[Dict[str, Any]] = []
        for record in self._levels:
            leaders.extend(record.get('leaders', []))
        return self._as_rows(self._sorted_leaders(leaders)[:self.top_n])

    def levels(self) -> List[Tuple[str, str]]:
        """(name, file) for every level with at least one score, sorted by name."""
        self._ensure_loaded()
        named = [(str(r.get('name', r.get('file', ''))), str(r.get('file', '')))
                 for r in self._levels if r.get('leaders')]
        return sorted(named, key=lambda nf: nf[0].lower())

    def qualifies(self, level_file, score) -> bool:
        """True if ``score`` would land in the level's top ``top_n``."""
        record = self._find(level_file)
        leaders = self._sorted_leaders(record.get('leaders', [])) if record else []
        if len(leaders) < self.top_n:
            return True
        return int(score) > int(leaders[-1].get('score', 0))

    ## ------------------------------------------------------------------ ##
    ## Mutation                                                             ##
    ## ------------------------------------------------------------------ ##

    def add(self, level_file, level_name, player_name, score):
        """Record a score, keeping the level's leaders sorted and capped at top_n."""
        record = self._find(level_file)
        if record is None:
            record = {'name': level_name, 'file': level_file, 'leaders': []}
            self._levels.append(record)
        else:
            record['name'] = level_name
        leaders = record.setdefault('leaders', [])
        leaders.append({'name': player_name, 'score': int(score)})
        record['leaders'] = self._sorted_leaders(leaders)[:self.top_n]
        self._save()
        logger.info("Recorded score %d for '%s' on '%s'", int(score), player_name, level_name)

    def reset(self):
        """Back up the existing file (dated) and write a fresh empty board."""
        self._backup()
        self._levels = []
        self._save()

    def _backup(self):
        """Rename the current file to <stem>_YYYY-MM-DD.yaml without clobbering."""
        self._ensure_loaded()
        if not self.path or not os.path.exists(self.path):
            return
        stem, ext = os.path.splitext(self.path)
        date = datetime.datetime.now().strftime('%Y-%m-%d')
        dest = f"{stem}_{date}{ext}"
        counter = 2
        while os.path.exists(dest):
            dest = f"{stem}_{date}_{counter}{ext}"
            counter += 1
        os.rename(self.path, dest)
        logger.info("Backed up leaderboard to %s", dest)
