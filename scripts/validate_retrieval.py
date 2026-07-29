import sys
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add src to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from evidence_platform.db.models.models import Base
from evidence_platform.modules.retrieval.retriever import HybridRetriever
from evidence_platform.adapters.models.factory import get_embedding_client, get_reranker_client
from evidence_platform.adapters.qdrant.qdrant_client import QdrantIndexClient

def run_retrieval_validation():
    db_url = "sqlite:///c:/Users/ASHIRWAD PRATAPSINGH/Desktop/Indence/backend/evidence_platform.db"
    print(f"Connecting to database: {db_url}")
    engine = create_engine(db_url)
    
    Session = sessionmaker(bind=engine)
    session = Session()

    # 1. Initialize our GPU clients and local Qdrant
    embedding_client = get_embedding_client()
    reranker_client = get_reranker_client()
    qdrant_client = QdrantIndexClient()

    print(f"Retrieving using real GPU-backed models.")
    print(f"Embedding Client: {embedding_client.__class__.__name__}")
    print(f"Reranker Client: {reranker_client.__class__.__name__}")

    # 2. Instantiate Retriever
    retriever = HybridRetriever(
        db_session=session,
        embedding_client=embedding_client,
        qdrant_client=qdrant_client
    )

    # 3. Perform search
    query = "individualized simple carbohydrate counting tool in type 1 diabetes mellitus"
    print(f"\nPerforming hybrid retrieval + RRF fusion for query: '{query}'")
    
    # retrieve returns list of RetrievalCandidate
    import asyncio
    results = asyncio.run(retriever.retrieve(
        query=query,
        limit=10
    ))

    print(f"\nFound {len(results)} raw candidate chunks from Qdrant RRF:")
    for idx, cand in enumerate(results):
        print(f"  [{idx+1}] Doc: {cand.pmcid} | RRF Score: {cand.score:.4f}")
        clean_text = cand.text[:150].encode('ascii', 'ignore').decode('ascii')
        print(f"      Text: {clean_text}...")

    # 4. Perform cross-encoder reranking
    print("\n--- Milestone 6 & 7: Running cross-encoder reranking (bge-reranker-v2-m3) on GPU ---")
    passages = [cand.text for cand in results]
    
    # Run reranker client asynchronously
    scores = asyncio.run(reranker_client.rerank(query, passages))

    # Pair and sort by rerank score
    reranked_results = sorted(
        zip(results, scores),
        key=lambda x: x[1],
        reverse=True
    )

    print(f"\nReranked Results (Sorted by Cross-Encoder Relevance):")
    for idx, (cand, score) in enumerate(reranked_results):
        print(f"  [{idx+1}] Doc: {cand.pmcid} | Rerank Score: {score:.4f}")
        clean_text = cand.text[:150].encode('ascii', 'ignore').decode('ascii')
        print(f"      Text: {clean_text}...")

    session.close()

if __name__ == "__main__":
    run_retrieval_validation()
