"""Canonical attribute order for the player embeddings.

`OUTFIELD_ATTRIBUTES` defines what dimension *i* of an 18-dim
`player_vectors.embedding` means, and `GOALKEEPER_ATTRIBUTES` the same for
the 6-dim `goalkeeper_vectors.embedding`. Both lists are the single source
of truth for that order: `compute_vectors.py` writes vectors in it,
`app/services/similarity.py` weights dimensions by it, and the search
parser resolves trait names to indices through it. They previously lived
only in `compute_vectors.py`, with the order restated as a comment
elsewhere -- a comment can't stay in sync, and a silent off-by-one in
dimension order would mean weighting or filtering on the wrong attribute
with no error anywhere.

Changing either list invalidates every stored embedding; vectors must be
recomputed (`python compute_vectors.py`) after any edit.
"""

OUTFIELD_ATTRIBUTES = [
    "attacking_crossing", "attacking_finishing", "attacking_short_passing",
    "skill_long_passing", "skill_ball_control", "skill_dribbling",
    "movement_acceleration", "movement_agility", "movement_balance",
    "movement_reactions", "power_shot_power", "power_jumping",
    "power_stamina", "power_strength", "mentality_aggression",
    "mentality_vision", "mentality_composure", "defending_standing_tackle",
]

GOALKEEPER_ATTRIBUTES = [
    "goalkeeping_diving", "goalkeeping_handling", "goalkeeping_kicking",
    "goalkeeping_positioning", "goalkeeping_reflexes", "goalkeeping_speed",
]

# Short, API-facing name -> source attribute column. The source names carry
# the dataset's category prefixes ("attacking_", "mentality_"), which are
# noise in a query language; the short names are what a SearchQuery
# exposes. The mapping exists so the two can't drift: dimension indices are
# derived from OUTFIELD_ATTRIBUTES below, never written down by hand.
TRAIT_ATTRIBUTES = {
    "crossing": "attacking_crossing",
    "finishing": "attacking_finishing",
    "short_passing": "attacking_short_passing",
    "long_passing": "skill_long_passing",
    "ball_control": "skill_ball_control",
    "dribbling": "skill_dribbling",
    "acceleration": "movement_acceleration",
    "agility": "movement_agility",
    "balance": "movement_balance",
    "reactions": "movement_reactions",
    "shot_power": "power_shot_power",
    "jumping": "power_jumping",
    "stamina": "power_stamina",
    "strength": "power_strength",
    "aggression": "mentality_aggression",
    "vision": "mentality_vision",
    "composure": "mentality_composure",
    "standing_tackle": "defending_standing_tackle",
}

# trait name -> its index in an outfield embedding.
TRAIT_DIMENSIONS = {
    trait: OUTFIELD_ATTRIBUTES.index(attribute)
    for trait, attribute in TRAIT_ATTRIBUTES.items()
}
