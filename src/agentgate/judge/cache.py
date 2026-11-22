"""Content-addressed judgment cache: one small JSON file per key.

Files (rather than one big database) keep diffs reviewable when a team commits the
cache, and make concurrent CI jobs safe enough: writes go to a temp file then rename.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from agentgate.judge.base import Judge, JudgeRequest, Judgment, cache_key


class JudgmentCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> Judgment | None:
        path = self._path(key)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Judgment.model_validate(data["judgment"])

    def put(self, key: str, request: JudgeRequest, judgment: Judgment) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "key": key,
            "judge_id": judgment.judge_id,
            "request": request.model_dump(mode="json"),
            "judgment": judgment.model_dump(mode="json", exclude={"cached"}),
        }
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)


class CachedJudge:
    """Wraps a judge: read-through, write-back cache keyed by content hash."""

    def __init__(self, inner: Judge, cache: JudgmentCache) -> None:
        self.inner = inner
        self.cache = cache
        self.hits = 0
        self.misses = 0

    @property
    def judge_id(self) -> str:
        return self.inner.judge_id

    def judge(self, request: JudgeRequest) -> Judgment:
        key = cache_key(request, self.judge_id)
        hit = self.cache.get(key)
        if hit is not None:
            self.hits += 1
            return hit.model_copy(update={"cached": True})
        self.misses += 1
        judgment = self.inner.judge(request)
        self.cache.put(key, request, judgment)
        return judgment
