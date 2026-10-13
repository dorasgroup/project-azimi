from typing import List
from uuid import uuid4
import structlog
from .interfaces import DocumentParser, ChunkingStrategy, IngestionProvider
from .domain import DocumentChunk

logger = structlog.get_logger()

class MockDocumentParser(DocumentParser):
    def supports(self, mime_type: str) -> bool:
        return True

    def extract(self, file_path: str) -> str:
        logger.info("Mock parsing file", path=file_path)
        return "این یک متن آزمایشی استخراج شده از فایل برای تست سیستم پردازش اسناد است."

class MockChunkingStrategy(ChunkingStrategy):
    def chunk(self, document_id: str, text: str) -> List[DocumentChunk]:
        return [
            DocumentChunk(
                chunk_id=uuid4(),
                document_id=document_id,
                sequence=1,
                text=text,
                token_count=15,
                metadata={"source": "mock"}
            )
        ]

class DifyDatasetAdapter(IngestionProvider):
    def __init__(self):
        # In a real app: self.client = requests.Session()
        pass

    def submit_batches(self, chunks: List[DocumentChunk]) -> None:
        logger.info(f"Submitting {len(chunks)} chunks to Dify Dataset API (Canonical Contract)")
        
        # Mapping to Canonical Contract format requested in Prompt 7
        for c in chunks:
            payload = {
                "chunkId": str(c.chunk_id),
                "sequence": c.sequence,
                "content": c.text,
                "metadata": {
                    "document_id": str(c.document_id),
                    "token_count": c.token_count
                }
            }
            logger.info("Chunk uploaded via ACL", payload=payload)
            # requests.post('http://dify-api:5001/v1/datasets/XYZ/document/create_by_text', json=payload)
