"""The composite 「材料/新材料」 group and its precision rules."""

from packages.config import OFFERBIU_INDUSTRY_GROUP_OPTIONS
from packages.industry_scope import (
    MATERIAL_INDUSTRY_CODES,
    expand_industry_codes,
    matches_industry_scope,
    matches_role_scope,
)


def _item(codes, positions, company="某公司"):
    return {
        "companyName": company,
        "industryGroupCodes": codes,
        "positionsText": positions,
    }


def test_materials_is_offered_as_a_group():
    codes = [code for code, _label in OFFERBIU_INDUSTRY_GROUP_OPTIONS]
    assert "materials" in codes
    # 源站的 14 个真实分组一个都没少
    assert len(codes) == 15


def test_materials_covers_the_four_source_industries():
    assert set(MATERIAL_INDUSTRY_CODES) == {
        "energy-chemical-environment",
        "manufacturing-equipment",
        "semiconductor-hardware",
        "auto-transport-equipment",
    }


def test_expand_keeps_order_and_drops_duplicates():
    assert expand_industry_codes(["materials", "internet-tech"]) == (
        *MATERIAL_INDUSTRY_CODES,
        "internet-tech",
    )
    assert expand_industry_codes(["internet-tech", "materials"])[0] == "internet-tech"


def test_materials_matches_a_materials_position():
    assert matches_industry_scope(_item(["energy-chemical-environment"], "材料研发工程师"), ["materials"])


def test_materials_rejects_other_positions_in_the_same_industry():
    """电池厂也招 HR，那不算材料岗。"""

    assert not matches_industry_scope(
        _item(["energy-chemical-environment"], "人力资源专员、财务管理"), ["materials"]
    )


def test_materials_only_looks_at_selected_industries():
    """互联网公司的「材料」字样不该被算进来。"""

    assert not matches_industry_scope(
        _item(["internet-tech"], "材料工程师"), ["materials"]
    )


def test_ordinary_group_still_matches_any_position_in_it():
    assert matches_industry_scope(_item(["internet-tech"], "人力资源专员"), ["internet-tech"])


def test_automation_engineer_is_not_chemical_engineering():
    """「自动化工程师」里含「化工」二字，是子串误判，必须排除。"""

    assert not matches_industry_scope(
        _item(["energy-chemical-environment"], "电气工程师、自动化工程师、结构工程师"),
        ["materials"],
    )


def test_chemical_engineering_still_matches_when_actually_present():
    assert matches_industry_scope(
        _item(["energy-chemical-environment"], "自动化工程师、化工工艺工程师"), ["materials"]
    )


def test_dynamics_and_thermodynamics_are_not_materials_mechanics():
    assert not matches_industry_scope(
        _item(["auto-transport-equipment"], "动力学仿真工程师"), ["materials"]
    )
    assert not matches_industry_scope(
        _item(["energy-chemical-environment"], "热力学工程师"), ["materials"]
    )


def test_new_energy_vehicle_sales_is_not_a_materials_role():
    assert not matches_industry_scope(
        _item(["auto-transport-equipment"], "新能源汽车销售"), ["materials"]
    )
    assert matches_industry_scope(
        _item(["auto-transport-equipment"], "新能源汽车电池工程师"), ["materials"]
    )


def test_rows_without_industry_tags_never_match():
    assert not matches_industry_scope({"positionsText": "材料工程师"}, ["materials"])
    assert not matches_industry_scope(_item([], "材料工程师"), ["materials"])


def test_unknown_group_matches_nothing():
    assert not matches_industry_scope(_item(["internet-tech"], "后端开发"), ["no-such-group"])


def test_role_match_catches_it_jobs_at_non_it_companies():
    """郑商所被标成金融业，但它招软件研发，按岗位看就是计算机岗。"""

    item = _item(["finance"], "软件研发岗、网络安全岗、财务会计岗", "郑州商品交易所集团")
    assert not matches_industry_scope(item, ["internet-tech"])
    assert matches_role_scope(item, ["internet-tech"])


def test_role_match_ignores_general_management_roles():
    item = _item(["finance"], "管理培训生、精算及产品开发")
    assert not matches_role_scope(item, ["internet-tech"])


def test_role_match_only_applies_to_selected_groups():
    """选的是材料，就不该因为岗位名带「算法」把纯软件岗收进来。"""

    item = _item(["internet-tech"], "算法工程师")
    assert matches_role_scope(item, ["internet-tech"])
    assert not matches_role_scope(item, ["materials"])


def test_role_match_never_matches_a_false_friend():
    item = _item(["finance"], "自动化工程师")
    assert not matches_role_scope(item, ["materials"])
