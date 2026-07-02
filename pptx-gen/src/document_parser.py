"""後方互換: document_parser は utils に移動"""
from src.utils.document_parser import (
    DocumentParseError,
    SUPPORTED_EXTENSIONS,
    extract_multiple,
    extract_text,
)

__all__ = ["DocumentParseError", "SUPPORTED_EXTENSIONS", "extract_multiple", "extract_text"]
