"""The text stored compressed (zip and tar members, gzip, bzip2 and xz streams, PDF streams), decoded within a cap
for the secret scan (design section 3.4)."""

import base64
import bz2
import gzip
import io
import lzma
import tarfile
import tracemalloc
import zipfile
import zlib
from collections.abc import Callable

import pytest

from codetrail.repo.compressed import Unscannable, inflated_text

CAP = 1_000_000


def make_zip(members: dict[str, bytes], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=method) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def make_pdf(*streams: tuple[bytes, bytes]) -> bytes:
    """A PDF whose objects hold each stream with its dictionary's filter."""
    body = b"%PDF-1.7\n"
    for number, (filter_name, data) in enumerate(streams, start=1):
        body += b"%d 0 obj\n<< /Length %d /Filter %s >>\nstream\n" % (number, len(data), filter_name)
        body += data + b"\nendstream\nendobj\n"
    return body + b"trailer\n<< >>\n%%EOF\n"


def test_a_plain_file_is_not_a_container() -> None:
    assert inflated_text(b"print('hi')\n", CAP) is None
    assert inflated_text(b"", CAP) is None


def test_zip_members_are_inflated() -> None:
    document = make_zip({"word/document.xml": b"<w:t>TOKEN = abc123</w:t>", "README": b"plain"})
    text = inflated_text(document, CAP)
    assert text is not None
    assert b"TOKEN = abc123" in text and b"plain" in text


def test_pdf_flate_streams_are_inflated_and_other_filters_left_alone() -> None:
    pdf = make_pdf((b"/FlateDecode", zlib.compress(b"BT (TOKEN = abc123) Tj ET")), (b"/DCTDecode", b"\xff\xd8jpeg"))
    text = inflated_text(pdf, CAP)
    assert text is not None
    assert b"TOKEN = abc123" in text and b"jpeg" not in text


def test_a_filter_array_starting_with_flate_is_inflated() -> None:
    pdf = make_pdf((b"[/FlateDecode /DCTDecode]", zlib.compress(b"first stage")))
    assert inflated_text(pdf, CAP) == b"first stage"


def test_containers_inside_containers_are_inflated_too() -> None:
    inner = make_zip({"secret.txt": b"TOKEN = nested"})
    outer = make_zip({"lib/inner.jar": inner})
    text = inflated_text(outer, CAP)
    assert text is not None and b"TOKEN = nested" in text


def test_containers_nested_too_deeply_are_unscannable() -> None:
    archive = make_zip({"x.txt": b"x"})
    for depth in range(5):
        archive = make_zip({f"level{depth}.zip": archive})
    with pytest.raises(Unscannable, match="nested"):
        inflated_text(archive, CAP)


def test_a_zip_bomb_is_unscannable_without_filling_memory() -> None:
    bomb = make_zip({"zeros.bin": bytes(50 * CAP)})
    assert len(bomb) < CAP // 10
    tracemalloc.start()
    try:
        with pytest.raises(Unscannable, match="more than 1000000 bytes"):
            inflated_text(bomb, CAP)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * CAP


def test_a_pdf_bomb_is_unscannable_without_filling_memory() -> None:
    pdf = make_pdf((b"/FlateDecode", zlib.compress(bytes(50 * CAP), 9)))
    tracemalloc.start()
    try:
        with pytest.raises(Unscannable, match="more than 1000000 bytes"):
            inflated_text(pdf, CAP)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * CAP


