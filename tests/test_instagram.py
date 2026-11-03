import json
from types import SimpleNamespace

from concertbot import instagram


def test_carousel_flow(monkeypatch):
    calls = []

    def fake(method, url, data=None, params=None, timeout=None):
        p = data or params
        calls.append((method, url.split("/v26.0/")[1], dict(p)))
        path = url.split("/v26.0/")[1]
        if method == "GET":
            body = {"status_code": "FINISHED"}
        elif path.endswith("/media_publish"):
            body = {"id": "MEDIA1"}
        elif p.get("media_type") == "CAROUSEL":
            body = {"id": "PARENT"}
        else:
            body = {"id": f"C{len(calls)}"}
        return SimpleNamespace(status_code=200, content=b"1", json=lambda: body, text=json.dumps(body))

    monkeypatch.setattr(instagram.requests, "request", fake)
    ig = instagram.Instagram("tok", "IG1")
    mid = ig.publish_carousel(["https://x/1.jpg", "https://x/2.jpg"], "cap")
    assert mid == "MEDIA1"
    parent = [c for c in calls if c[2].get("media_type") == "CAROUSEL"][0]
    assert parent[2]["children"].count(",") == 1 and parent[2]["caption"] == "cap"
    assert calls[-1][1] == "IG1/media_publish" and calls[-1][2]["creation_id"] == "PARENT"
