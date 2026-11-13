"""The text inside zip members and PDF Flate streams, inflated within a cap for the secret scan (design section 3.4)."""

import io
import tracemalloc
import zipfile
import zlib

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
)
def test_a_malformed_container_is_unscannable(content: bytes) -> None:
    with pytest.raises(Unscannable):
        inflated_text(content, CAP)
