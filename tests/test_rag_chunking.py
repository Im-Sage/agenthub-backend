from app.rag.chunking import WorkspaceChunker


def test_python_symbols_are_chunked_with_exact_line_numbers():
    content = '''"""Example module."""

def first():
    return 1

class Greeter:
    def greet(self):
        return "hello"

def second(value):
    return value * 2
'''

    chunks = WorkspaceChunker().chunk_file("app/example.py", content)
    symbols = {
        chunk.symbol_name: chunk
        for chunk in chunks
        if chunk.symbol_name is not None
    }

    assert set(symbols) == {"first", "Greeter", "second"}
    assert (symbols["first"].start_line, symbols["first"].end_line) == (
        3,
        4,
    )
    assert (symbols["Greeter"].start_line, symbols["Greeter"].end_line) == (
        6,
        8,
    )
    assert (symbols["second"].start_line, symbols["second"].end_line) == (
        10,
        11,
    )
    assert symbols["first"].chunk_type == "function"
    assert symbols["Greeter"].chunk_type == "class"
    assert symbols["second"].language == "python"


def test_long_python_symbol_is_split_to_maximum_line_count():
    body = "\n".join(f"    value_{line} = {line}" for line in range(1, 13))
    content = f"def oversized():\n{body}\n"

    chunks = WorkspaceChunker(
        max_chunk_lines=5,
        overlap_lines=1,
    ).chunk_file("oversized.py", content)
    symbol_chunks = [
        chunk for chunk in chunks if chunk.symbol_name == "oversized"
    ]

    assert len(symbol_chunks) > 1
    assert all(
        chunk.end_line - chunk.start_line + 1 <= 5
        for chunk in symbol_chunks
    )
    assert symbol_chunks[0].start_line == 1
    assert symbol_chunks[-1].end_line == 13


def test_invalid_python_uses_generic_overlapping_line_windows():
    content = "\n".join(f"line {line}" for line in range(1, 101))

    chunks = WorkspaceChunker().chunk_file("broken.py", content)

    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 80),
        (66, 100),
    ]
    assert all(chunk.chunk_type == "line_window" for chunk in chunks)


def test_typescript_uses_eighty_line_windows_with_fifteen_line_overlap():
    content = "\n".join(f"const value{line} = {line};" for line in range(1, 101))

    chunks = WorkspaceChunker().chunk_file("src/example.ts", content)

    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 80),
        (66, 100),
    ]
    assert all(chunk.language == "typescript" for chunk in chunks)


def test_markdown_prefers_heading_sections_then_splits_long_sections():
    intro = ["# Intro", "Overview"]
    long_section = ["## Details"] + [
        f"detail {line}" for line in range(1, 91)
    ]
    final = ["## End", "Done"]
    content = "\n".join(intro + long_section + final)

    chunks = WorkspaceChunker().chunk_file("docs/guide.md", content)

    assert chunks[0].symbol_name == "Intro"
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 2)
    detail_chunks = [
        chunk for chunk in chunks if chunk.symbol_name == "Details"
    ]
    assert [(chunk.start_line, chunk.end_line) for chunk in detail_chunks] == [
        (3, 82),
        (68, 93),
    ]
    assert chunks[-1].symbol_name == "End"
    assert chunks[-1].chunk_type == "markdown_section"


def test_content_hash_is_stable_and_content_sensitive():
    chunker = WorkspaceChunker()

    first = chunker.chunk_file("notes.txt", "same content\n")[0]
    again = chunker.chunk_file("notes.txt", "same content\n")[0]
    changed = chunker.chunk_file("notes.txt", "different content\n")[0]

    assert first.content_hash == again.content_hash
    assert first.content_hash != changed.content_hash


def test_ignored_unsupported_binary_and_oversized_files_are_skipped():
    chunker = WorkspaceChunker(max_file_bytes=20)

    assert chunker.chunk_file("node_modules/pkg/index.js", "const x = 1") == []
    assert chunker.chunk_file(".env", "SECRET=value") == []
    assert chunker.chunk_file("package-lock.json", "{}") == []
    assert chunker.chunk_file("image.png", "not really an image") == []
    assert chunker.chunk_file("data.json", "contains\x00nul") == []
    assert chunker.chunk_file("large.txt", "x" * 21) == []
