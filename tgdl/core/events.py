from pydantic import BaseModel

class JobProgress(BaseModel):
    job_id: int
    total: int | None
    downloaded: int
