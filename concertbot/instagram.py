from __future__ import annotations

import time

import requests

from .util import log


class InstagramError(RuntimeError):
    pass


class PublishUncertain(InstagramError):
    """media_publish 가 오류를 돌려줬지만 실제로 게시됐는지 확인하지 못함 (재시도하면 중복 게시 위험)."""


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
        try:
            media_id = self._call("POST", f"{self.ig_id}/media_publish", creation_id=parent)["id"]
        except InstagramError as e:
            # 'Application request limit reached' 등: 오류가 나도 실제로는 게시된 경우가 있다 → 최근 게시물에서 확인
            time.sleep(20)
            found = self.find_recent(caption)
            if found:
                log.warning("[ig] media_publish 오류였지만 실제로 게시됨: %s (%s)", found, e)
                return found
            raise PublishUncertain(str(e)) from e
        log.info("[ig] published media %s", media_id)
        return media_id

    def find_recent(self, caption: str, minutes: int = 20) -> str | None:
        """최근 게시물 중 캡션 앞부분이 같고 방금 올라간 것이 있으면 그 id."""
        from datetime import datetime, timedelta, timezone
        try:
            items = self._call("GET", f"{self.ig_id}/media", fields="id,caption,timestamp", limit=5).get("data", [])
        except InstagramError:
            return None
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        head = (caption or "").strip()[:60]
        for it in items:
            try:
                ts = datetime.strptime(it.get("timestamp", ""), "%Y-%m-%dT%H:%M:%S%z")
            except ValueError:
                continue
            if ts >= since and (it.get("caption") or "").strip()[:60] == head:
                return it["id"]
        return None

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


def discover_account(token: str, host: str = "graph.facebook.com", version: str = "v26.0") -> tuple[str, str]:
    """토큰으로 관리하는 페이지에 연결된 인스타 비즈니스 계정을 찾는다 → (ig_user_id, username)."""
    r = requests.get(f"https://{host}/{version}/me/accounts", params={
        "fields": "name,instagram_business_account{id,username}", "access_token": token}, timeout=30)
    data = r.json()
    if "error" in data:
        raise InstagramError(f"me/accounts: {data['error'].get('message')}")
    for page in data.get("data", []):
        ig = page.get("instagram_business_account")
        if ig:
            return ig["id"], ig.get("username", "")
    raise InstagramError("토큰에 연결된 페이지 중 인스타 비즈니스 계정이 연결된 곳이 없습니다 "
                         f"(페이지 {len(data.get('data', []))}개 확인)")
