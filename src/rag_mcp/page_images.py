"""get_page_image의 페이지 이미지 처리 — 페이지 번호 정규화, 캐시 조회·누락 렌더, PNG 변환.

service.RagService.get_page_image가 쓰기 잠금(mutation_lock.MutationGuard)을 잡고/놓는
흐름만 담당하고, 실제 처리는 이 모듈의 load_page_images가 한다.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .config import Config
from .manifest import ManifestStore, resolve_source_path
from .page_render import render_pages

# 한 번의 MCP 응답에 넣을 수 있는 페이지 수. 이미지 base64 전송량을 제한한다.
_MAX_PAGE_IMAGES = 5
# 화면 확인용 DPI 범위. 너무 낮으면 한글을 읽기 어렵고 너무 높으면 렌더·전송량이 급증한다.
_MIN_PAGE_IMAGE_DPI = 72
_MAX_PAGE_IMAGE_DPI = 300
# 실측(2026-07-30): 이 코퍼스의 지침서는 색이 단순해 PNG가 JPEG보다 오히려 작고 선명하다.
# 같은 페이지에서 PNG 0.21MB 대 JPEG(품질 82) 0.29MB. 최대 페이지도 0.63MB라 압축이 불필요하다.


def _normalize_page_numbers(pages: int | list[int]) -> list[int]:
    """단일 정수나 목록을 중복 없는 페이지 목록으로 정규화한다."""
    if isinstance(pages, bool):
        values = [pages]
    elif isinstance(pages, int):
        values = [pages]
    elif isinstance(pages, list):
        values = pages
    else:
        raise ValueError("pages는 페이지 번호 정수 또는 정수 목록이어야 합니다")

    if not values:
        raise ValueError("pages에는 한 쪽 이상의 페이지 번호가 필요합니다")
    if any(isinstance(page, bool) or not isinstance(page, int) or page < 1 for page in values):
        raise ValueError("페이지 번호는 1 이상의 정수여야 합니다")

    normalized = list(dict.fromkeys(values))
    if len(normalized) > _MAX_PAGE_IMAGES:
        raise ValueError(
            f"페이지 이미지는 한 번에 최대 {_MAX_PAGE_IMAGES}쪽까지 요청할 수 있습니다"
        )
    return normalized


def _page_image_bytes(path: Path, max_width: int) -> tuple[bytes, int, int]:
    """캐시 PNG를 가로폭 제한 PNG 바이트로 변환한다.

    기본 설정에서는 렌더 DPI와 가로폭 상한이 맞춰져 있어 축소가 일어나지 않고
    캐시 PNG가 사실상 그대로 전달된다.
    """
    import fitz

    try:
        # 바이트를 읽고 파일을 닫은 뒤 디코딩해 변환 중에는 캐시 파일을 열어두지 않는다.
        pixmap = fitz.Pixmap(path.read_bytes())
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ValueError(f"페이지 이미지 캐시를 읽을 수 없습니다: {path}") from exc

    if pixmap.colorspace is None or pixmap.colorspace.n not in {1, 3}:
        pixmap = fitz.Pixmap(fitz.csRGB, pixmap)

    if pixmap.width > max_width:
        height = max(1, round(pixmap.height * max_width / pixmap.width))
        pixmap = fitz.Pixmap(pixmap, max_width, height)

    return (
        pixmap.tobytes("png"),
        pixmap.width,
        pixmap.height,
    )


def load_page_images(
    config: Config,
    manifests: ManifestStore,
    document_id: str,
    pages: int | list[int],
    dpi: int | None,
    *,
    render_missing: bool,
) -> list[dict] | None:
    """요청 페이지 PNG를 반환한다. render_missing=False면 캐시가 하나라도 없을 때 None."""
    page_numbers = _normalize_page_numbers(pages)
    effective_dpi = config.page_image_dpi if dpi is None else dpi
    if (
        isinstance(effective_dpi, bool)
        or not isinstance(effective_dpi, int)
        or not _MIN_PAGE_IMAGE_DPI <= effective_dpi <= _MAX_PAGE_IMAGE_DPI
    ):
        raise ValueError(
            f"dpi는 {_MIN_PAGE_IMAGE_DPI} 이상 {_MAX_PAGE_IMAGE_DPI} 이하의 정수여야 합니다"
        )

    pages_dir = config.pages_dir(document_id)
    page_paths = {
        page: pages_dir / f"p{page}.png"
        for page in page_numbers
    }
    cache_hits = {
        page: path.exists()
        for page, path in page_paths.items()
    }
    missing_pages = {
        page
        for page in page_numbers
        if not cache_hits[page]
    }
    if missing_pages and not render_missing:
        return None

    # 캐시가 모두 있으면 매니페스트와 PDF 경로조차 확인하지 않는다. 원본 경로가 끊긴
    # 문서도 이미 렌더된 페이지는 계속 볼 수 있어야 한다.
    if missing_pages:
        manifest = manifests.read(document_id)
        # 옛 WSL 경로(`/mnt/d/...`)로 기록된 문서도 파일이 제자리에 있으면 되살린다.
        source_path = resolve_source_path(
            manifest.source_path if manifest is not None else None
        )
        if source_path is None or not source_path.exists():
            # 해석 결과가 아니라 **매니페스트에 기록된 원문**을 보여준다. 옛 WSL 경로처럼
            # 기록 자체가 낡은 경우가 있어, 사용자가 무엇을 고쳐야 할지 알아야 한다.
            recorded = manifest.source_path if manifest is not None else None
            pdf_display = recorded or "매니페스트에 기록 없음"
            cache_display = ", ".join(
                str(page_paths[page])
                for page in sorted(missing_pages)
            )
            raise ValueError(
                f"문서 '{document_id}'의 요청 페이지에는 PDF와 페이지 캐시가 모두 없습니다. "
                f"매니페스트에 기록된 PDF 경로: {pdf_display}. "
                f"기대한 캐시 경로: {cache_display}. "
                "PDF를 위 경로에 두거나, 파일을 옮겼다면 "
                "set_source_path(document_id, 새 경로)로 경로만 고치면 됩니다(재색인 불필요)."
            )

        # render_pages의 기존 캐시 경로 규약을 그대로 쓰되 이번 요청의 화면 확인 DPI만
        # 임시 Config에 주입한다. 서비스의 색인용 render_dpi 설정은 변경하지 않는다.
        render_config = replace(config, render_dpi=effective_dpi)
        rendered_paths = render_pages(
            source_path,
            document_id,
            render_config,
            missing_pages,
        )
        for page in missing_pages:
            rendered_path = Path(rendered_paths.get(page, page_paths[page]))
            if rendered_path.exists():
                page_paths[page] = rendered_path

        unavailable_pages = [
            page
            for page in sorted(missing_pages)
            if not page_paths[page].exists()
        ]
        if unavailable_pages:
            cache_display = ", ".join(
                str(page_paths[page])
                for page in unavailable_pages
            )
            raise ValueError(
                f"PDF에서 요청한 페이지를 렌더하지 못했습니다: {unavailable_pages}. "
                f"PDF 경로: {source_path}. 기대한 캐시 경로: {cache_display}."
            )

    response = []
    for page in page_numbers:
        path = page_paths[page]
        try:
            image, width, height = _page_image_bytes(
                path,
                config.page_image_max_width,
            )
        except FileNotFoundError:
            if not render_missing:
                return None
            raise
        response.append(
            {
                "document_id": document_id,
                "page": page,
                "cache_hit": cache_hits[page],
                "path": str(path),
                "mime_type": "image/png",
                "width": width,
                "height": height,
                "image": image,
            }
        )
    return response
