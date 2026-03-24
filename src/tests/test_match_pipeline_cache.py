from headless.pipeline.match_pipeline import MatchPipeline


class _DummyClient:
    def __init__(self):
        self.calls: list[str] = []

    def fetch_text(self, url: str) -> str:
        self.calls.append(url)
        return f"html::{url}"


def test_match_pipeline_cache_reuses_and_evicts():
    client = _DummyClient()
    pipeline = MatchPipeline(
        client=client,
        cache_enabled=True,
        max_cache_entries=2,
    )

    pages = pipeline._fetch_named_pages([("a", "u1"), ("b", "u2")])
    assert pages["a"] == "html::u1"
    assert pages["b"] == "html::u2"
    assert client.calls == ["u1", "u2"]

    pages = pipeline._fetch_named_pages([("c", "u1")])
    assert pages["c"] == "html::u1"
    assert client.calls == ["u1", "u2"]

    pipeline._fetch_named_pages([("d", "u3")])
    pipeline._fetch_named_pages([("e", "u2")])
    assert client.calls == ["u1", "u2", "u3", "u2"]

    stats = pipeline.cache_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 4
    assert stats["evictions"] == 2
    assert stats["entries"] == 2


def test_match_pipeline_reset_cache_clears_stats():
    client = _DummyClient()
    pipeline = MatchPipeline(
        client=client,
        cache_enabled=True,
        max_cache_entries=2,
    )

    pipeline._fetch_named_pages([("a", "u1")])
    assert pipeline.cache_stats()["entries"] == 1

    pipeline.reset_cache()
    stats = pipeline.cache_stats()
    assert stats["entries"] == 0
    assert stats["hits"] == 0
    assert stats["misses"] == 0
    assert stats["evictions"] == 0
