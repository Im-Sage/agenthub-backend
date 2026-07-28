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


class IndexSummary(BaseModel):
    repository_id: int
    files_indexed: int = 0
    files_unchanged: int = 0
    files_deleted: int = 0
    chunks_written: int = 0
    chunks_deleted: int = 0
