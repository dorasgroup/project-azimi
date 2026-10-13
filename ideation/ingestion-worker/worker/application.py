import structlog
from .domain import JobData
from .interfaces import DocumentParser, ChunkingStrategy, IngestionProvider

logger = structlog.get_logger()

class ProcessDocumentUseCase:
    def __init__(
        self,
        parser: DocumentParser,
        chunker: ChunkingStrategy,
        dify_adapter: IngestionProvider
    ):
        self.parser = parser
        self.chunker = chunker
        self.dify = dify_adapter

    def execute(self, job_data: dict):
        data = JobData(**job_data)
        logger.info("Starting processing pipeline", job_id=str(data.jobId), document_id=str(data.documentId))

        # 1. Mock Download
        logger.info("Downloading file...", bucket=data.storage.bucket, key=data.storage.objectKey)
        mock_file_path = f"/tmp/{data.documentId}.pdf"

        try:
            # 2. Extract Text
            logger.info("Extracting content...")
            raw_text = self.parser.extract(mock_file_path)

            # 3. Clean Text (Simulated)
            clean_text = raw_text.strip()

            # 4. Chunk
            logger.info("Chunking content...")
            chunks = self.chunker.chunk(str(data.documentId), clean_text)
            logger.info(f"Generated {len(chunks)} chunks")

            # 5. Submit to Dify
            logger.info("Submitting to Dify...")
            self.dify.submit_batches(chunks)

            logger.info("Pipeline completed successfully", job_id=str(data.jobId))
            # In a real app, publish Completion Event to RabbitMQ here
            return True

        except Exception as e:
            logger.error("Pipeline failed", job_id=str(data.jobId), error=str(e))
            raise
        finally:
            logger.info("Cleaning up temporary files...")
