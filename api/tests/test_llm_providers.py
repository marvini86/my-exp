"""Several providers: routes, catalog, pauses per kind of limit and quality gate."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from schemas.extraction import DocumentType
from services.extraction.llm import LLMDocumentExtractor, LLMExtraction, LLMItem
from services.llm_providers import ProviderCatalog, parse_route
from services.llm_router import LLMRouter, LLMUnavailableError

ALL = {"openrouter", "gemini", "groq", "cloudflare"}
NOW = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)  # 12:00 in Brasília, 07:00 in the Pacific


class HttpError(Exception):
    def __init__(self, status_code: int, message: str = "", retry_after: str | None = None) -> None:
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code
        self.response = SimpleNamespace(headers={"retry-after": retry_after} if retry_after else {})


class FakeChat:
    """Imita ChatOpenAI/ChatOpenRouter: devolve as respostas na ordem; registra chamadas e bind."""

    def __init__(self, model_id: str, script: dict, log: list) -> None:
        self.model_id, self.script, self.log = model_id, script, log
        self.bound: dict = {}

    def bind(self, **kwargs):
        clone = FakeChat(self.model_id, self.script, self.log)
        clone.bound = kwargs
        return clone

    def with_structured_output(self, schema, method, include_raw):
        return self

    def invoke(self, messages):
        self.log.append((self.model_id, self.bound))
        response = self.script[self.model_id].pop(0)
        if isinstance(response, Exception):
            raise response
        content = response.model_dump_json() if isinstance(response, LLMExtraction) else response
        return SimpleNamespace(content=content)


def router(specs, script, *, vision=True, enabled=ALL, clock=None):
    log: list = []
    catalog = ProviderCatalog(specs, vision=vision, enabled_providers=enabled, clock=clock or (lambda: 1000.0))
    return LLMRouter(catalog, lambda mid: FakeChat(mid, script, log), now=lambda: NOW), catalog, log


def receipt(total: float, prices: list[float], printed: int | None = None) -> LLMExtraction:
    return LLMExtraction(
        readable=True,
        document_type=DocumentType.NFCE,
        establishment="SUPERMERCADO EXEMPLO",
        total_amount=total,
        items=[LLMItem(description=f"ITEM {i}", quantity=1, unit_price=p, total_price=p) for i, p in enumerate(prices)],
        items_count_printed=printed,
    )


class TestRoutes:
    def test_parse_route_keeps_colons_in_model(self):
        route = parse_route(" openrouter:nvidia/nemotron-3.5-super-vl:free ")

        assert (route.provider, route.model, route.id) == (
            "openrouter", "nvidia/nemotron-3.5-super-vl:free", "openrouter:nvidia/nemotron-3.5-super-vl:free",
        )

    @pytest.mark.parametrize("spec", ["gpt-4", "acme:model", "groq:", ":model"])
    def test_invalid_routes(self, spec):
        with pytest.raises(ValueError):
            parse_route(spec)

    def test_catalog_skips_providers_without_key_and_flags_json_mode(self):
        catalog = ProviderCatalog(
            ["gemini:g", "groq:q", "openrouter:o:free"], vision=True, enabled_providers={"gemini", "openrouter"}
        )

        assert [(m.id, m.json_mode, m.structured_output) for m in catalog.models] == [
            ("gemini:g", True, False),
            ("openrouter:o:free", False, True),
        ]

    def test_text_catalog_offers_nothing_for_vision(self):
        catalog = ProviderCatalog(["groq:t"], vision=False, enabled_providers=ALL)

        assert catalog.candidates(needs_vision=True) == []
        assert [m.id for m in catalog.candidates(needs_vision=False)] == ["groq:t"]


class TestFailures:
    def test_json_mode_is_requested_from_openai_compatible_providers(self):
        r, _, log = router(["groq:q"], {"groq:q": [receipt(10, [10]).model_dump_json()]})

        result, model = r.structured([], LLMExtraction, needs_vision=True)

        assert model == "groq:q"
        assert log == [("groq:q", {"response_format": {"type": "json_object"}})]

    def test_per_minute_limit_waits_retry_after_and_falls_back(self):
        r, catalog, log = router(
            ["groq:q", "gemini:g"],
            {"groq:q": [HttpError(429, "Rate limit reached ... tokens per minute (TPM)", retry_after="33")],
             "gemini:g": [receipt(10, [10]).model_dump_json()]},
        )

        _, model = r.structured([], LLMExtraction, needs_vision=True)

        assert model == "gemini:g"
        assert catalog.wait_info([catalog.models[0]]) == (pytest.approx(33), False)

    def test_cloudflare_daily_limit_pauses_whole_provider(self):
        r, catalog, log = router(
            ["cloudflare:a", "cloudflare:b", "groq:q"],
            {"cloudflare:a": [HttpError(429, "you have used up your daily free allocation of 10,000 neurons")],
             "cloudflare:b": [], "groq:q": [receipt(10, [10]).model_dump_json()]},
        )

        _, model = r.structured([], LLMExtraction, needs_vision=True)

        assert model == "groq:q"
        assert [m for m, _ in log] == ["cloudflare:a", "groq:q"]  # cloudflare:b was not even tried
        assert not catalog.is_available(catalog.models[1])

    def test_gemini_daily_limit_is_per_model_until_pacific_midnight(self):
        r, catalog, _ = router(
            ["gemini:flash", "gemini:lite"],
            {"gemini:flash": [HttpError(429, "Quota exceeded ... GenerateRequestsPerDayPerProjectPerModel-FreeTier")],
             "gemini:lite": [HttpError(429, "Quota exceeded ... GenerateRequestsPerDayPerProjectPerModel-FreeTier")]},
        )

        with pytest.raises(LLMUnavailableError) as raised:
            r.structured([], LLMExtraction, needs_vision=True)

        # 07:00 Pacific → next midnight = 17 h (+1 min of slack).
        assert raised.value.daily_limit is True
        assert raised.value.delay_seconds == pytest.approx(17 * 3600 + 60, abs=1)

    def test_not_on_free_plan_is_skipped_for_a_day(self):
        r, catalog, _ = router(
            ["cloudflare:glm", "groq:q"],
            {"cloudflare:glm": [HttpError(403, "AiError: Model is not available on the Workers Free plan")],
             "groq:q": [receipt(10, [10]).model_dump_json()]},
        )

        _, model = r.structured([], LLMExtraction, needs_vision=True)

        assert model == "groq:q"
        assert catalog.wait_info([catalog.models[0]])[0] == pytest.approx(24 * 3600)

    def test_short_outage_is_not_daily(self):
        r, _, _ = router(["gemini:g"], {"gemini:g": [HttpError(503, "This model is currently experiencing high demand")]})

        with pytest.raises(LLMUnavailableError) as raised:
            r.text([])

        assert raised.value.daily_limit is False
        assert raised.value.delay_seconds == pytest.approx(120)


class TestQualityGate:
    def extractor(self, script, retries=2):
        r, _, log = router(list(script), script)
        return LLMDocumentExtractor.from_router(r, quality_retries=retries), log

    def test_inconsistent_reading_is_redone_by_next_model(self):
        extractor, log = self.extractor({
            "gemini:lite": [receipt(123.83, [100, 38.19], printed=2)],   # soma 138,19 ≠ 123,83
            "groq:q": [receipt(123.83, [100, 23.83], printed=2)],
        })

        document = extractor.extract(b"\xff\xd8 imagem", "image/jpeg")

        assert document.metadata["model"] == "groq:q"
        assert sum(i.total_price for i in document.items) == Decimal("123.83")
        assert [m for m, _ in log] == ["gemini:lite", "groq:q"]

    def test_missing_items_against_printed_count_triggers_retry(self):
        extractor, log = self.extractor({
            "gemini:lite": [receipt(10, [10], printed=3)],
            "groq:q": [receipt(10, [5, 3, 2], printed=3)],
        })

        assert len(extractor.extract(b"\xff\xd8", "image/jpeg").items) == 3

    def test_when_nobody_agrees_keeps_least_wrong_and_records_models(self):
        extractor, _ = self.extractor({
            "gemini:lite": [receipt(10, [1], printed=3)],       # 2 problemas
            "groq:q": [receipt(10, [9, 2, 1], printed=3)],      # 1 problema (soma 12)
            "cloudflare:c": [receipt(10, [1, 1], printed=3)],   # 2 problemas
        })

        document = extractor.extract(b"\xff\xd8", "image/jpeg")

        assert document.metadata["model"] == "groq:q"
        assert document.metadata["models_tried"] == "gemini:lite, groq:q, cloudflare:c"

    def test_quota_ending_after_a_reading_keeps_it_instead_of_deferring(self):
        extractor, _ = self.extractor({
            "gemini:lite": [receipt(10, [9], printed=1)],
            "groq:q": [HttpError(429, "Rate limit reached ... (RPD)")],
        })

        document = extractor.extract(b"\xff\xd8", "image/jpeg")

        assert document.metadata["model"] == "gemini:lite"

    def test_consistent_first_reading_needs_no_retry(self):
        extractor, log = self.extractor({"gemini:lite": [receipt(10, [4, 6], printed=2)], "groq:q": []})

        extractor.extract(b"\xff\xd8", "image/jpeg")

        assert [m for m, _ in log] == ["gemini:lite"]


def test_groq_413_over_tokens_per_minute_falls_back():
    # Groq answers 413 when the request exceeds what is left of the tokens/min limit.
    r, catalog, _ = router(
        ["groq:t", "cloudflare:t"],
        {"groq:t": [HttpError(413, "Request too large for model ... on tokens per minute (TPM): Limit 8000, Requested 8700")],
         "cloudflare:t": ["texto"]},
        vision=False,
    )

    text, model = r.text([])

    assert (text, model) == ("texto", "cloudflare:t")
    assert catalog.wait_info([catalog.models[0]]) == (pytest.approx(60), False)


def test_groq_gets_smaller_max_tokens():
    from core.config import OpenRouterSettings, ProviderKeys
    from services.llm_providers import chat_model_factory

    build = chat_model_factory(
        OpenRouterSettings(api_key="x"), ProviderKeys(groq_api_key="g", gemini_api_key="m", cloudflare_api_key="c", cloudflare_account_id="a")
    )

    assert build("groq:openai/gpt-oss-120b").max_tokens == 3000
    assert build("gemini:gemini-3.5-flash-lite").max_tokens == 8192
    assert "accounts/a/ai/v1" in str(build("cloudflare:@cf/openai/gpt-oss-120b").openai_api_base)


def test_slow_first_reading_skips_quality_retries(monkeypatch):
    import services.extraction.llm as llm_module

    clock = iter([0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(llm_module.time, "monotonic", lambda: next(clock))
    r, _, log = router(["gemini:lite", "groq:q"], {"gemini:lite": [receipt(10, [9], printed=1)], "groq:q": []})
    extractor = LLMDocumentExtractor.from_router(r, quality_retries=2)

    document = extractor.extract(b"\xff\xd8", "image/jpeg")

    assert document.metadata["model"] == "gemini:lite"
    assert [m for m, _ in log] == ["gemini:lite"]  # over 90 s: does not try another model


def test_discount_is_read_into_metadata():
    from services.extraction.llm import LLMDocumentExtractor as Extractor

    extraction = receipt(112.15, [115.93], printed=1).model_copy(update={"discount_amount": 3.78})

    assert Extractor._to_contract(extraction).metadata["discount"] == "3.78"
