def test_matches_specific_wz_before_general_manufacturing():
    from boniforce_mcp.server import _match_sectorbench_wz

    match = _match_sectorbench_wz(
        {"industry_codes": {"WZ2025": [{"code": "29.10"}]}}
    )
    assert match is not None
    assert match["branch_key"] == "automotive"
    assert match["confidence"] == "high"


def test_matches_it_services_from_nested_company_details():
    from boniforce_mcp.server import _match_sectorbench_wz

    match = _match_sectorbench_wz(
        {"company": {"primary_industry_code": {"system": "WZ2025", "code": "62.20"}}}
    )
    assert match is not None
    assert match["branch_key"] == "it_services"


def test_does_not_treat_register_number_as_industry_code():
    from boniforce_mcp.server import _match_sectorbench_wz

    assert _match_sectorbench_wz({"register_number": "68067"}) is None


def test_uncovered_service_division_is_not_forced_to_sector():
    from boniforce_mcp.server import _match_sectorbench_wz

    assert _match_sectorbench_wz({"wz_code": "70.22"}) is None
