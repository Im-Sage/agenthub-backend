from pydantic import BaseModel


class CodeChunkDraft(BaseModel):
    file_path: str
    language: str
    symbol_name: str | None
    chunk_type: str
    start_line: int
    end_line: int
    content: str
    content_hash: str
