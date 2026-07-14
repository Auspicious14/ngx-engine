# brain/api.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from brain.query_engine import AlphaIntelligence

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

brain = AlphaIntelligence()

class QueryRequest(BaseModel):
    message: str

@app.post("/ask")
async def ask(req: QueryRequest):
    response = brain.ask(req.message)
    return {"response": response}

@app.get("/health")
def health():
    return {"status": "ok"}