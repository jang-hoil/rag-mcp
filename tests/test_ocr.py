"""ocr — needs_image PNG 보강 + Fake backend."""
from rag_mcp.config import Config
from rag_mcp.models import Chunk
from rag_mcp.ocr import FakeOcrBackend, augment_chunks_with_ocr


def test_augment_appends_ocr_text_and_refreshes_flags(tmp_path):
    png = tmp_path / "p9.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")  # placeholder; FakeOcrBackend ignores content

    chunks = [
        Chunk(
            chunk_id="d::c0", document_id="d", text="뭉친표",
            needs_image=True, page=9, page_image=str(png), is_table=True,
        ),
        Chunk(
            chunk_id="d::c1", document_id="d", text="본문",
            needs_image=False, page=1,
        ),
    ]
    cfg = Config(ocr_mode="auto")
    backend = FakeOcrBackend({str(png): "201-01 한도 50,000,000원"})

    out, info = augment_chunks_with_ocr(chunks, cfg, backend=backend)
    assert "[OCR]" in out[0].text
    assert "201-01" in out[0].text
    assert out[0].has_code is True
    assert out[0].has_amount is True
    assert out[0].page_image == str(png)  # vision 경로 유지
    assert out[1].text == "본문"
    assert info["ocr_applied"] is True
    assert 9 in info["ocr_pages"]


def test_augment_skips_when_off():
    chunks = [
        Chunk(chunk_id="d::c0", document_id="d", text="t", needs_image=True, page_image="/x.png"),
    ]
    cfg = Config(ocr_mode="off")
    out, info = augment_chunks_with_ocr(chunks, cfg, backend=FakeOcrBackend())
    assert out[0].text == "t"
    assert info["ocr_applied"] is False


def test_augment_deduplicates_page_ocr(tmp_path):
    png = tmp_path / "p5.png"
    png.write_bytes(b"x")
    path = str(png)
    chunks = [
        Chunk(chunk_id="d::c0", document_id="d", text="a", needs_image=True, page=5, page_image=path),
        Chunk(chunk_id="d::c1", document_id="d", text="b", needs_image=True, page=5, page_image=path),
    ]
    backend = FakeOcrBackend({path: "shared ocr"})
    cfg = Config(ocr_mode="auto")
    augment_chunks_with_ocr(chunks, cfg, backend=backend)
    assert backend.calls == 1
