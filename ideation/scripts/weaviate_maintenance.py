import structlog
import weaviate

logger = structlog.get_logger()

def hard_delete_document(weaviate_url, api_key, tenant_id, document_id):
    """
    حذف قطعی (Hard Delete) تمامی وکتورهای یک داکیومنت در یک Tenant مشخص
    """
    client = weaviate.Client(
        url=weaviate_url,
        auth_client_secret=weaviate.AuthApiKey(api_key=api_key)
    )
    
    logger.info("Initiating hard delete", tenant_id=tenant_id, document_id=document_id)

    # Multi-tenant safety check
    where_filter = {
        "operator": "And",
        "operands": [
            {
                "path": ["tenantId"],
                "operator": "Equal",
                "valueText": tenant_id
            },
            {
                "path": ["publicDocumentId"],
                "operator": "Equal",
                "valueText": document_id
            }
        ]
    }
    
    result = client.batch.delete_objects(
        class_name="DocumentChunk",
        where=where_filter
    )
    
    successful = result.get("results", {}).get("successful", 0)
    failed = result.get("results", {}).get("failed", 0)
    
    logger.info("Hard delete completed", successful_chunks=successful, failed_chunks=failed)

if __name__ == "__main__":
    hard_delete_document("http://localhost:8080", "your_secure_weaviate_key", "tenant_1", "doc_100")
