from .crawler import crawl_repository
from .extractor import extract_document
from .staleness import score_staleness
from .standards import check_standards
from .governance import check_governance
from .aggregate import aggregate_findings, compute_trust_score

AUDITOR_TOOLS = [
    crawl_repository,
    extract_document,
    score_staleness,
    check_standards,
    check_governance,
    aggregate_findings,
]
