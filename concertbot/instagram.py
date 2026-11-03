from __future__ import annotations

import time

import requests

from .util import log


class InstagramError(RuntimeError):
    pass


class Instagram:
    def __init__(self, token: str, ig_id: str, host: str = "graph.facebook.com", version: str = "v26.0"):
        self.token, self.ig_id = token, ig_id
        self.base = f"https://{host}/{version}"

    def _call(self, method, path, **params):
        params["access_token"] = self.token
        r = requests.request(method, f"{self.base}/{path}", data=params if method == "POST" else None,
                             params=params if method == "GET" else None, timeout=60)
        data = r.json() if r.content else {}
        if r.status_code >= 400 or "error" in data:
            msg = data.get("error", {}).get("message", r.text[:300])
            raise InstagramError(f"{method} {path}: {msg}")
        return data

    def wait_ready(self, container_id: str, tries: int = 10, delay: int = 6):
        for _ in range(tries):
            st = self._call("GET", container_id, fields="status_code").get("status_code")
            if st == "FINISHED":
                return
            if st in ("ERROR", "EXPIRED"):
                raise InstagramError(f"container {container_id} {st}")
            time.sleep(delay)
        raise InstagramError(f"container {container_id} not ready")

    def publishing_quota(self):
        try:
            d = self._call("GET", f"{self.ig_id}/content_publishing_limit", fields="quota_usage,config")
            return d.get("data", [{}])[0]
        except InstagramError as e:
            log.warning("quota check failed: %s", e)
            return {}

    def publish_carousel(self, image_urls: list[str], caption: str) -> str:
        if not 2 <= len(image_urls) <= 20:
            raise InstagramError(f"carousel needs 2-20 images, got {len(image_urls)}")
        children = []
        for u in image_urls:
            cid = self._call("POST", f"{self.ig_id}/media", image_url=u, is_carousel_item="true")["id"]
            children.append(cid)
            log.info("[ig] child %s ← %s", cid, u)
        for c in children:
            self.wait_ready(c)
        parent = self._call("POST", f"{self.ig_id}/media", media_type="CAROUSEL",
                            children=",".join(children), caption=caption)["id"]
        self.wait_ready(parent)
        media_id = self._call("POST", f"{self.ig_id}/media_publish", creation_id=parent)["id"]
        log.info("[ig] published media %s", media_id)
        return media_id

    def permalink(self, media_id: str) -> str:
        try:
            return self._call("GET", media_id, fields="permalink").get("permalink", "")
        except InstagramError:
            return ""


def wait_public(urls: list[str], timeout: int = 600):
    """GitHub Pages 배포가 끝나 이미지가 실제로 열릴 때까지 대기."""
    t0 = time.time()
    pending = list(urls)
    while pending and time.time() - t0 < timeout:
        pending = [u for u in pending if requests.head(u, timeout=15, allow_redirects=True).status_code != 200]
        if pending:
            time.sleep(10)
    if pending:
        raise InstagramError(f"images not public yet: {pending[:2]}")
