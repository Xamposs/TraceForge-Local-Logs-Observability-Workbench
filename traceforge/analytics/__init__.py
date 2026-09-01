"""Analytics helpers: timeline, severity distribution, top-N, signatures, correlation."""

from traceforge.analytics.correlation import (
    CorrelatedEvent,
    build_hierarchy,
    collect_correlation,
)
from traceforge.analytics.severity import (
    severity_counts,
    severity_over_time,
)
from traceforge.analytics.signatures import (
    group_messages,
    normalize_signature,
)
from traceforge.analytics.timeline import (
    Resolution,
    TimelinePoint,
    severity_distribution,
    timeline,
    top_error_signatures,
    top_services,
)

__all__ = [
    "CorrelatedEvent",
    "Resolution",
    "TimelinePoint",
    "build_hierarchy",
    "collect_correlation",
    "group_messages",
    "normalize_signature",
    "severity_counts",
    "severity_distribution",
    "severity_over_time",
    "timeline",
    "top_error_signatures",
    "top_services",
]
