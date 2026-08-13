"""Regression tests for the report-creation credit cost shown to clients."""

from pathlib import Path

import pytest


def test_mcp_instructions_show_current_report_cost():
    from boniforce_mcp.server import _make_mcp

    instructions = _make_mcp().instructions
    assert instructions is not None
    assert "COSTS 75 CREDITS" in instructions
    assert "COSTS 100 CREDITS" not in instructions
    assert "COSTS 1 CREDIT" not in instructions


@pytest.mark.asyncio
async def test_mcp_exposes_current_boniforce_tool_surface():
    from boniforce_mcp.server import _make_mcp

    tools = await _make_mcp().list_tools(run_middleware=False)
    names = {tool.name for tool in tools}
    assert {
        "search_companies_advanced",
        "get_financial_data",
        "get_financial_analysis",
        "get_company_details",
        "get_company_shareholders",
        "get_company_holdings",
    } <= names
    assert len(names) == 22
    create_report = next(tool for tool in tools if tool.name == "create_report")
    assert "search_result_id" in create_report.parameters["properties"]
    assert not {
        "company_name",
        "register_type",
        "register_number",
        "register_court",
    } & set(create_report.parameters.get("required", []))


def test_plugin_skill_shows_current_report_cost():
    root = Path(__file__).resolve().parents[1]
    skill = (
        root
        / "plugins"
        / "boniforce-credit-check"
        / "skills"
        / "boniforce-credit-check"
        / "SKILL.md"
    ).read_text()

    assert "spending 75 Boniforce credits" in skill
    assert "Do not spend 75 credits" in skill
    assert "spending 100 Boniforce credits" not in skill
    assert "Do not spend 100 credits" not in skill
    assert "spending one Boniforce credit" not in skill
    assert "Do not spend a credit" not in skill
