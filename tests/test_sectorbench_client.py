import httpx
import pytest
import respx

from boniforce_mcp.config import get_settings
from boniforce_mcp.sectorbench_client import SectorbenchClient, SectorbenchError


SECTORBENCH_BASE = "https://sectorbench.theaiwhisperer.cloud/api/v1"


@pytest.fixture
def sb_token(monkeypatch):
    monkeypatch.setenv("BF_SECTORBENCH_TOKEN", "sbk_test")
    monkeypatch.setenv("BF_SECTORBENCH_CACHE_TTL", "600")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_get_all_scores_attaches_bearer(respx_mock, sb_token):
    route = respx_mock.get(f"{SECTORBENCH_BASE}/scores").mock(
        return_value=httpx.Response(
            200, json={"fetch_run_id": 7, "fetched_at": "2026-01-01T00:00:00Z", "scores": []}
        )
    )
    client = SectorbenchClient()
    try:
        out = await client.get_all_scores()
    finally:
        await client.aclose()
    assert out["fetch_run_id"] == 7
    assert route.calls.last.request.headers["authorization"] == "Bearer sbk_test"


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_branch_history_passes_months(respx_mock, sb_token):
    route = respx_mock.get(
        f"{SECTORBENCH_BASE}/branches/construction/history"
    ).mock(return_value=httpx.Response(200, json={"branch_key": "construction", "points": []}))
    client = SectorbenchClient()
    try:
        await client.get_branch_history("construction", months=24)
    finally:
        await client.aclose()
    sent = route.calls.last.request
    assert dict(sent.url.params) == {"months": "24"}


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_4xx_raises_sectorbench_error(respx_mock, sb_token):
    respx_mock.get(f"{SECTORBENCH_BASE}/branches/unknown").mock(
        return_value=httpx.Response(404, json={"error": "branch_not_found"})
    )
    client = SectorbenchClient()
    try:
        with pytest.raises(SectorbenchError) as exc:
            await client.get_branch("unknown")
    finally:
        await client.aclose()
    assert exc.value.status == 404
    assert exc.value.body == {"error": "branch_not_found"}


@pytest.mark.asyncio
async def test_request_without_token_raises_503(monkeypatch):
    monkeypatch.setenv("BF_SECTORBENCH_TOKEN", "")
    get_settings.cache_clear()
    client = SectorbenchClient()
    try:
        with pytest.raises(SectorbenchError) as exc:
            await client.get_all_scores()
    finally:
        await client.aclose()
    assert exc.value.status == 503


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_cache_hit_avoids_second_request(respx_mock, sb_token):
    route = respx_mock.get(f"{SECTORBENCH_BASE}/scores").mock(
        return_value=httpx.Response(200, json={"scores": [{"branch_key": "automotive"}]})
    )
    client = SectorbenchClient()
    try:
        first = await client.get_all_scores()
        second = await client.get_all_scores()
    finally:
        await client.aclose()
    assert first == second
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_cache_keys_separate_by_params(respx_mock, sb_token):
    route = respx_mock.get(
        f"{SECTORBENCH_BASE}/branches/automotive/history"
    ).mock(return_value=httpx.Response(200, json={"points": []}))
    client = SectorbenchClient()
    try:
        await client.get_branch_history("automotive", months=12)
        await client.get_branch_history("automotive", months=24)
    finally:
        await client.aclose()
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_indicator_history_endpoint(respx_mock, sb_token):
    route = respx_mock.get(
        f"{SECTORBENCH_BASE}/branches/automotive/indicators/financial.insolvency_cases/history"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "branch_key": "automotive",
                "indicator_key": "financial.insolvency_cases",
                "unit": "count",
                "higher_is_better": False,
                "points": [],
            },
        )
    )
    client = SectorbenchClient()
    try:
        out = await client.get_indicator_history(
            "automotive", "financial.insolvency_cases", months=6
        )
    finally:
        await client.aclose()
    assert out["unit"] == "count"
    assert dict(route.calls.last.request.url.params) == {"months": "6"}
