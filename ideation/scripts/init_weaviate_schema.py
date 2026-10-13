import weaviate
import os

def init_schema():
    api_key = os.getenv("WEAVIATE_API_KEY", "your_secure_weaviate_key")
    client = weaviate.Client(
        url="http://localhost:8080",
        auth_client_secret=weaviate.AuthApiKey(api_key=api_key)
    )

    class_obj = {
        "class": "DocumentChunk",
        "description": "A chunk of a document for semantic search",
        "vectorizer": "none",
        "properties": [
            {
                "name": "publicDocumentId",
                "dataType": ["text"],
                "indexFilterable": True,
                "indexSearchable": False
            },
            {
                "name": "tenantId",
                "dataType": ["text"],
                "indexFilterable": True,
                "indexSearchable": False
            },
            {
                "name": "content",
                "dataType": ["text"],
                "indexFilterable": False,
                "indexSearchable": True
            },
            {
                "name": "pageStart",
                "dataType": ["int"]
            },
            {
                "name": "pageEnd",
                "dataType": ["int"]
            },
            {
                "name": "embeddingVersion",
                "dataType": ["text"],
                "indexFilterable": True,
                "indexSearchable": False
            }
        ]
    }

    if not client.schema.exists("DocumentChunk"):
        client.schema.create_class(class_obj)
        print("Schema 'DocumentChunk' created successfully.")
    else:
        print("Schema 'DocumentChunk' already exists.")

if __name__ == "__main__":
    init_schema()
