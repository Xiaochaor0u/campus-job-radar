"""City and cohort filters: the two narrowings a 郑州 / 2027 request relies on."""

from packages.job_filters import (
    cohort_label,
    matches_city,
    matches_cohort,
    normalize_place,
    parse_places,
    selected_place_label,
)


def _item(locations=None, years=None, recruit_type="秋招"):
    payload = {"companyName": "某公司", "recruitType": recruit_type, "positionsText": "后端开发"}
    if locations is not None:
        payload["locations"] = locations
    if years is not None:
        payload["targetYears"] = years
    return payload


def test_city_suffix_is_ignored():
    assert normalize_place("郑州市") == "郑州"
    assert normalize_place("郑州") == "郑州"
    assert normalize_place("北京市") == "北京"


def test_multiple_cities_are_split():
    assert parse_places("郑州、洛阳") == ("郑州", "洛阳")
    assert parse_places("郑州,洛阳") == ("郑州", "洛阳")
    assert parse_places(["郑州市", "洛阳"]) == ("郑州", "洛阳")


def test_empty_query_keeps_everything():
    assert matches_city(_item(["上海"]), "")
    assert matches_city(_item(["上海"]), "   ")


def test_city_matches_any_listed_location():
    assert matches_city(_item(["北京", "郑州"]), "郑州")
    assert matches_city(_item(["郑州市"]), "郑州")
    assert matches_city(_item(["郑州", "洛阳"]), "洛阳, 新乡")


def test_other_cities_and_missing_locations_are_excluded():
    assert not matches_city(_item(["北京", "上海"]), "郑州")
    assert not matches_city(_item([]), "郑州")
    assert not matches_city(_item(None), "郑州")


def test_city_prefix_matches_a_district():
    assert matches_city(_item(["郑州航空港区"]), "郑州")


def test_cohort_zero_means_any_year():
    assert matches_cohort(_item(years=[2026]), 0)
    assert matches_cohort(_item(years=None), 0)


def test_cohort_keeps_matching_campus_rows():
    assert matches_cohort(_item(years=[2027]), 2027)
    assert matches_cohort(_item(years=[2026, 2027]), 2027)
    assert not matches_cohort(_item(years=[2026]), 2027)
    assert not matches_cohort(_item(years=None), 2027)


def test_internships_are_exempt_from_the_cohort_filter():
    """寒假/日常/春季实习往往不写届别，按届别卡会把它们全删掉。"""

    assert matches_cohort(_item(years=[2026], recruit_type="实习"), 2027)
    assert matches_cohort(_item(years=None, recruit_type="实习"), 2027)


def test_labels_read_naturally():
    assert selected_place_label("") == "全部城市"
    assert selected_place_label("郑州") == "郑州"
    assert selected_place_label("郑州、洛阳") == "郑州、洛阳"
    assert cohort_label(0) == "不限届别"
    assert cohort_label(2027) == "2027 届"