def test_the_cap_counts_every_member_together() -> None:
    archive = make_zip({"a.txt": b"a" * (CAP // 2 + 1), "b.txt": b"b" * (CAP // 2 + 1)})
    with pytest.raises(Unscannable):
        inflated_text(archive, CAP)


def test_an_encrypted_member_is_unscannable() -> None:
    archive = bytearray(make_zip({"secret.txt": b"TOKEN = abc123"}, zipfile.ZIP_STORED))
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):  # the flags in both headers
        start = archive.index(signature)
        archive[start + offset] |= 0x01
    with pytest.raises(Unscannable, match="encrypted"):
        inflated_text(bytes(archive), CAP)


def test_a_method_codetrail_doesnt_inflate_is_unscannable() -> None:
    with pytest.raises(Unscannable, match="method"):
        inflated_text(make_zip({"a.txt": b"text"}, zipfile.ZIP_BZIP2), CAP)


@pytest.mark.parametrize(
    "content",
    [
        make_zip({"a.txt": b"text " * 100})[:60],  # cut short: no central directory
        make_zip({"a.txt": b"text " * 100}, zipfile.ZIP_STORED).replace(
            b"text", b"TEXT"
        ),  # stored data no longer matches its CRC
        make_pdf((b"/FlateDecode", zlib.compress(bytes(range(256)) * 4)[:100])),  # a stream cut short
        make_pdf((b"/FlateDecode", b"not deflate data")),
        b"%PDF-1.7\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\nx",  # no endstream
    ],
    # A zip carries the time it was built, so test ids drawn from the bytes would differ between test workers.
    ids=["zip-cut-short", "zip-bad-crc", "pdf-stream-cut-short", "pdf-not-deflate", "pdf-no-endstream"],
)
def test_a_malformed_container_is_unscannable(content: bytes) -> None:
    with pytest.raises(Unscannable):
        inflated_text(content, CAP)


def make_tar(members: dict[str, bytes], links: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)
    return buffer.getvalue()


SMALL_DICTIONARY = 64 * 1024  # the decoder's memory counts within the cap, and the tests' cap is 1 MB
STREAMS: dict[str, Callable[[bytes], bytes]] = {
    "gzip": gzip.compress,
    "bzip2": bz2.compress,
    "xz": lambda data: lzma.compress(data, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": SMALL_DICTIONARY}]),
    "lzma": lambda data: lzma.compress(
        data, format=lzma.FORMAT_ALONE, filters=[{"id": lzma.FILTER_LZMA1, "dict_size": SMALL_DICTIONARY}]
    ),
}


@pytest.mark.parametrize("compress", STREAMS.values(), ids=STREAMS.keys())
def test_compressed_streams_are_decompressed(compress: Callable[[bytes], bytes]) -> None:
    assert inflated_text(compress(b"TOKEN = abc123"), CAP) == b"TOKEN = abc123"


@pytest.mark.parametrize("compress", [STREAMS["gzip"], STREAMS["bzip2"], STREAMS["xz"]], ids=["gzip", "bzip2", "xz"])
def test_every_concatenated_stream_is_read(compress: Callable[[bytes], bytes]) -> None:
    assert inflated_text(compress(b"first ") + compress(b"TOKEN = abc123"), CAP) == b"first TOKEN = abc123"


@pytest.mark.parametrize("compress", STREAMS.values(), ids=STREAMS.keys())
def test_a_stream_bomb_is_unscannable_without_filling_memory(compress: Callable[[bytes], bytes]) -> None:
    bomb = compress(bytes(50 * CAP))
    tracemalloc.start()
    try:
        with pytest.raises(Unscannable, match="more than 1000000 bytes"):
            inflated_text(bomb, CAP)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * CAP


@pytest.mark.parametrize("compress", STREAMS.values(), ids=STREAMS.keys())
def test_a_stream_cut_short_or_followed_by_other_data_is_unscannable(compress: Callable[[bytes], bytes]) -> None:
    whole = compress(bytes(range(256)) * 40)
    with pytest.raises(Unscannable):
        inflated_text(whole[: len(whole) // 2], CAP)
    with pytest.raises(Unscannable):
        inflated_text(whole + b"not another stream", CAP)


def test_tar_members_are_read_and_links_skipped() -> None:
    archive = make_tar({"config/app.ini": b"TOKEN = abc123", "README": b"plain"}, links={"etc": "/etc/passwd"})
    text = inflated_text(archive, CAP)
    assert text is not None
    assert b"TOKEN = abc123" in text and b"plain" in text and b"/etc/passwd" not in text


def test_a_compressed_tar_is_read_through() -> None:
    text = inflated_text(gzip.compress(make_tar({"deploy/key.txt": b"TOKEN = abc123"})), CAP)
    assert text is not None and b"TOKEN = abc123" in text


def test_a_tar_over_the_cap_is_unscannable() -> None:
    with pytest.raises(Unscannable, match="more than 1000000 bytes"):
        inflated_text(make_tar({"a.bin": bytes(CAP // 2 + 1), "b.bin": bytes(CAP // 2 + 1)}), CAP)


def test_a_malformed_tar_is_unscannable() -> None:
    archive = bytearray(make_tar({"a.txt": b"text"}))
    archive[148:156] = b"garbage!"  # the header's checksum
    with pytest.raises(Unscannable):
        inflated_text(bytes(archive), CAP)


def test_a_pdf_filter_chain_is_decoded_in_order() -> None:
    text = b"BT (TOKEN = abc123) Tj ET"
    hex_then_flate = base64.b16encode(zlib.compress(text)) + b">"
    a85 = base64.a85encode(text) + b"~>"
    pdf = make_pdf((b"[/AHx /FlateDecode]", hex_then_flate), (b"/ASCII85Decode", a85))
    assert inflated_text(pdf, CAP) == text + b"\n" + text


def test_a_pdf_png_predictor_is_undone() -> None:
    rows = [b"TOKE", b"N = ", b"abc1", b"23  "]
    previous = bytes(4)
    encoded = b""
    for row in rows:  # PNG's Up filter: each byte minus the one above it
        encoded += b"\x02" + bytes((byte - above) % 256 for byte, above in zip(row, previous, strict=True))
        previous = row
    pdf = make_pdf((b"/FlateDecode /DecodeParms << /Predictor 12 /Columns 4 >>", zlib.compress(encoded)))
    assert inflated_text(pdf, CAP) == b"".join(rows)


@pytest.mark.parametrize(
    "filters",
    [b"/LZWDecode", b"[/FlateDecode /RunLengthDecode]", b"/Crypt", b"/FlateDecode /DecodeParms << /Predictor 2 >>"],
)
def test_a_pdf_stream_that_cant_be_fully_decoded_is_unscannable(filters: bytes) -> None:
    with pytest.raises(Unscannable):
        inflated_text(make_pdf((filters, zlib.compress(b"text"))), CAP)


def test_an_lzma_file_naming_a_huge_dictionary_is_unscannable() -> None:
    """The decoder allocates the dictionary the header names; that memory is held within the cap too."""
    stream = bytearray(STREAMS["lzma"](b"text"))
    stream[1:5] = (1536 * 1024 * 1024).to_bytes(4, "little")
    with pytest.raises(Unscannable, match="memory"):
        inflated_text(bytes(stream), CAP)
