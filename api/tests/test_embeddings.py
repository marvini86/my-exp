import json

import httpx
import pytest

from services.embeddings import EmbeddingRouter, EmbeddingUnavailableError, OpenRouterEmbeddings, vector_name


def client_returning(handler) -> httpx.Client:
    return httpx.Client(base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))


def ok_handler(dim: int = 3, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if calls is not None:
            calls.append(body)
        # Returns out of order to test sorting by "index".
        data = [{"index": i, "embedding": [float(i)] * dim} for i in range(len(body["input"]))][::-1]
        return httpx.Response(200, json={"data": data})

    return handler


def model(handler, name="liquid/lfm-2.5-embedding-350m:free", **kwargs) -> OpenRouterEmbeddings:
    return OpenRouterEmbeddings(name, "key", client=client_returning(handler), **kwargs)


class TestVectorName:
    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("liquid/lfm-2.5-embedding-350m:free", "lfm_2_5_embedding_350m"),
            ("nvidia/nemotron-3.5-embed-1b:free", "nemotron_3_5_embed_1b"),
            ("baai/bge-m3", "bge_m3"),
        ],
    )
    def test_slug(self, model_id, expected):
        assert vector_name(model_id) == expected


class TestOpenRouterEmbeddings:
    def test_orders_by_index_and_sends_privacy_policy(self):
        calls = []
        embeddings = model(ok_handler(calls=calls), data_collection="allow")

        vectors = embeddings.embed_documents(["a", "b", "c"])

        assert vectors == [[0.0] * 3, [1.0] * 3, [2.0] * 3]
        assert calls[0]["provider"] == {"data_collection": "allow"}
        assert calls[0]["model"] == "liquid/lfm-2.5-embedding-350m:free"

    def test_batches_requests(self):
        calls = []
        embeddings = model(ok_handler(calls=calls), batch_size=2)

        assert len(embeddings.embed_documents(["a", "b", "c", "d", "e"])) == 5
        assert [len(c["input"]) for c in calls] == [2, 2, 1]

    def test_embed_query(self):
        assert model(ok_handler(dim=4)).embed_query("x") == [0.0] * 4

    @pytest.mark.parametrize("status", [404, 402, 500, 502])
    def test_http_errors_are_unavailable(self, status):
        embeddings = model(lambda r: httpx.Response(status, json={"error": {"message": "x"}}))

        with pytest.raises(EmbeddingUnavailableError, match=f"HTTP {status}"):
            embeddings.embed_query("x")

    def test_rate_limit_short_cooldown(self):
        embeddings = model(lambda r: httpx.Response(429, json={"error": {"message": "slow down"}}))

        with pytest.raises(EmbeddingUnavailableError) as raised:
            embeddings.embed_query("x")
        assert raised.value.cooldown_seconds == 60

    def test_daily_limit_cooldown_until_midnight(self):
        embeddings = model(lambda r: httpx.Response(429, text="Rate limit exceeded: free-models-per-day"))

        with pytest.raises(EmbeddingUnavailableError, match="daily") as raised:
            embeddings.embed_query("x")
        assert 60 < raised.value.cooldown_seconds <= 24 * 3600 + 60

    def test_network_error_is_unavailable(self):
        def boom(request):
            raise httpx.ConnectError("down")

        with pytest.raises(EmbeddingUnavailableError, match="ConnectError"):
            model(boom).embed_query("x")

    def test_wrong_vector_count_is_unavailable(self):
        embeddings = model(lambda r: httpx.Response(200, json={"data": []}))

        with pytest.raises(EmbeddingUnavailableError, match="expected 1"):
            embeddings.embed_query("x")


class FakeEmbeddings:
    def __init__(self, name: str, dim: int, failures: list | None = None) -> None:
        self.vector_name = name
        self.dim = dim
        self.failures = failures or []
        self.calls = 0

    def _maybe_fail(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)

    def embed_documents(self, texts):
        self._maybe_fail()
        return [[1.0] * self.dim for _ in texts]

    def embed_query(self, text):
        self._maybe_fail()
        return [1.0] * self.dim


