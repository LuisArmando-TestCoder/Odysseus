# src/app_initializer.py
"""Initialize all application components and dependencies."""
import os
import logging
from typing import Dict, Any

from src.constants import (
    DATA_DIR, PERSONAL_DIR, RUNBOOK_DIR, UPLOAD_DIR,
    SESSIONS_FILE, DEFAULT_HOST, OPENAI_API_KEY
)
from src.memory import MemoryManager
from services.memory.skills import SkillsManager
from core.session_manager import SessionManager
from core.models import set_session_manager
from src.personal_docs import PersonalDocsManager
from src.api_key_manager import APIKeyManager
from src.preset_manager import PresetManager
from src.chat_processor import ChatProcessor
from src.model_discovery import ModelDiscovery
from src.chat_handler import ChatHandler
from src.research_handler import ResearchHandler
from src.upload_handler import UploadHandler
from src.search import update_search_config

logger = logging.getLogger(__name__)


def _seed_scraper_endpoint() -> None:
    """Ensure a ModelEndpoint row exists for the Gemini scraper backend.

    Called at startup when ODYSSEUS_LLM_SCRAPER=1.  Creates a shared (owner=None)
    endpoint with base_url='scraper://gemini' so every resolve_endpoint() call
    finds a valid row and never returns (None, None, None) which would trigger
    a 503 "No model endpoint configured" guard in the routes.

    Idempotent — skips creation if a scraper endpoint already exists.
    """
    if os.getenv("ODYSSEUS_LLM_SCRAPER", "").lower() not in ("1", "true", "yes"):
        return
    try:
        import uuid
        from core.database import SessionLocal, ModelEndpoint
        db = SessionLocal()
        try:
            existing = (
                db.query(ModelEndpoint)
                .filter(ModelEndpoint.base_url.like("scraper%"))
                .filter(ModelEndpoint.is_enabled.is_(True))
                .first()
            )
            if existing:
                logger.info(
                    "[Scraper] ModelEndpoint '%s' already present — skipping seed.",
                    existing.name,
                )
                return
            ep = ModelEndpoint(
                id=f"scraper-{uuid.uuid4().hex[:8]}",
                name="Gemini Web Scraper",
                base_url="scraper://gemini",
                api_key=None,
                is_enabled=True,
                model_type="llm",
                # cached_models is used by resolve_endpoint() to pick a default
                # model name when no explicit model is configured for a session.
                cached_models='["gemini-web-ui"]',
                supports_tools=False,
                owner=None,  # shared — visible to all users
            )
            db.add(ep)
            db.commit()
            logger.info(
                "[Scraper] Seeded ModelEndpoint 'Gemini Web Scraper' (scraper://gemini)."
            )
        finally:
            db.close()
    except Exception as _e:
        logger.warning("[Scraper] Could not seed scraper endpoint: %s", _e)


def create_directories():
    """Create necessary directories if they don't exist."""
    for directory in (DATA_DIR, PERSONAL_DIR, RUNBOOK_DIR, UPLOAD_DIR):
        os.makedirs(directory, exist_ok=True)
        
def initialize_managers(base_dir: str, rag_manager=None) -> Dict[str, Any]:
    """
    Initialize all manager and handler instances.

    Args:
        base_dir: Base directory path
        rag_manager: RAG manager instance (optional)
    Returns:
        Dictionary containing all initialized components
    """
    # Create directories first
    create_directories()

    # When running in web-scraper mode, ensure the sentinel ModelEndpoint exists
    # so every resolve_endpoint() call has a valid row to return.
    _seed_scraper_endpoint()

    # Initialize core managers
    memory_manager = MemoryManager(DATA_DIR)
    skills_manager = SkillsManager(DATA_DIR)
    session_manager = SessionManager(SESSIONS_FILE)
    set_session_manager(session_manager)  # Enable Session.add_message() persistence
    upload_handler = UploadHandler(base_dir, UPLOAD_DIR)
    personal_docs_manager = PersonalDocsManager(PERSONAL_DIR, rag_manager)
    # Also index the editor-documents folder (data/documents/) so documents
    # written as files are retrievable from other chats via RAG.
    try:
        from src.document_files import DOCUMENTS_DIR
        os.makedirs(DOCUMENTS_DIR, exist_ok=True)
        personal_docs_manager.add_directory(DOCUMENTS_DIR, index=True)
    except Exception as e:
        logger.warning("Failed to register documents folder for indexing: %s", e)
    api_key_manager = APIKeyManager(DATA_DIR)
    preset_manager = PresetManager(DATA_DIR)

    # Initialize memory vector store (share embedding model with RAG if available)
    memory_vector = None
    try:
        from src.memory_vector import MemoryVectorStore
        embedding_model = getattr(rag_manager, '_model', None) if rag_manager else None
        memory_vector = MemoryVectorStore(DATA_DIR, embedding_model=embedding_model)
        if memory_vector.healthy:
            # Rebuild index from existing memories if empty
            if memory_vector.count() == 0:
                existing = memory_manager.load()
                if existing:
                    memory_vector.rebuild(existing)
                    logger.info(f"Rebuilt memory vector index from {len(existing)} existing entries")
            logger.info("MemoryVectorStore initialized")
        else:
            logger.warning("MemoryVectorStore DEGRADED: ChromaDB vector memory unavailable")
            memory_vector = None
    except Exception as e:
        logger.warning(f"MemoryVectorStore DEGRADED: {e}")
        memory_vector = None

    # Initialize processors
    chat_processor = ChatProcessor(memory_manager, personal_docs_manager, memory_vector=memory_vector, skills_manager=skills_manager)
    research_handler = ResearchHandler()
    
    # Initialize chat handler with all dependencies
    chat_handler = ChatHandler(
        session_manager=session_manager,
        memory_manager=memory_manager,
        chat_processor=chat_processor,
        research_handler=research_handler,
        preset_manager=preset_manager,
        upload_handler=upload_handler,
    )
    
    # Initialize model discovery
    model_discovery = ModelDiscovery(DEFAULT_HOST, OPENAI_API_KEY)
    
    # Load and apply saved API keys
    saved_keys = api_key_manager.load()
    if "brave" in saved_keys:
        update_search_config(api_key=saved_keys["brave"])
        logger.info("Loaded Brave API key from saved configuration")
    
    return {
        "memory_manager": memory_manager,
        "memory_vector": memory_vector,
        "skills_manager": skills_manager,
        "session_manager": session_manager,
        "upload_handler": upload_handler,
        "personal_docs_manager": personal_docs_manager,
        "api_key_manager": api_key_manager,
        "preset_manager": preset_manager,
        "chat_processor": chat_processor,
        "research_handler": research_handler,
        "chat_handler": chat_handler,
        "model_discovery": model_discovery,
        "current_presets": preset_manager.presets,
        "PERSONAL_INDEX": personal_docs_manager.index
    }
