from __future__ import annotations

import json
import os
import pathlib
import tempfile
from collections.abc import Iterable
from typing import Any

from session_search.retrieval_experience import (
    aggregate_patterns,
    feedback_record_to_episode,
    validate_episode,
)


REFRESH_SCHEMA = "theseus.session-search-feedback-refresh.v0"


def build_feedback_refresh(
    records: Iterable[dict[str, Any]],
    *,
    seed_episodes: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    seeds = [validate_episode(dict(row)) for row in seed_episodes]
    live = [feedback_record_to_episode(dict(row)) for row in records]

    episodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*seeds, *live]:
        episode_id = row["episode_id"]
        if episode_id in seen:
            raise ValueError(f"FEEDBACK_REFRESH_DUPLICATE_EPISODE:{episode_id}")
        seen.add(episode_id)
        episodes.append(row)

    episodes.sort(key=lambda row: row["episode_id"])
    patterns = aggregate_patterns(episodes)
    return {
        "schema": REFRESH_SCHEMA,
        "source_counts": {
            "seed": len(seeds),
            "live_feedback": len(live),
        },
        "episodes": episodes,
        "patterns": patterns,
    }


def _atomic_private_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = pathlib.Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def write_feedback_refresh(
    payload: dict[str, Any],
    *,
    episodes_path: pathlib.Path,
    patterns_path: pathlib.Path,
) -> None:
    if payload.get("schema") != REFRESH_SCHEMA:
        raise ValueError("FEEDBACK_REFRESH_SCHEMA_INVALID")

    episodes = payload.get("episodes")
    patterns = payload.get("patterns")
    if not isinstance(episodes, list) or not isinstance(patterns, dict):
        raise ValueError("FEEDBACK_REFRESH_PAYLOAD_INVALID")

    episode_lines = [
        json.dumps(
            validate_episode(row),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for row in episodes
    ]
    episodes_bytes = (("\n".join(episode_lines) + "\n") if episode_lines else "").encode("utf-8")
    patterns_bytes = (
        json.dumps(
            patterns,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    _atomic_private_write(episodes_path, episodes_bytes)
    _atomic_private_write(patterns_path, patterns_bytes)
