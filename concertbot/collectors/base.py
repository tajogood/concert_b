class SkipSource(Exception):
    """필수 키 누락 등으로 소스를 건너뛸 때."""


def need(secrets: dict, *keys: str) -> list[str]:
    missing = [k for k in keys if not secrets.get(k)]
    if missing:
        raise SkipSource("missing secrets: " + ", ".join(missing))
    return [secrets[k] for k in keys]
