"""The text a file stores compressed, for the secret scan (design section 3.4).

gitleaks reads raw bytes, so a token inside a `.docx`, `.jar`, `.tar.gz` or a PDF's text would pass it. This decodes,
with the standard library only, zip and tar members, gzip, bzip2, xz and lzma streams, and PDF streams, all detected by
content, within a cap on the bytes it produces, in chunks, so a bomb can't fill memory. A file that can't be decoded
whole within the cap, or that is encrypted, malformed or encoded in a way this doesn't read, is unscannable, and the
caller excludes it: the scan fails closed.
"""

from __future__ import annotations

import base64
import binascii
import bz2
import functools
import io
import lzma
import re
import tarfile
import zipfile
import zlib
from collections.abc import Callable
from typing import IO, Protocol

CHUNK_BYTES = 64 * 1024
MAX_NESTING = 3  # containers inside containers (a tar in a gzip in a zip) are read this deep; deeper is unscannable
PDF_HEADER = b"%PDF-"
PDF_HEADER_WINDOW = 1024  # readers accept the header anywhere in a file's first kilobyte
ZIP_LOCAL_HEADER = b"PK\x03\x04"
TAR_MAGIC = (257, b"ustar")  # POSIX and GNU tar headers
GZIP_MAGIC = b"\x1f\x8b"
BZIP2_MAGIC = re.compile(rb"BZh[1-9]")
XZ_MAGIC = b"\xfd7zXZ\x00"
# The legacy .lzma header: the usual properties byte, a dictionary size, then the size (below 1 TB) or "unknown".
LZMA_ALONE = re.compile(rb"\x5d[\x00-\xff]{4}(?:\xff{8}|[\x00-\xff]{5}\x00{3})", re.DOTALL)
# A stream's data starts after `stream` and a line end; `endstream` ends it.
PDF_STREAM = re.compile(rb"(?<!end)stream\r?\n")
PDF_FILTER = re.compile(rb"/Filter\s*(\[[^\]]*\]|/[A-Za-z0-9]+)")
PDF_NAME = re.compile(rb"/([A-Za-z0-9]+)")
# Filters whose output is an image: the chain stops before them, and what they'd decode is scanned as it is.
PDF_IMAGE_FILTERS = {b"DCTDecode", b"DCT", b"JPXDecode", b"CCITTFaxDecode", b"CCF", b"JBIG2Decode"}
ZIP_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)


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


class _Decompressor(Protocol):
    @property
    def eof(self) -> bool: ...

    @property
    def needs_input(self) -> bool: ...

    @property
    def unused_data(self) -> bytes: ...

    def decompress(self, data: bytes, max_length: int) -> bytes: ...


class _Zlib:
    """zlib's decompressor with bz2's and lzma's interface: input it couldn't take yet stays inside."""

    def __init__(self, wbits: int) -> None:
        self._decompressor = zlib.decompressobj(wbits)
        self._tail = b""
        self._full = False

    @property
    def eof(self) -> bool:
        return self._decompressor.eof

    @property
    def needs_input(self) -> bool:
        return not self._tail and not self._full

    @property
    def unused_data(self) -> bytes:
        return self._decompressor.unused_data

    def decompress(self, data: bytes, max_length: int) -> bytes:
        output = self._decompressor.decompress(self._tail + data, max_length)
        self._tail = self._decompressor.unconsumed_tail
        self._full = len(output) == max_length
        return output


def inflated_text(content: bytes, max_bytes: int) -> bytes | None:
    """The text stored compressed in the file, its pieces one per line; None for a file that stores none.

    Raises Unscannable when it can't all be read within `max_bytes` decoded bytes."""
    if not _is_container(content):
        return None
    pieces: list[bytes] = []
    _inflate(content, _Budget(max_bytes), pieces, depth=0)
    return b"\n".join(pieces)


def _is_pdf(content: bytes) -> bool:
    return PDF_HEADER in content[:PDF_HEADER_WINDOW]


def _is_zip(content: bytes) -> bool:
    return content.startswith(ZIP_LOCAL_HEADER) or zipfile.is_zipfile(io.BytesIO(content))


def _is_tar(content: bytes) -> bool:
    offset, magic = TAR_MAGIC
    return content[offset : offset + len(magic)] == magic


