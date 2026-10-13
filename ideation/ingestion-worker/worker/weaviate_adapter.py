import uuid
import structlog
import weaviate
from typing import List

logger = structlog.get_logger()

class VectorChunk:
    def __init__(self, document_id: str, tenant_id: str, sequence: int, text: str, vector: List[float], embedding_version: str):
        self.document_id = document_id
        self.tenant_id = tenant_id
        self.sequence = sequence
        self.text = text
        self.vector = vector
        self.embedding_version = embedding_version

        # Deterministic UUID Generation (Idempotency)
        namespace = uuid.NAMESPACE_DNS
        name = f"{tenant_id}-{document_id}-{sequence}-{embedding_version}"
        self.chunk_id = str(uuid.uuid5(namespace, name))

class WeaviateIngestionAdapter:
    def __init__(self, url: str, api_key: str):
        self.client = weaviate.Client(
            url=url,
            auth_client_secret=weaviate.AuthApiKey(api_key=api_key)
        )

    def upsert_batch(self, chunks: List[VectorChunk]) -> None:
        logger.info(f"Upserting {len(chunks)} chunks to Weaviate")
        
        self.client.batch.configure(batch_size=100)
        
        with self.client.batch as batch:
            for chunk in chunks:
                properties = {
                    "publicDocumentId": chunk.document_id,
                    "tenantId": chunk.tenant_id,
                    "content": chunk.text,
                    "embeddingVersion": chunk.embedding_version
                }
                
                batch.add_data_object(
                    data_object=properties,
                    class_name="DocumentChunk",
                    uuid=chunk.chunk_id,
                    vector=chunk.vector
                )
        
        logger.info("Batch upsert completed.")
