"""
doc_pipeline.py — Document pipeline orchestrator for batch processing.

Sprint 4 Stream F: orchestrates extract → OCR → index → store pipeline
for mixed file types (PDF, image, text) with progress tracking.

Pipeline stages:
  1. Classify file by extension (pdf / image / text / skip)
  2. Extract text content (plain text, PyPDF2 for PDFs, pytesseract OCR for images)
  3. (Optional) Index into memory_vault store
  4. Return structured PipelineResult for each file

Thread-safe: process_batch_async wraps the batch in a daemon thread.
"""

import os
import threading
from dataclasses import dataclass, field
from typing import Optional, List, Callable


from logging_config import get_logger


logger = get_logger(name="DocPipeline")

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Result of processing a single file through the pipeline."""

    filepath: str
    status: str          # "success" | "skipped" | "error"
    file_type: str       # "pdf" | "image" | "text" | "skip"
    extracted_text: str = ""
    node_ids: List[int] = field(default_factory=list)
    error: str = ""


@dataclass
class PipelineProgress:
    """Aggregate progress across a batch of files."""

    total: int = 0
    completed: int = 0
    results: List[PipelineResult] = field(default_factory=list)

    @property
    def percent(self) -> int:
        """Percentage of files completed (0–100). Returns 0 if total is 0."""
        if self.total == 0:
            return 0
        return int(self.completed / self.total * 100)


# ---------------------------------------------------------------------------
# Step 1: File classification
# ---------------------------------------------------------------------------

_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".html", ".css", ".json",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log",
    ".csv", ".tsv", ".sql", ".sh", ".bat", ".ps1", ".env", ".gitignore",
    ".dockerfile", ".cfg", ".ini",
})

_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif", ".webp",
})

_PDF_EXTENSIONS = frozenset({".pdf",})

_SKIP_EXTENSIONS = frozenset({
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".db", ".sqlite",
    ".pyc", ".pyo", ".pyd", ".zip", ".tar", ".gz", ".bz2", ".xz",
    ".7z", ".rar", ".iso", ".img", ".deb", ".rpm", ".msi",
    ".o", ".a", ".lib", ".obj", ".class", ".jar", ".war",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".ogg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
})


def _classify_file(filepath: str) -> str:
    """Classify a file by its extension.

    Returns one of the following strings:
        - ``"pdf"``  — PDF document (processed with PyPDF2)
        - ``"image"`` — Image file (processed with OCR via pytesseract)
        - ``"text"``  — Plain-text file (read directly)
        - ``"skip"``  — Binary or unsupported format (skipped)
    """
    _, ext = os.path.splitext(filepath)
    ext = ext.lower()
    if ext in _TEXT_EXTENSIONS:
        return "text"
    if ext in _PDF_EXTENSIONS:
        return "pdf"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    return "skip"


# ---------------------------------------------------------------------------
# Step 2: Text extraction
# ---------------------------------------------------------------------------


def _extract_text(filepath: str, file_type: str) -> str:
    """Extract text from a file based on its type.

    Args:
        filepath: Absolute path to the file.
        file_type: One of ``"text"``, ``"pdf"``, ``"image"`` (from ``_classify_file``).

    Returns:
        Extracted text content, or empty string on failure.
    """
    if not os.path.isfile(filepath):
        return ""

    if file_type == "text":
        return _extract_plain_text(filepath)
    elif file_type == "pdf":
        return _extract_pdf_text(filepath)
    elif file_type == "image":
        return _extract_image_text(filepath)
    else:
        return ""


def _extract_plain_text(filepath: str) -> str:
    """Read a plain-text file as UTF-8, with fallback encodings."""
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            with open(filepath, "r", encoding=encoding) as f:
                return f.read()
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def _extract_pdf_text(filepath: str) -> str:
    """Extract text from a PDF using PyPDF2."""
    try:
        import PyPDF2
        text_parts = []
        with open(filepath, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n".join(text_parts)
    except ImportError:
        logger.debug("PyPDF2 not available — skipping PDF extraction")
        return ""
    except Exception as exc:
        logger.debug(f"PDF extraction failed for {filepath}: {exc}")
        return ""


def _extract_image_text(filepath: str) -> str:
    """Extract text from an image using OCR (pytesseract)."""
    try:
        from PIL import Image
        import pytesseract
        img = Image.open(filepath)
        text = pytesseract.image_to_string(img)
        return text.strip()
    except ImportError:
        logger.debug("pytesseract/Pillow not available — skipping OCR")
        return ""
    except Exception as exc:
        logger.debug(f"OCR failed for {filepath}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Step 3: Single file processing
# ---------------------------------------------------------------------------


def process_file(filepath: str, chunk_size: int = 2000) -> PipelineResult:
    """Process a single file through the pipeline.

    Args:
        filepath: Absolute path to the file.
        chunk_size: Maximum characters per chunk (unused in current
            implementation; reserved for future splitting).

    Returns:
        A ``PipelineResult`` with extracted text and metadata.
    """
    file_type = _classify_file(filepath)

    if file_type == "skip":
        return PipelineResult(
            filepath=filepath,
            status="skipped",
            file_type="skip",
        )

    text = _extract_text(filepath, file_type)
    if not text or not text.strip():
        return PipelineResult(
            filepath=filepath,
            status="error",
            file_type=file_type,
            error="No extractable text",
        )

    return PipelineResult(
        filepath=filepath,
        status="success",
        file_type=file_type,
        extracted_text=text.strip(),
    )


# ---------------------------------------------------------------------------
# Step 4: Batch processing
# ---------------------------------------------------------------------------


def process_batch(
    filepaths: List[str],
    progress_callback: Optional[Callable] = None,
    chunk_size: int = 2000,
    index_to_vault: bool = False,
) -> PipelineProgress:
    """Process multiple files sequentially, with optional progress reporting.

    Args:
        filepaths: List of absolute file paths to process.
        progress_callback: Optional callable called with ``(completed, total)``
            after each file.
        chunk_size: Maximum characters per chunk (passed to ``process_file``).
        index_to_vault: If ``True``, store extracted text into ``memory_vault``
            via ``memory_vault.store_memory``.

    Returns:
        A ``PipelineProgress`` aggregating results.
    """
    progress = PipelineProgress(total=len(filepaths))

    for fp in filepaths:
        result = process_file(fp, chunk_size=chunk_size)

        if result.status == "success" and index_to_vault:
            try:
                import memory_vault
                node_id = memory_vault.store_memory(
                    result.extracted_text,
                    source=f"doc_pipeline:{fp}",
                )
                result.node_ids.append(node_id)
            except Exception as exc:
                result.status = "error"
                result.error = f"Vault indexing failed: {exc}"

        progress.results.append(result)
        progress.completed += 1

        if progress_callback:
            try:
                progress_callback(progress.completed, progress.total)
            except Exception:
                pass

    return progress


# ---------------------------------------------------------------------------
# Step 5: Async batch processing
# ---------------------------------------------------------------------------


def process_batch_async(
    filepaths: List[str],
    progress_callback: Optional[Callable] = None,
    done_callback: Optional[Callable] = None,
    index_to_vault: bool = False,
    chunk_size: int = 2000,
) -> threading.Thread:
    """Process files in a background thread.

    Args:
        filepaths: List of absolute file paths to process.
        progress_callback: Called with ``(completed, total)`` after each file.
        done_callback: Called with the ``PipelineProgress`` result when all
            files have been processed.
        index_to_vault: If ``True``, store extracted text into memory_vault.
        chunk_size: Passed to ``process_file``.

    Returns:
        A daemon ``threading.Thread`` that has already been started.
    """
    def _run():
        try:
            result = process_batch(
                filepaths,
                progress_callback=progress_callback,
                index_to_vault=index_to_vault,
                chunk_size=chunk_size,
            )
            if done_callback:
                try:
                    done_callback(result)
                except Exception:
                    pass
        except Exception as exc:
            logger.error(f"Async batch processing failed: {exc}")

    thread = threading.Thread(target=_run, daemon=True, name="doc-pipeline")
    thread.start()
    return thread
