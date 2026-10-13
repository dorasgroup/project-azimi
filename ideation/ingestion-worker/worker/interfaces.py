from abc import ABC, abstractmethod
from typing import List
from .domain import DocumentChunk

class DocumentParser(ABC):
    @abstractmethod
    def supports(self, mime_type: str) -> bool:
        pass

    @abstractmethod
    def extract(self, file_path: str) -> str:
        pass

class ChunkingStrategy(ABC):
    @abstractmethod
    def chunk(self, document_id: str, text: str) -> List[DocumentChunk]:
        pass

class IngestionProvider(ABC):
    @abstractmethod
    def submit_batches(self, chunks: List[DocumentChunk]) -> None:
        pass
