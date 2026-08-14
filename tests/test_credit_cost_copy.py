"""Regression tests for the report-creation credit cost shown to clients."""

from pathlib import Path

import pytest
import yaml


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
    from boniforce_mcp.progress_ui import BONISCORE_PROGRESS_UI_URI

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
    assert "get_credit_intelligence" in names
    assert len(names) == 23
    create_report = next(tool for tool in tools if tool.name == "create_report")
    assert "search_result_id" in create_report.parameters["properties"]
    assert "ctx" not in create_report.parameters["properties"]
    assert not {
        "company_name",
        "register_type",
        "register_number",
        "register_court",
    } & set(create_report.parameters.get("required", []))
    get_job_status = next(tool for tool in tools if tool.name == "get_job_status")
    get_report = next(tool for tool in tools if tool.name == "get_report")
    assert "ctx" not in get_job_status.parameters["properties"]
    assert create_report.parameters["properties"]["wait_seconds"]["default"] == 0
    assert create_report.meta["ui"] == {
        "resourceUri": BONISCORE_PROGRESS_UI_URI,
        "visibility": ["model", "app"],
    }
    assert create_report.meta["openai/outputTemplate"] == BONISCORE_PROGRESS_UI_URI
    assert create_report.meta["openai/widgetAccessible"] is True
    assert create_report.meta["openai/toolInvocation/invoking"] == (
        "Boniscore-Bericht wird erstellt …"
    )
    assert create_report.meta["openai/toolInvocation/invoked"] == (
        "Boniscore-Anfrage abgeschlossen"
    )
    assert get_job_status.meta["ui"]["visibility"] == ["model", "app"]
    assert get_job_status.meta["openai/widgetAccessible"] is True
    assert get_job_status.meta["openai/toolInvocation/invoking"] == (
        "Boniscore-Berechnung läuft …"
    )
    assert get_job_status.meta["openai/toolInvocation/invoked"] == (
        "Boniscore-Status aktualisiert"
    )
    assert get_report.meta["ui"]["visibility"] == ["model", "app"]
    assert get_report.meta["openai/widgetAccessible"] is True


@pytest.mark.asyncio
async def test_mcp_exposes_live_boniscore_progress_app():
    from boniforce_mcp.progress_ui import (
        BONISCORE_PROGRESS_HTML,
        BONISCORE_PROGRESS_UI_URI,
    )
    from boniforce_mcp.server import _make_mcp

    mcp = _make_mcp()
    resources = await mcp.list_resources(run_middleware=False)
    resource = next(item for item in resources if str(item.uri) == BONISCORE_PROGRESS_UI_URI)
    assert resource.mime_type == "text/html;profile=mcp-app"
    assert resource.meta["ui"]["prefersBorder"] is True

    result = await mcp.read_resource(BONISCORE_PROGRESS_UI_URI, run_middleware=False)
    assert len(result.contents) == 1
    assert result.contents[0].mime_type == "text/html;profile=mcp-app"
    assert result.contents[0].content == BONISCORE_PROGRESS_HTML
    assert "Boniscore-Bericht gestartet" in result.contents[0].content
    assert 'bridgeRequest("tools/call"' in result.contents[0].content
    assert 'callTool("get_job_status"' in result.contents[0].content
    assert 'callTool("get_report"' in result.contents[0].content
    assert "innerHTML" not in result.contents[0].content


def test_boniscore_progress_messages_are_german():
    from boniforce_mcp.server import _boniscore_progress_message

    assert _boniscore_progress_message("queued", 4.2) == (
        "Der Boniscore-Bericht ist eingeplant – 4 Sekunden vergangen."
    )
    assert _boniscore_progress_message("running", 12.8) == (
        "Der Boniscore wird berechnet – 13 Sekunden vergangen."
    )
    assert _boniscore_progress_message("completed", 30) == (
        "Der Boniscore-Bericht ist fertig."
    )


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


def test_plugin_release_uses_server_progress_without_duplicate_narration():
    import json

    root = Path(__file__).resolve().parents[1]
    plugin_dir = root / "plugins" / "boniforce-credit-check"
    manifest = json.loads(
        (plugin_dir / ".codex-plugin" / "plugin.json").read_text()
    )
    skill = (
        plugin_dir / "skills" / "boniforce-credit-check" / "SKILL.md"
    ).read_text()

    assert manifest["version"] == "0.3.2"
    assert "MCP server's localized progress notifications" in skill
    assert "live MCP App progress card" in skill
    assert "Do not add assistant-authored polling updates" in skill


def test_plugin_skill_requires_sector_aware_decision_brief():
    root = Path(__file__).resolve().parents[1]
    skill_dir = (
        root
        / "plugins"
        / "boniforce-credit-check"
        / "skills"
        / "boniforce-credit-check"
    )
    skill = (skill_dir / "SKILL.md").read_text()
    sector_context = (skill_dir / "references" / "sector-context.md").read_text()
    output_format = (skill_dir / "references" / "output-format.md").read_text()

    assert "get_report_financial_data" in skill
    assert "get_report_financial_analysis" in skill
    assert "get_credit_intelligence(report_id)` exactly once" in skill
    assert "get_branch_history" in skill
    assert "get_branch_insolvency_history" in skill
    assert "verified" in skill and "inferred" in skill and "unavailable" in skill
    assert "Never average the Boniscore and SectorBench score" in skill
    assert "company-specific weakness" in sector_context.lower()
    assert "Compounded risk" in sector_context
    assert "Financial trajectory" in output_format
    assert "Sector environment" in output_format
    assert "omit the sector line" in output_format
    assert "bis zu 120 Sekunden" in skill

    agent_config = yaml.safe_load((skill_dir / "agents" / "openai.yaml").read_text())
    assert agent_config["dependencies"]["tools"][0]["value"] == "boniforce"
    assert agent_config["policy"]["allow_implicit_invocation"] is True
