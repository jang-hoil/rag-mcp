"""검색 결과의 원본 페이지를 실제 MCP 이미지 콘텐츠로 돌려주는 기능 테스트."""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest
from mcp.types import ImageContent, TextContent

from rag_mcp.config import Config
from rag_mcp.service import RagService


def _service(tmp_path, fake_backend, *, max_width: int = 320) -> RagService:
    config = Config(
        data_dir=tmp_path,
        qdrant_path=tmp_path / "qdrant",
        page_image_dpi=144,
        page_image_max_width=max_width,
    )
    return RagService(config, backend=fake_backend)


def _write_png(path: Path, *, width: int = 640, height: int = 480) -> None:
    """실제 PDF 없이 PyMuPDF만으로 테스트용 PNG를 만든다."""
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pixmap.clear_with(255)
    pixmap.save(str(path))


def test_get_page_image_uses_cache_without_opening_pdf(
    tmp_path, fake_backend, monkeypatch
):
    """원본 PDF와 매니페스트가 없어도 남은 PNG 캐시만으로 이미지를 반환한다."""
    from rag_mcp import service as service_module

    svc = _service(tmp_path, fake_backend)
    cached = svc.config.pages_dir("공직윤리") / "p11.png"
    _write_png(cached)

    def should_not_render(*args, **kwargs):
        raise AssertionError("캐시가 있으면 PDF 렌더러를 호출하면 안 됩니다")

    monkeypatch.setattr(service_module, "render_pages", should_not_render)

    result = svc.get_page_image("공직윤리", 11)

    assert len(result) == 1
    page = result[0]
    assert page["document_id"] == "공직윤리"
    assert page["page"] == 11
    assert page["cache_hit"] is True
    assert page["path"] == str(cached)
    assert page["mime_type"] == "image/png"
    assert page["width"] == 320
    assert page["height"] == 240
    assert page["image"].startswith(b"\x89PNG")


def test_get_page_image_renders_only_missing_pages_with_requested_dpi(
    tmp_path, fake_backend, monkeypatch
):
    """캐시된 쪽은 보존하고 빠진 쪽만 manifest PDF에서 지정 DPI로 렌더한다."""
    from rag_mcp import service as service_module

    svc = _service(tmp_path, fake_backend, max_width=1600)
    document_id = "예산지침"
    cached = svc.config.pages_dir(document_id) / "p2.png"
    rendered = svc.config.pages_dir(document_id) / "p3.png"
    _write_png(cached, width=800, height=1000)
    source_pdf = tmp_path / "원본.pdf"
    source_pdf.write_text("PDF 렌더는 monkeypatch로 대체", encoding="utf-8")
    svc.manifests.update(
        document_id,
        status="done",
        source_path=str(source_pdf),
    )
    calls = []

    def fake_render(pdf_path, rendered_document_id, config, pages):
        calls.append((Path(pdf_path), rendered_document_id, config.render_dpi, pages))
        _write_png(rendered, width=600, height=900)
        return {3: str(rendered)}

    monkeypatch.setattr(service_module, "render_pages", fake_render)

    result = svc.get_page_image(document_id, [2, 3], dpi=120)

    assert calls == [(source_pdf, document_id, 120, {3})]
    assert [item["page"] for item in result] == [2, 3]
    assert [item["cache_hit"] for item in result] == [True, False]
    assert [item["path"] for item in result] == [str(cached), str(rendered)]


def test_get_page_image_uses_readable_low_dpi_default(
    tmp_path, fake_backend, monkeypatch
):
    """dpi 미지정 시 색인용 200dpi보다 낮은 화면 확인용 144dpi를 사용한다."""
    from rag_mcp import service as service_module

    svc = _service(tmp_path, fake_backend)
    document_id = "문서"
    source_pdf = tmp_path / "문서.pdf"
    source_pdf.write_text("PDF 렌더는 monkeypatch로 대체", encoding="utf-8")
    svc.manifests.update(document_id, status="done", source_path=str(source_pdf))
    captured_dpi = []

    def fake_render(pdf_path, rendered_document_id, config, pages):
        captured_dpi.append(config.render_dpi)
        output = config.pages_dir(rendered_document_id) / "p1.png"
        _write_png(output)
        return {1: str(output)}

    monkeypatch.setattr(service_module, "render_pages", fake_render)

    svc.get_page_image(document_id, 1)

    assert captured_dpi == [144]
    assert svc.config.page_image_dpi < svc.config.render_dpi


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ([], "한 쪽 이상"),
        ([1, 2, 3, 4, 5, 6], "최대 5쪽"),
        ([0], "1 이상의 정수"),
        ([True], "1 이상의 정수"),
    ],
)
def test_get_page_image_rejects_invalid_pages(
    tmp_path, fake_backend, pages, message
):
    svc = _service(tmp_path, fake_backend)

    with pytest.raises(ValueError, match=message):
        svc.get_page_image("문서", pages)


def test_get_page_image_missing_pdf_and_cache_has_actionable_korean_error(
    tmp_path, fake_backend
):
    svc = _service(tmp_path, fake_backend)
    document_id = "원본끊김"
    missing_pdf = tmp_path / "사라진 원본.pdf"
    expected_cache = svc.config.pages_dir(document_id) / "p4.png"
    svc.manifests.update(
        document_id,
        status="done",
        source_path=str(missing_pdf),
    )

    with pytest.raises(ValueError) as exc_info:
        svc.get_page_image(document_id, 4)

    message = str(exc_info.value)
    assert "PDF와 페이지 캐시가 모두 없습니다" in message
    assert str(missing_pdf) in message
    assert str(expected_cache) in message
    assert "PDF를 위 경로에 두거나" in message
    # 파일을 옮긴 경우의 복구 수단까지 알려줘야 실제로 행동할 수 있다(재색인 불필요)
    assert "set_source_path" in message


def test_fastmcp_get_page_image_returns_text_and_actual_image_content(
    tmp_path, monkeypatch
):
    """도구 호출 결과에는 경로 문자열만이 아니라 base64 이미지 블록이 포함된다."""
    from rag_mcp import server

    png_bytes = b"\x89PNG\r\n\x1a\ntest-image"

    class FakeService:
        def get_page_image(self, document_id, pages, dpi=None):
            assert (document_id, pages, dpi) == ("문서", 7, None)
            return [
                {
                    "document_id": document_id,
                    "page": 7,
                    "cache_hit": True,
                    "path": str(tmp_path / "pages" / "p7.png"),
                    "mime_type": "image/png",
                    "width": 320,
                    "height": 480,
                    "image": png_bytes,
                }
            ]

    monkeypatch.setattr(server, "service", lambda: FakeService())

    content = asyncio.run(
        server.mcp.call_tool(
            "get_page_image",
            {"document_id": "문서", "pages": 7},
        )
    )

    assert len(content) == 2
    assert isinstance(content[0], TextContent)
    assert "document_id: 문서" in content[0].text
    assert "page: 7" in content[0].text
    assert "캐시 사용: 예" in content[0].text
    assert "실제 파일 경로:" in content[0].text
    assert isinstance(content[1], ImageContent)
    assert content[1].mimeType == "image/png"
    assert base64.b64decode(content[1].data) == png_bytes
