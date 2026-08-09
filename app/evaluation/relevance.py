from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.db.models import Player

DEFAULT_RELEVANCE_SET_PATH = Path(__file__).resolve().parent / "relevance_set.yaml"


class RelevanceSetError(Exception):
    """Malformed relevance set file, or one that references player_ids
    that don't exist in the DB. Raised loudly rather than skipping bad
    entries -- a relevance set silently missing an entry would understate
    or overstate a model's measured quality without anyone noticing.
    """


class _DuplicateKeyLoader(yaml.SafeLoader):
    """yaml.safe_load silently lets a later duplicate key overwrite an
    earlier one. That's exactly the failure mode we need to catch loudly
    (e.g. the same query player_id listed twice with different relevant
    sets), so mapping construction is overridden to raise instead.
    """


def _construct_mapping_no_duplicates(loader: yaml.SafeLoader, node: yaml.MappingNode):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_no_duplicates
)


@dataclass(frozen=True)
class RelevanceSet:
    dataset_name: str
    judgments: dict[int, frozenset[int]]

    def relevant_for(self, player_id: int) -> frozenset[int]:
        return self.judgments.get(player_id, frozenset())

    @property
    def query_player_ids(self) -> list[int]:
        return list(self.judgments.keys())


def relevance_set_fingerprint(relevance_set: RelevanceSet) -> str:
    """Deterministic SHA-256 hex digest over a relevance set's actual
    content (dataset_name + every query -> sorted relevant_ids), computed
    from a canonical JSON encoding (sorted keys, sorted id lists, no
    whitespace) so it's independent of YAML formatting, comment text, or
    key ordering in the source file.

    Two evaluation_run rows are only safe to compare head-to-head if this
    fingerprint matches on both -- otherwise a later v1-vs-v2 comparison
    could silently be scoring against two different sets of judgments.
    """
    payload = {
        "dataset_name": relevance_set.dataset_name,
        "judgments": {
            str(query_id): sorted(relevant_ids)
            for query_id, relevant_ids in sorted(relevance_set.judgments.items())
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_relevance_set(db: Session, path: Path | str = DEFAULT_RELEVANCE_SET_PATH) -> RelevanceSet:
    """Load and validate a relevance set. Raises RelevanceSetError for any
    of: malformed YAML, wrong shape, non-integer ids, duplicate query
    player_ids, duplicate relevant player_ids within one query's list, or
    any referenced player_id that doesn't exist in the DB.
    """
    path = Path(path)
    try:
        text = path.read_text()
    except OSError as exc:
        raise RelevanceSetError(f"can't read relevance set at {path}: {exc}") from exc

    try:
        raw = yaml.load(text, Loader=_DuplicateKeyLoader)
    except yaml.YAMLError as exc:
        raise RelevanceSetError(f"malformed YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RelevanceSetError(f"{path} must contain a top-level mapping")

    dataset_name = raw.get("dataset_name")
    if not isinstance(dataset_name, str) or not dataset_name:
        raise RelevanceSetError(f"{path} must have a non-empty string 'dataset_name'")

    relevance = raw.get("relevance")
    if not isinstance(relevance, dict) or not relevance:
        raise RelevanceSetError(f"{path} must have a non-empty 'relevance' mapping")

    judgments: dict[int, frozenset[int]] = {}
    referenced_ids: set[int] = set()

    for query_id, relevant_list in relevance.items():
        if not isinstance(query_id, int) or isinstance(query_id, bool):
            raise RelevanceSetError(f"query player_id {query_id!r} must be an integer")
        if not isinstance(relevant_list, list) or not relevant_list:
            raise RelevanceSetError(
                f"relevant players for query {query_id} must be a non-empty list"
            )

        seen: list[int] = []
        for relevant_id in relevant_list:
            if not isinstance(relevant_id, int) or isinstance(relevant_id, bool):
                raise RelevanceSetError(
                    f"relevant player_id {relevant_id!r} for query {query_id} must be an integer"
                )
            if relevant_id in seen:
                raise RelevanceSetError(
                    f"duplicate relevant player_id {relevant_id} for query {query_id}"
                )
            seen.append(relevant_id)

        judgments[query_id] = frozenset(seen)
        referenced_ids.add(query_id)
        referenced_ids.update(seen)

    existing_ids = {
        player_id
        for (player_id,) in db.query(Player.player_id)
        .filter(Player.player_id.in_(referenced_ids))
        .all()
    }
    missing_ids = referenced_ids - existing_ids
    if missing_ids:
        raise RelevanceSetError(
            f"relevance set references unknown player_id(s): {sorted(missing_ids)}"
        )

    return RelevanceSet(dataset_name=dataset_name, judgments=judgments)
