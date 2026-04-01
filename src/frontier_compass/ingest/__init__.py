"""Feed ingestors exported by FrontierCompass."""

from frontier_compass.ingest.arxiv import ArxivClient
from frontier_compass.ingest.biorxiv import BioRxivClient
from frontier_compass.ingest.medrxiv import MedRxivClient

__all__ = ["ArxivClient", "BioRxivClient", "MedRxivClient"]
