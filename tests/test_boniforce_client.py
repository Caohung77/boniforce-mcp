import httpx
import pytest
import respx

from boniforce_mcp.boniforce_client import BoniforceClient, BoniforceError


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_search_companies_passes_bearer(respx_mock):
    route = respx_mock.get("https://api.boniforce.de/v1/search").mock(
        return_value=httpx.Response(200, json={"results": [{"name": "ACME"}]})
    )
    client = BoniforceClient()
    try:
        out = await client.search_companies("tok-123", "ACME")
    finally:
        await client.aclose()
    assert out == {"results": [{"name": "ACME"}]}
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer tok-123"
    assert dict(sent.url.params) == {"query": "ACME"}


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_create_report_posts_json(respx_mock):
    route = respx_mock.post("https://api.boniforce.de/v1/reports").mock(
        return_value=httpx.Response(202, json={"job_id": "j1"})
    )
    client = BoniforceClient()
    try:
        out = await client.create_report(
            "tok",
            company_name="ACME GmbH",
            register_type="HRB",
            register_number="12345",
            register_court="Berlin",
        )
    finally:
        await client.aclose()
    assert out == {"job_id": "j1"}
    body = route.calls.last.request.read()
    assert b"ACME GmbH" in body
    assert b"HRB" in body


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_4xx_raises_boniforce_error(respx_mock):
    respx_mock.get("https://api.boniforce.de/v1/reports").mock(
        return_value=httpx.Response(401, json={"detail": "invalid token"})
    )
    client = BoniforceClient()
    try:
        with pytest.raises(BoniforceError) as exc:
            await client.list_reports("bad")
    finally:
        await client.aclose()
    assert exc.value.status == 401
    assert exc.value.body == {"detail": "invalid token"}
