"""RAG engine: tokenising, chunking, indexing, retrieval, synthesis."""

from app.rag.chunker import ChunkSpan, chunk_text
from app.rag.embedder import TfidfModel, cosine_similarity
from app.rag.extractors import Extraction, UnsupportedFileTypeError, extract
from app.rag.index import IndexedChunk, SearchIndex
from app.rag.retriever import ScoredChunk, retrieve
from app.rag.synthesizer import Citation, Synthesis, synthesise
from app.rag.tokenizer import split_sentences, tokenize

__all__ = [
    "ChunkSpan",
    "Citation",
    "Extraction",
    "IndexedChunk",
    "ScoredChunk",
    "SearchIndex",
    "Synthesis",
    "TfidfModel",
    "UnsupportedFileTypeError",
    "chunk_text",
    "cosine_similarity",
    "extract",
    "retrieve",
    "split_sentences",
    "synthesise",
    "tokenize",
]