class TestEmbeddingRouter:
    def test_documents_use_all_available_models(self):
        router = EmbeddingRouter([FakeEmbeddings("a", 3), FakeEmbeddings("b", 5)])

        vectors = router.embed_documents(["x", "y"])

        assert {name: len(v[0]) for name, v in vectors.items()} == {"a": 3, "b": 5}

    def test_documents_skip_failed_model(self):
        router = EmbeddingRouter([FakeEmbeddings("a", 3, [EmbeddingUnavailableError("down")]), FakeEmbeddings("b", 5)])

        assert set(router.embed_documents(["x"])) == {"b"}

    def test_query_falls_back_and_respects_cooldown(self):
        clock = [0.0]
        first = FakeEmbeddings("a", 3, [EmbeddingUnavailableError("down", cooldown_seconds=60)])
        second = FakeEmbeddings("b", 5)
        router = EmbeddingRouter([first, second], clock=lambda: clock[0])

        assert router.embed_query("x")[0] == "b"
        assert router.embed_query("x")[0] == "b"  # "a" cooling down: not even called
        assert first.calls == 1

        clock[0] = 61
        assert router.embed_query("x")[0] == "a"

    def test_all_models_failing_raises_with_shortest_cooldown(self):
        router = EmbeddingRouter([
            FakeEmbeddings("a", 3, [EmbeddingUnavailableError("a down", cooldown_seconds=300)]),
            FakeEmbeddings("b", 3, [EmbeddingUnavailableError("b down", cooldown_seconds=90)]),
        ])

        with pytest.raises(EmbeddingUnavailableError, match="All embedding models unavailable") as raised:
            router.embed_documents(["x"])
        assert raised.value.cooldown_seconds == 90

    def test_dimensions(self):
        router = EmbeddingRouter([FakeEmbeddings("a", 3), FakeEmbeddings("b", 7)])

        assert router.dimensions() == {"a": 3, "b": 7}

    def test_requires_a_model(self):
        with pytest.raises(ValueError):
            EmbeddingRouter([])


class TestProviders:
    def test_specs_with_and_without_provider(self):
        from services.embeddings import _split_spec

        assert _split_spec("gemini:gemini-embedding-001") == ("gemini", "gemini-embedding-001")
        assert _split_spec("cloudflare:@cf/baai/bge-m3") == ("cloudflare", "@cf/baai/bge-m3")
        assert _split_spec("liquid/lfm-2.5-embedding-350m:free") == ("openrouter", "liquid/lfm-2.5-embedding-350m:free")

    def test_routes_without_key_are_skipped(self):
        from core.config import EmbeddingSettings, OpenRouterSettings, ProviderKeys
        from services.embeddings import EmbeddingRouter

        router = EmbeddingRouter.from_settings(
            OpenRouterSettings(api_key="x"),
            EmbeddingSettings(models=["gemini:gemini-embedding-001", "cloudflare:@cf/baai/bge-m3"]),
            ProviderKeys(gemini_api_key="g", cloudflare_api_key=None, cloudflare_account_id=None),
        )

        assert router.vector_names == ["gemini_embedding_001"]

    def _client(self, handler):
        import httpx

        return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test")

    def test_gemini_missing_index_and_dimensions(self):
        import httpx

        from services.embeddings import OpenAICompatibleEmbeddings

        seen = {}

        def handler(request):
            seen["body"] = request.read()
            # O Gemini omite "index" no primeiro item.
            return httpx.Response(200, json={"data": [{"embedding": [1.0]}, {"index": 1, "embedding": [2.0]}]})

        model = OpenAICompatibleEmbeddings(
            "gemini-embedding-001", "k", provider="gemini", extra_body={"dimensions": 768}, client=self._client(handler)
        )

        assert model.embed_documents(["a", "b"]) == [[1.0], [2.0]]
        assert b'"dimensions":768' in seen["body"].replace(b" ", b"")

    def test_gemini_daily_limit_waits_until_reset(self):
        import httpx

        from services.embeddings import EmbeddingUnavailableError, OpenAICompatibleEmbeddings

        def handler(request):
            return httpx.Response(429, text="Quota exceeded ... EmbedContentRequestsPerDayPerProjectPerModel-FreeTier")

        model = OpenAICompatibleEmbeddings("gemini-embedding-001", "k", provider="gemini", client=self._client(handler))

        with pytest.raises(EmbeddingUnavailableError) as raised:
            model.embed_query("x")

        assert "daily" in str(raised.value)
        assert raised.value.cooldown_seconds > 3600
