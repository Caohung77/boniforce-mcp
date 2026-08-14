import httpx
import pytest
import respx

from boniforce_mcp.boniforce_client import (
    BoniforceClient,
    BoniforceError,
    _operation_name,
)


def test_operation_names_do_not_expose_resource_ids():
    assert _operation_name("GET", "/v1/reports/report-secret") == "get_report"
    assert (
        _operation_name("GET", "/v1/jobs/job-secret/status")
        == "get_job_status"
    )
    assert (
        _operation_name("GET", "/v1/company/report-secret/details")
        == "get_company_details"
    )


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
async def test_advanced_search_uses_current_endpoint(respx_mock):
    route = respx_mock.get("https://api.boniforce.de/v1/search/advanced").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = BoniforceClient()
    try:
        await client.search_companies_advanced("tok", "ACME")
    finally:
        await client.aclose()
    assert dict(route.calls.last.request.url.params) == {"query": "ACME"}


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
async def test_create_report_accepts_search_result_id(respx_mock):
    route = respx_mock.post("https://api.boniforce.de/v1/reports").mock(
        return_value=httpx.Response(200, json={"job_id": "j1", "report_id": "r1"})
    )
    client = BoniforceClient()
    try:
        await client.create_report("tok", search_result_id="search-1")
    finally:
        await client.aclose()
    assert route.calls.last.request.content == b'{"search_result_id":"search-1"}'


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_wait_for_job_reports_each_poll(respx_mock):
    route = respx_mock.get("https://api.boniforce.de/v1/jobs/j1/status").mock(
        side_effect=[
            httpx.Response(200, json={"status": "running"}),
            httpx.Response(200, json={"status": "completed"}),
        ]
    )
    updates = []

    async def capture_progress(poll_count, elapsed_s, status):
        updates.append((poll_count, elapsed_s, status))

    client = BoniforceClient()
    try:
        result = await client.wait_for_job(
            "tok",
            "j1",
            max_wait_s=1,
            poll_every_s=0,
            on_progress=capture_progress,
        )
    finally:
        await client.aclose()

    assert result == {"status": "completed"}
    assert route.call_count == 2
    assert [(poll_count, status) for poll_count, _, status in updates] == [
        (1, "running"),
        (2, "completed"),
    ]
    assert all(elapsed_s >= 0 for _, elapsed_s, _ in updates)


@pytest.mark.parametrize(
    ("method_name", "path"),
    [
        ("get_financial_data", "/v1/financial_data"),
        ("get_financial_analysis", "/v1/financial_data/analysis"),
    ],
)
@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_direct_financial_endpoints_accept_search_result_id(
    respx_mock, method_name, path
):
    route = respx_mock.get(f"https://api.boniforce.de{path}").mock(
        return_value=httpx.Response(200, json={"report_id": "r1", "financials": []})
    )
    client = BoniforceClient()
    try:
        await getattr(client, method_name)("tok", search_result_id="search-1")
    finally:
        await client.aclose()
    assert dict(route.calls.last.request.url.params) == {
        "search_result_id": "search-1"
    }


@pytest.mark.parametrize(
    ("method_name", "path"),
    [
        ("get_company_details", "/v1/company/r1/details"),
        ("get_company_shareholders", "/v1/company/r1/shareholders"),
        ("get_company_holdings", "/v1/company/r1/holdings"),
    ],
)
@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_company_metadata_endpoints(respx_mock, method_name, path):
    route = respx_mock.get(f"https://api.boniforce.de{path}").mock(
        return_value=httpx.Response(200, json={"report_id": "r1"})
    )
    client = BoniforceClient()
    try:
        await getattr(client, method_name)("tok", "r1")
    finally:
        await client.aclose()
    assert route.called


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
