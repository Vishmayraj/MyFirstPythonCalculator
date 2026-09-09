"""
Model 2 — Ingestion subpackage
Re-exports the existing StreamIngestClient for use in the AI pipeline.
"""
from pipeline.ingest import StreamIngestClient, fetch_ingest_catalogue

__all__ = ["StreamIngestClient", "fetch_ingest_catalogue"]
