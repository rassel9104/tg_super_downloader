from enum import Enum

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED  = "paused"
    DONE    = "done"
    ERROR   = "error"
    CANCELED= "canceled"