def _stream_format(content: bytes) -> str | None:
    """ "gzip", "bzip2", "xz" or "lzma" for a file of that format, or None."""
    if content.startswith(GZIP_MAGIC):
        return "gzip"
    if BZIP2_MAGIC.match(content):
        return "bzip2"
    if content.startswith(XZ_MAGIC):
        return "xz"
    if LZMA_ALONE.match(content):
        return "lzma"
    return None


def _decompressor(stream_format: str, budget: _Budget) -> _Decompressor:
    """A decompressor for the format. An xz or lzma file names the dictionary its decoder allocates, so that memory is
    held within the same cap too."""
    if stream_format == "gzip":
        return _Zlib(16 + zlib.MAX_WBITS)
    if stream_format == "bzip2":
        return bz2.BZ2Decompressor()
    container = lzma.FORMAT_XZ if stream_format == "xz" else lzma.FORMAT_ALONE
    return lzma.LZMADecompressor(container, memlimit=budget.max_bytes)


def _is_container(content: bytes) -> bool:
    return _is_pdf(content) or _is_zip(content) or _is_tar(content) or _stream_format(content) is not None


def _inflate(content: bytes, budget: _Budget, pieces: list[bytes], depth: int) -> None:
    if depth > MAX_NESTING:
        raise Unscannable(f"holds containers nested more than {MAX_NESTING} deep")
    found: list[bytes] = []
    if _is_pdf(content):
        found += _pdf_streams(content, budget)
    if _is_zip(content):
        found += _zip_members(content, budget)
    if _is_tar(content):
        found += _tar_members(content, budget)
    stream_format = _stream_format(content)
    if stream_format is not None:
        new = functools.partial(_decompressor, stream_format, budget)
        found.append(_decompress(memoryview(content), new, budget, concatenated=True))
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
                if info.compress_type not in ZIP_METHODS:
                    raise Unscannable(f"holds a member compressed with method {info.compress_type}, which isn't read")
                with archive.open(info) as member:
                    members.append(_read(member, budget))
    except Unscannable:
        raise
    except Exception as error:  # untrusted bytes: anything the zip reader raises means the text can't be read whole
        raise Unscannable("is a malformed zip file") from error
    return members


def _tar_members(content: bytes, budget: _Budget) -> list[bytes]:
    """Each regular file's content, read in memory; links, folders and devices hold no text of their own."""
    members = []
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            for info in archive:
                if info.isreg():
                    member = archive.extractfile(info)
                    if member is not None:
                        members.append(_read(member, budget))
    except Unscannable:
        raise
    except Exception as error:  # untrusted bytes: anything the tar reader raises means the text can't be read whole
        raise Unscannable("is a malformed tar file") from error
    return members


def _read(member: IO[bytes], budget: _Budget) -> bytes:
    chunks = []
    while chunk := member.read(CHUNK_BYTES):
        budget.spend(len(chunk))
        chunks.append(chunk)
    return b"".join(chunks)


def _decompress(data: memoryview, new: Callable[[], _Decompressor], budget: _Budget, *, concatenated: bool) -> bytes:
    """The data decompressed in chunks within the budget. With `concatenated`, streams that follow the first are read
    too, and only zero padding may follow the last; without, anything may follow the stream's end."""
    chunks = []
    decompressor, offset, start = new(), 0, 0
    try:
        while True:
            if decompressor.needs_input:
                if offset >= len(data):
                    raise Unscannable("holds a compressed stream that is cut short")
                piece, offset = bytes(data[offset : offset + CHUNK_BYTES]), offset + CHUNK_BYTES
            else:
                piece = b""
            chunk = decompressor.decompress(piece, CHUNK_BYTES)
            budget.spend(len(chunk))
            chunks.append(chunk)
            if not decompressor.eof:
                continue
            start = min(offset, len(data)) - len(decompressor.unused_data)  # where the next stream would begin
            if not concatenated or not any(data[start:]):
                return b"".join(chunks)
            if _stream_format(bytes(data[start : start + 32])) is None:
                raise Unscannable("holds data after its compressed stream")
            decompressor, offset = new(), start
    except lzma.LZMAError as error:
        if "memory" in str(error).lower():
            raise Unscannable(
                f"needs more than {budget.max_bytes} bytes of memory to decompress ([source] max_inflated_bytes)"
            ) from error
        raise Unscannable("holds a compressed stream that doesn't decompress") from error
    except (zlib.error, OSError, EOFError, ValueError) as error:
        raise Unscannable("holds a compressed stream that doesn't decompress") from error


