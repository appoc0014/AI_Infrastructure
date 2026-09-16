import uuid
import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from sentence_transformers import SentenceTransformer

app = FastAPI()

QDRANT_HOST = "qdrant"
QDRANT_PORT = 6333
COLLECTION_NAME = "documents"
VLLM_URL = "http://vllm-qwen:8000/v1/chat/completions"
EMBEDDING_DIM = 384  # matches all-MiniLM-L6-v2's output size

qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
embedder = SentenceTransformer("all-MiniLM-L6-v2")


@app.on_event("startup")
def setup_collection():
    existing = [c.name for c in qdrant.get_collections().collections]
    if COLLECTION_NAME not in existing:
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )


def chunk_text(text: str, chunk_size: int = 200, overlap: int = 30):
    """Splits on whitespace into overlapping word chunks.
    Overlap prevents an answer-relevant sentence from being cut
    in half at a chunk boundary and losing its context."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i:i + chunk_size]))
        i += chunk_size - overlap
    return chunks


class IngestRequest(BaseModel):
    text: str
    source: str = "manual"


@app.post("/ingest")
def ingest(req: IngestRequest):
    chunks = chunk_text(req.text)
    embeddings = embedder.encode(chunks)
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=emb.tolist(),
            payload={"text": chunk, "source": req.source},
        )
        for chunk, emb in zip(chunks, embeddings)
    ]
    qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
    return {"ingested_chunks": len(chunks)}


class QueryRequest(BaseModel):
    question: str
    top_k: int = 3


@app.post("/query")
async def query(req: QueryRequest):
    query_vector = embedder.encode(req.question).tolist()
    results = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=req.top_k,
    ).points

    context = "\n\n".join(r.payload["text"] for r in results)
    prompt = (
        "Answer the question using only the context below. "
        "If the answer isn't in the context, say you don't know.\n\n"
        f"Context:\n{context}\n\nQuestion: {req.question}"
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(VLLM_URL, json={
            "model": "Qwen/Qwen2.5-3B-Instruct",
            "messages": [{"role": "user", "content": prompt}],
        })
        answer = resp.json()["choices"][0]["message"]["content"]

    return {
        "answer": answer,
        "sources": [r.payload["source"] for r in results],
    }


@app.get("/health")
def health():
    return {"status": "ok"}
