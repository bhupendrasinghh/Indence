import sys
import asyncio
import re
import traceback
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add backend/src to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from evidence_platform.db.models.models import Base
from evidence_platform.modules.retrieval.retriever import HybridRetriever
from evidence_platform.modules.retrieval.context_assembler import ContextAssembler
from evidence_platform.modules.synthesis.synthesizer import AnswerSynthesizer
from evidence_platform.modules.synthesis.validator import NumericDosageValidator
from evidence_platform.modules.synthesis.abstention import AbstentionLayer
from evidence_platform.adapters.models.factory import (
    get_embedding_client, get_reranker_client, get_llm_client, get_entailment_client
)
from evidence_platform.adapters.qdrant.qdrant_client import QdrantIndexClient

def safe_print(text):
    print(text.encode('ascii', 'ignore').decode('ascii'))

async def run_e2e_qa():
    db_url = "sqlite:///c:/Users/ASHIRWAD PRATAPSINGH/Desktop/Indence/backend/evidence_platform.db"
    safe_print(f"Connecting to database: {db_url}")
    engine = create_engine(db_url)
    
    Session = sessionmaker(bind=engine)
    session = Session()

    # 1. Resolve GPU and OpenRouter clients
    embedding_client = get_embedding_client()
    reranker_client = get_reranker_client()
    llm_client = get_llm_client()
    entailment_client = get_entailment_client()
    qdrant_client = QdrantIndexClient()

    safe_print(f"Embedding Client: {embedding_client.__class__.__name__}")
    safe_print(f"Reranker Client: {reranker_client.__class__.__name__}")
    safe_print(f"LLM Client: {llm_client.__class__.__name__} (Model: {llm_client.model_name})")
    safe_print(f"Entailment Client: {entailment_client.__class__.__name__}")

    # 2. Setup retriever
    retriever = HybridRetriever(
        db_session=session,
        embedding_client=embedding_client,
        qdrant_client=qdrant_client
    )

    # 3. Setup context assembler and answer synthesizer
    context_assembler = ContextAssembler(embedding_client=embedding_client, reranker_client=reranker_client)
    synthesizer = AnswerSynthesizer(llm_client=llm_client)
    numeric_validator = NumericDosageValidator()
    abstention_layer = AbstentionLayer(entailment_client=entailment_client)

    # 4. Perform search
    query = "Is simple carbohydrate counting (SCC) non-inferior to regular carbohydrate counting (RCC)?"
    safe_print(f"\nQuerying: '{query}'")

    try:
        # Retrieve candidates from hybrid RRF
        candidates = await retriever.retrieve(query=query, limit=10)
        safe_print(f"Retrieved {len(candidates)} raw chunks from Qdrant RRF.")

        # Assemble and rerank context
        assembled = await context_assembler.assemble_context(
            query=query,
            candidates=candidates,
            max_tokens_budget=2000
        )
        safe_print(f"Reranked and assembled context with {len(assembled.selected_candidates)} chunks.")

        # Synthesize answer via LLM
        safe_print("\n--- Generating structured answer via OpenRouter (Nemotron-3 Super) ---")
        answer = await synthesizer.synthesize(query=query, assembled=assembled)
        
        safe_print(f"\nGenerated Answer Status: {answer.status}")
        if answer.status == "answer":
            safe_print("\nDirect Answer Claims:")
            for idx, claim in enumerate(answer.direct_claims):
                safe_print(f"  [{idx+1}] Text: {claim.get('text')}")
                safe_print(f"      Sources: {claim.get('evidence_ids')} | Sentences: {claim.get('sentence_ids')}")

            safe_print("\nSummary Claims:")
            for idx, claim in enumerate(answer.summary_claims):
                safe_print(f"  [{idx+1}] Text: {claim.get('text')}")
                safe_print(f"      Sources: {claim.get('evidence_ids')}")

            safe_print("\nLimitations Claims:")
            for idx, claim in enumerate(answer.limitations):
                safe_print(f"  [{idx+1}] Text: {claim.get('text')}")
                safe_print(f"      Sources: {claim.get('evidence_ids')} | Sentences: {claim.get('sentence_ids')}")

            # 5. Run NLI Entailment Verification
            safe_print("\n--- Running NLI & Numeric Verification ---")
            
            # Numeric validate
            validated_direct_nums = numeric_validator.validate_answer(answer.direct_claims, answer.evidence_map)
            
            # Entailment validate
            nli_status, verified_claims, abstention_msg = await abstention_layer.verify_and_calibrate(
                status=answer.status,
                direct_claims=validated_direct_nums,
                evidence_map=answer.evidence_map
            )

            safe_print(f"\nVerification Results (Final status: {nli_status}):")
            if nli_status == "answer":
                for idx, claim in enumerate(verified_claims):
                    safe_print(f"  [{idx+1}] Text: {claim.get('text')}")
                    safe_print(f"      NLI: {claim.get('entailment_status')} | Numeric validation passed: {claim.get('numeric_validation_passed')}")
            else:
                safe_print(f"      Abstention/Rejection Reason: {abstention_msg}")
        else:
            safe_print(f"Abstention Reason: {answer.abstention_reason}")

    except Exception as e:
        safe_print(f"Error during E2E run: {e}")
        traceback.print_exc()

    session.close()

if __name__ == "__main__":
    asyncio.run(run_e2e_qa())