def _pdf_streams(content: bytes, budget: _Budget) -> list[bytes]:
    """Every encoded stream, decoded. The search only moves forward, so a crafted file costs time in proportion to
    its size."""
    streams = []
    position = 0
    while match := PDF_STREAM.search(content, position):
        end = content.find(b"endstream", match.end())
        if end == -1:
            raise Unscannable("is a malformed PDF: a stream has no end")
        dictionary = content[max(position, content.rfind(b"obj", position, match.start())) : match.start()]
        filters = PDF_FILTER.search(dictionary)
        if filters:
            names = PDF_NAME.findall(filters.group(1))
            decoded = _pdf_decode(memoryview(content)[match.end() : end], names, dictionary, budget)
            if decoded is not None:
                streams.append(decoded)
        position = end + len(b"endstream")
    return streams


def _pdf_decode(data: memoryview, names: list[bytes], dictionary: bytes, budget: _Budget) -> bytes | None:
    """The stream's data through its filters, up to an image filter; None when the first is one."""
    decoded: bytes | None = None
    for name in names:
        if name in PDF_IMAGE_FILTERS:
            break
        source = memoryview(decoded) if decoded is not None else data
        if name in (b"FlateDecode", b"Fl"):
            decoded = _undo_predictor(_decompress(source, lambda: _Zlib(zlib.MAX_WBITS), budget, concatenated=False),
                                      dictionary)  # fmt: skip
        elif name in (b"ASCIIHexDecode", b"AHx"):
            decoded = _ascii_hex(bytes(source))
        elif name in (b"ASCII85Decode", b"A85"):
            decoded = _ascii85(bytes(source))
        else:
            raise Unscannable(f"is a PDF with a stream encoded with {name.decode('ascii')}, which isn't read")
        budget.spend(len(decoded))
    return decoded


def _ascii_hex(data: bytes) -> bytes:
    digits = re.sub(rb"\s", b"", data.split(b">", 1)[0])
    try:
        return binascii.unhexlify(digits + b"0" * (len(digits) % 2))
    except binascii.Error as error:
        raise Unscannable("is a malformed PDF: an ASCIIHex stream doesn't decode") from error


def _ascii85(data: bytes) -> bytes:
    text = data.strip().removeprefix(b"<~").split(b"~>", 1)[0]
    try:
        return base64.a85decode(text, ignorechars=b" \t\n\r\x0b\x0c")
    except ValueError as error:
        raise Unscannable("is a malformed PDF: an ASCII85 stream doesn't decode") from error


def _parameter(dictionary: bytes, name: bytes, default: int) -> int:
    found = re.search(rb"/" + name + rb"\s+(\d+)", dictionary)
    return int(found.group(1)) if found else default


def _undo_predictor(data: bytes, dictionary: bytes) -> bytes:
    """Flate's output without the PNG row filters a /Predictor of 10 to 15 put on it; TIFF's (2) isn't read."""
    predictor = _parameter(dictionary, b"Predictor", 1)
    if predictor == 1:
        return data
    if predictor < 10:
        raise Unscannable(f"is a PDF with a stream using predictor {predictor}, which isn't read")
    colors, bits = _parameter(dictionary, b"Colors", 1), _parameter(dictionary, b"BitsPerComponent", 8)
    pixel = max(1, colors * bits // 8)
    width = (_parameter(dictionary, b"Columns", 1) * colors * bits + 7) // 8
    if width == 0 or len(data) % (width + 1):
        raise Unscannable("is a malformed PDF: a predicted stream's rows don't fit its columns")
    output = bytearray()
    above = bytearray(width)
    for start in range(0, len(data), width + 1):
        kind, row = data[start], bytearray(data[start + 1 : start + 1 + width])
        for index in range(width):
            left = row[index - pixel] if index >= pixel else 0
            upper_left = above[index - pixel] if index >= pixel else 0
            if kind == 1:
                row[index] = (row[index] + left) & 0xFF
            elif kind == 2:
                row[index] = (row[index] + above[index]) & 0xFF
            elif kind == 3:
                row[index] = (row[index] + (left + above[index]) // 2) & 0xFF
            elif kind == 4:
                row[index] = (row[index] + _paeth(left, above[index], upper_left)) & 0xFF
            elif kind != 0:
                raise Unscannable("is a malformed PDF: a predicted stream has an unknown row filter")
        output += row
        above = row
    return bytes(output)


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
    return (left, above, upper_left)[distances.index(min(distances))]
