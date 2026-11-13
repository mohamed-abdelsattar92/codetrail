"""The text a file stores compressed, for the secret scan (design section 3.4): zip members and PDF Flate streams.

gitleaks reads raw bytes, so a token inside a `.docx`, `.xlsx`, `.jar` or `.zip`, or in a PDF's text, would pass it.
This inflates them with the standard library, within a cap on the bytes it produces, in chunks, so a zip bomb can't
fill memory. A file that can't be inflated whole within the cap, or that is encrypted, malformed or compressed in a way
this doesn't read, is unscannable, and the caller excludes it: the scan fails closed.
"""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from typing import IO

CHUNK_BYTES = 64 * 1024
MAX_NESTING = 3  # containers inside containers (a jar in a war in a zip) are inflated this deep; deeper is unscannable
PDF_HEADER = b"%PDF-"
PDF_HEADER_WINDOW = 1024  # readers accept the header anywhere in a file's first kilobyte
ZIP_LOCAL_HEADER = b"PK\x03\x04"
# A stream's data starts after `stream` and a line end; `endstream` ends it.
PDF_STREAM = re.compile(rb"(?<!end)stream\r?\n")
# The stream's first filter is Flate; with another first filter (an image's DCT, ASCII85), it isn't inflated.
PDF_FLATE_FIRST = re.compile(rb"/Filter\s*\[?\s*/(?:FlateDecode|Fl)(?![A-Za-z])")
INFLATED_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)


class Unscannable(Exception):
    """The file's compressed text can't be read whole within the cap; it says why."""


class _Budget:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.spent = 0

    def spend(self, size: int) -> None:
        self.spent += size
        if self.spent > self.max_bytes:
            raise Unscannable(f"inflates to more than {self.max_bytes} bytes ([source] max_inflated_bytes)")


def inflated_text(content: bytes, max_bytes: int) -> bytes | None:
    """The text stored compressed in a zip or PDF file, its pieces one per line; None for any other file.

    Raises Unscannable when it can't all be read within `max_bytes` inflated bytes."""
    if not _is_container(content):
        return None
    pieces: list[bytes] = []
    _inflate(content, _Budget(max_bytes), pieces, depth=0)
    return b"\n".join(pieces)


def _is_pdf(content: bytes) -> bool:
    return PDF_HEADER in content[:PDF_HEADER_WINDOW]


def _is_zip(content: bytes) -> bool:
    return content.startswith(ZIP_LOCAL_HEADER) or zipfile.is_zipfile(io.BytesIO(content))


def _is_container(content: bytes) -> bool:
    return _is_pdf(content) or _is_zip(content)


def _inflate(content: bytes, budget: _Budget, pieces: list[bytes], depth: int) -> None:
    if depth > MAX_NESTING:
        raise Unscannable(f"holds containers nested more than {MAX_NESTING} deep")
    found: list[bytes] = []
    if _is_pdf(content):
        found += _pdf_streams(content, budget)
    if _is_zip(content):
        found += _zip_members(content, budget)
    for piece in found:
        pieces.append(piece)
        if _is_container(piece):
            _inflate(piece, budget, pieces, depth + 1)


def _zip_members(content: bytes, budget: _Budget) -> list[bytes]:
    members = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise Unscannable("holds an encrypted member")
                if info.compress_type not in INFLATED_METHODS:
                    raise Unscannable(f"holds a member compressed with method {info.compress_type}, which isn't read")
                with archive.open(info) as member:
                    members.append(_read(member, budget))
    except Unscannable:
        raise
    except Exception as error:  # untrusted bytes: anything the zip reader raises means the text can't be read whole
        raise Unscannable("is a malformed zip file") from error
    return members


def _read(member: IO[bytes], budget: _Budget) -> bytes:
    chunks = []
    while chunk := member.read(CHUNK_BYTES):
        budget.spend(len(chunk))
        chunks.append(chunk)
    return b"".join(chunks)


def _pdf_streams(content: bytes, budget: _Budget) -> list[bytes]:
    """The inflated data of every stream whose first filter is Flate. The search only moves forward, so a crafted
    file costs time in proportion to its size."""
    streams = []
    position = 0
    while match := PDF_STREAM.search(content, position):
        end = content.find(b"endstream", match.end())
        if end == -1:
            raise Unscannable("is a malformed PDF: a stream has no end")
        dictionary = content[max(position, content.rfind(b"obj", position, match.start())) : match.start()]
        if PDF_FLATE_FIRST.search(dictionary):
            streams.append(_inflate_stream(memoryview(content)[match.end() : end], budget))
        position = end + len(b"endstream")
    return streams


def _inflate_stream(data: memoryview, budget: _Budget) -> bytes:
    decompressor = zlib.decompressobj()
    chunks = []
    try:
        for offset in range(0, len(data), CHUNK_BYTES):
            pending = bytes(data[offset : offset + CHUNK_BYTES])
            while not decompressor.eof:
                chunk = decompressor.decompress(pending, CHUNK_BYTES)
                budget.spend(len(chunk))
                chunks.append(chunk)
                pending = decompressor.unconsumed_tail
                if not pending and len(chunk) < CHUNK_BYTES:
                    break  # this piece of input is used up
            if decompressor.eof:
                break
    except zlib.error as error:
        raise Unscannable("is a malformed PDF: a Flate stream doesn't inflate") from error
    if not decompressor.eof:
        raise Unscannable("is a malformed PDF: a Flate stream is cut short")
    return b"".join(chunks)
