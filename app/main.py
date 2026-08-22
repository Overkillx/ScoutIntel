from fastapi import FastAPI
from app.api.routes import evaluations, players, search
app = FastAPI(title="ScoutIntel API", version="1.0")
app.include_router(players.router)
app.include_router(evaluations.router)
app.include_router(search.router)
@app.get("/api/v1/health")
def health_check():
    return {"status": "ok"}
