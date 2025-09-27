from pydantic import BaseModel


class Job(BaseModel):
    id: int | None = None
    kind: str
    payload: dict
    status: str
