from app.db.session import get_standalone_db
from app.services.embedding_service import EmbeddingService


def main() -> None:
    db = get_standalone_db()
    try:
        result = EmbeddingService.rebuild_knowledge_chunks(db)
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
