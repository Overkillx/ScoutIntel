"""Natural-language search endpoint.

The request body carries a sentence; the response carries the structured
query that sentence was parsed into, plus the results that structured query
produced. Returning the parse is the point, not a debugging aid: a natural
language interface that only shows results asks to be trusted, while one
that shows its interpretation can be checked.

No model-generated SQL touches the database at any point -- the parser's
entire output surface is a validated `SearchQuery` (see
app/schemas/search.py and DECISIONS.md).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.search import SearchRequest, SearchResponse, SearchResultOut
from app.search.parser import InvalidQueryError, UnparseableQueryError, parse_query
from app.services.search import (
    AmbiguousPlayerNameError,
    PlayerNameNotFoundError,
    execute_search,
)
from app.services.similarity import (
    NoVectorError,
    PlayerNotFoundError,
    UnrecognizedPositionError,
)

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.post("/", response_model=SearchResponse)
def search(payload: SearchRequest, db: Session = Depends(get_db)):
    """Parse a natural-language scouting query and run it.

    Status codes distinguish where a query failed, since the fixes differ:
      400 -- the sentence couldn't be parsed, or parsed into a query that
             contradicts itself ("under 30 and over 35"). Rephrase.
      404 -- the query was fine but names a player that doesn't exist.
      422 -- the query was fine but a name matches several players; the
             detail lists them with their ids so the caller can pick one.
    """
    try:
        query = parse_query(payload.query)
    except (UnparseableQueryError, InvalidQueryError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        outcome = execute_search(db, query)
    except PlayerNameNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except AmbiguousPlayerNameError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except PlayerNotFoundError:
        raise HTTPException(status_code=404, detail="Player not found")
    except NoVectorError:
        raise HTTPException(
            status_code=404, detail="No similarity vector computed for this player"
        )
    except UnrecognizedPositionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return SearchResponse(
        query=outcome.query,
        resolved_player_id=outcome.resolved_player_id,
        resolved_player_name=outcome.resolved_player_name,
        results=[
            SearchResultOut(
                rank=rank,
                player_id=player.player_id,
                name=player.short_name,
                position=player.primary_position,
                club=player.club_name,
                age=player.age,
                value_eur=player.value_eur,
                preferred_foot=player.preferred_foot,
            )
            for rank, player in enumerate(outcome.players, start=1)
        ],
    )
