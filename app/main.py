from fastapi import FastAPI
from app.api.routes import evaluations, players
app = FastAPI(title="ScoutIntel API", version="1.0")
app.include_router(players.router)
app.include_router(evaluations.router)
@app.get("/api/v1/health")
def health_check():
    return {"status": "ok"}
