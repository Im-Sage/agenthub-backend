import ast
import hashlib
import re
from pathlib import PurePosixPath

from app.rag.models import CodeChunkDraft


_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".md": "markdown",
    ".txt": "text",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}
_IGNORED_DIRECTORIES = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
_LOCK_FILES = {
    "cargo.lock",
    "composer.lock",
    "package-lock.json",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
}
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")


"""
WorkspaceChunker用于将工作区中的文件内容分割成多个代码块，以便进行处理和分析。
它支持多种编程语言和文件类型，并提供了灵活的分块策略，包括按行数分块、重叠行数以及最大文件大小限制。
对于Python文件，它还会解析抽象语法树（AST）以识别函数、类和模块文档字符串，从而生成更有意义的代码块。
对于Markdown文件，它会根据标题进行分块，以便更好地组织内容。

"""
class WorkspaceChunker:
    def __init__(
        self,
        *,
        max_chunk_lines: int = 80,
        overlap_lines: int = 15,
        max_file_bytes: int = 500 * 1024,
    ):
        if max_chunk_lines < 1:
            raise ValueError("max_chunk_lines must be positive")
        if overlap_lines < 0 or overlap_lines >= max_chunk_lines:
            raise ValueError(
                "overlap_lines must be non-negative and smaller than "
                "max_chunk_lines"
            )
        self.max_chunk_lines = max_chunk_lines
        self.overlap_lines = overlap_lines
        self.max_file_bytes = max_file_bytes

    def chunk_file(
        self,
        file_path: str,
        content: str,
    ) -> list[CodeChunkDraft]:
        normalized_path = file_path.replace("\\", "/")
        path = PurePosixPath(normalized_path)
        suffix = path.suffix.lower()
        if self._should_skip(path, suffix, content):
            return []

        lines = content.splitlines()
        if not lines:
            return []
        language = _LANGUAGES[suffix]

        if suffix == ".py":
            try:
                tree = ast.parse(content)
            except (SyntaxError, ValueError):
                return self._line_windows(
                    normalized_path,
                    language,
                    lines,
                )
            return self._python_chunks(
                normalized_path,
                lines,
                tree,
            )
        if suffix == ".md":
            return self._markdown_chunks(
                normalized_path,
                lines,
            )
        return self._line_windows(
            normalized_path,
            language,
            lines,
        )

    def _should_skip(
        self,
        path: PurePosixPath,
        suffix: str,
        content: str,
    ) -> bool:
        lower_parts = {part.lower() for part in path.parts}
        return (
            suffix not in _LANGUAGES
            or path.name.lower() == ".env"
            or path.name.lower() in _LOCK_FILES
            or bool(lower_parts & _IGNORED_DIRECTORIES)
            or "\x00" in content
            or len(content.encode("utf-8")) > self.max_file_bytes
        )

    def _python_chunks(
        self,
        file_path: str,
        lines: list[str],
        tree: ast.Module,
    ) -> list[CodeChunkDraft]:
        chunks: list[CodeChunkDraft] = []
        body = tree.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            chunks.extend(
                self._range_chunks(
                    file_path=file_path,
                    language="python",
                    lines=lines,
                    start_line=body[0].lineno,
                    end_line=body[0].end_lineno or body[0].lineno,
                    symbol_name=None,
                    chunk_type="module_docstring",
                )
            )

        node_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        for node in body:
            if not isinstance(node, node_types):
                continue
            decorator_lines = [
                decorator.lineno
                for decorator in getattr(node, "decorator_list", [])
            ]
            start_line = min([node.lineno, *decorator_lines])
            chunk_type = (
                "class"
                if isinstance(node, ast.ClassDef)
                else "function"
            )
            chunks.extend(
                self._range_chunks(
                    file_path=file_path,
                    language="python",
                    lines=lines,
                    start_line=start_line,
                    end_line=node.end_lineno or node.lineno,
                    symbol_name=node.name,
                    chunk_type=chunk_type,
                )
            )

        if chunks:
            return chunks
        return self._line_windows(file_path, "python", lines)

    def _markdown_chunks(
        self,
        file_path: str,
        lines: list[str],
    ) -> list[CodeChunkDraft]:
        headings = [
            (line_number, match.group(1).strip())
            for line_number, line in enumerate(lines, start=1)
            if (match := _MARKDOWN_HEADING.match(line))
        ]
        if not headings:
            return self._line_windows(
                file_path,
                "markdown",
                lines,
                chunk_type="markdown_section",
            )

        chunks: list[CodeChunkDraft] = []
        if headings[0][0] > 1:
            chunks.extend(
                self._range_chunks(
                    file_path=file_path,
                    language="markdown",
                    lines=lines,
                    start_line=1,
                    end_line=headings[0][0] - 1,
                    symbol_name=None,
                    chunk_type="markdown_preamble",
                )
            )
        for index, (start_line, title) in enumerate(headings):
            end_line = (
                headings[index + 1][0] - 1
                if index + 1 < len(headings)
                else len(lines)
            )
            chunks.extend(
                self._range_chunks(
                    file_path=file_path,
                    language="markdown",
                    lines=lines,
                    start_line=start_line,
                    end_line=end_line,
                    symbol_name=title,
                    chunk_type="markdown_section",
                )
            )
        return chunks

    def _line_windows(
        self,
        file_path: str,
        language: str,
        lines: list[str],
        *,
        chunk_type: str = "line_window",
    ) -> list[CodeChunkDraft]:
        return self._range_chunks(
            file_path=file_path,
            language=language,
            lines=lines,
            start_line=1,
            end_line=len(lines),
            symbol_name=None,
            chunk_type=chunk_type,
        )

    def _range_chunks(
        self,
        *,
        file_path: str,
        language: str,
        lines: list[str],
        start_line: int,
        end_line: int,
        symbol_name: str | None,
        chunk_type: str,
    ) -> list[CodeChunkDraft]:
        chunks: list[CodeChunkDraft] = []
        window_start = start_line
        step = self.max_chunk_lines - self.overlap_lines
        while window_start <= end_line:
            window_end = min(
                window_start + self.max_chunk_lines - 1,
                end_line,
            )
            chunk_content = "\n".join(
                lines[window_start - 1 : window_end]
            )
            chunks.append(
                CodeChunkDraft(
                    file_path=file_path,
                    language=language,
                    symbol_name=symbol_name,
                    chunk_type=chunk_type,
                    start_line=window_start,
                    end_line=window_end,
                    content=chunk_content,
                    content_hash=self._content_hash(chunk_content),
                )
            )
            if window_end == end_line:
                break
            window_start += step
        return chunks

    @staticmethod
    def _content_hash(content: str) -> str:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
