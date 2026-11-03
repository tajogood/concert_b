from __future__ import annotations

import os

from .util import log


def capture_slides(html_paths: list[str], out_dir: str, quality: int = 92) -> list[str]:
    from playwright.sync_api import sync_playwright

    os.makedirs(out_dir, exist_ok=True)
    out = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        for hp in html_paths:
            page.goto("file://" + os.path.abspath(hp), wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)  # 포스터 이미지
            except Exception:  # noqa: BLE001
                log.warning("poster images slow on %s", hp)
            page.evaluate("document.fonts.ready")
            jpg = os.path.join(out_dir, os.path.basename(hp).replace(".html", ".jpg"))
            page.screenshot(path=jpg, type="jpeg", quality=quality, full_page=False)
            out.append(jpg)
        b.close()
    log.info("[capture] %d slides → %s", len(out), out_dir)
    return out


def capture_page(index_html: str, out_path: str):
    """모바일 페이지 미리보기(아이폰 크기) — 확인용/OG 이미지용."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
        page.goto("file://" + os.path.abspath(index_html), wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        page.evaluate("document.fonts.ready")
        page.screenshot(path=out_path, type="jpeg", quality=88)
        b.close()
    return out_path
