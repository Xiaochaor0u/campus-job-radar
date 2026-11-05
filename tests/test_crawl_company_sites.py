"""官网直采脚本的字段清洗：城市、校招/实习、届别。"""

from scripts.crawl_company_sites import city_of, cohort_of, track_of


def test_city_takes_the_first_real_place():
    assert city_of("河南省·漯河市") == "漯河"
    assert city_of("洛阳市-栾川县") == "洛阳"
    assert city_of("上海市") == "上海"
    assert city_of("巴西、厄瓜多尔、上海市") == "上海"
    assert city_of("") == ""


def test_track_separates_campus_from_social():
    assert track_of("双汇26&27届-计算机软件类管培生") == "campus"
    assert track_of("战略发展部实习生") == "internship"
    # 官网热招里的社招岗位不能混进校招雷达
    assert track_of("电气维修工") == "unknown"


def test_cohort_reads_the_year_off_the_title():
    assert cohort_of("双汇26&27届-计算机软件类管培生") == 2027
    assert cohort_of("2027届校园招聘-算法工程师") == 2027
    assert cohort_of("27届管培生") == 2027
    assert cohort_of("电气维修工") is None
