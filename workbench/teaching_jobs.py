"""State owned by one submitted experiment, never shared with another worker."""
from dataclasses import dataclass, field
import threading


@dataclass
class ExperimentJob:
    runtime: object
    settings: dict
    key: str
    kind: str = 'chat'
    cancelled: threading.Event = field(default_factory=threading.Event)
    worker: threading.Thread | None = None
