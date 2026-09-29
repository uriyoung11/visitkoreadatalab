"""검증된 4개 지역 CSV를 Vercel 대시보드용 정적 JSON으로 변환한다."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pandas as pd
from shapely.geometry import mapping, shape


ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "output" / "4개_웰니스관광지_성과분석"
SUPPORT = ROOT / "output" / "대시보드_보조데이터"
PUBLIC = ROOT / "vercel-dashboard" / "public"

SITES = {
    "전북완주": {
        "region": "완주군", "site": "아원고택", "dong": "소양면", "year": 2024, "theme": "스테이",
        "longitude": 127.244624, "latitude": 35.903470, "geoName": "Wanju", "image": "wanju.jpg",
        "address": "전북특별자치도 완주군 소양면 송광수만로 516-7",
        "type": "숙박 저하형", "headline": "완주군 방문은 늘었지만, 숙박 비율은 낮아졌다",
        "bottleneck": ["숙박"], "bottleneckLabel": "숙박", "strength": "강함",
        "contextWarning": "아원고택 자체 성과가 아니라 완주군 전체 관광지표입니다.",
        "evidence": "숙박자 비율이 지정 시점에 기존 추세보다 0.74%p 낮아졌고, 2년차에도 돌아오지 않았습니다.",
        "good": "방문은 4년 연속 증가했고, 소비·체류는 지정 이후 회복 흐름입니다.",
        "recommend": [["1박 연계 상품", "숙박 가능한 웰니스 시설과 주변 관광지를 묶은 1박 패키지 검토"], ["저녁·야간 웰니스", "당일 방문객이 하룻밤 머물 이유가 되는 프로그램 검토"], ["숙박 공급 확인", "숙소 부족인지 머물 이유 부족인지 구분"]],
        "avoid": ["방문객 유치 홍보 확대", "방문은 이미 4년 연속 늘고 있어 우선 점검 축이 아닙니다."],
        "checkMetric": "숙박자비율_pct",
        "actions": [["지금", "1박 연계 상품·야간 프로그램 운영 여건 점검"], ["6개월 뒤", "숙박자 비율 반등 여부 확인"], ["재지정 검토", "지정 직전 수준 회복 여부 점검"]],
        "needs": [["시설 직접 이용", "예약·방문·숙박 구분 실적"], ["숙박 공급", "숙박업 개폐업·객실 수"], ["숙박 원인", "방문객 숙박지·예약·이동 동선"]],
    },
    "전북순창": {
        "region": "순창군", "site": "쉴랜드", "dong": "인계면", "year": 2024, "theme": "푸드",
        "longitude": 127.131587, "latitude": 35.431547, "geoName": "Sunchang", "image": "sunchang.jpg",
        "address": "전북특별자치도 순창군 인계면 인덕로 427-128",
        "type": "소비 저하형", "headline": "방문은 회복했지만, 소비는 낮은 수준에 머문다",
        "bottleneck": ["소비"], "bottleneckLabel": "소비", "strength": "강함",
        "contextWarning": "쉴랜드 자체 성과가 아니라 순창군 전체 관광지표입니다.",
        "evidence": "총 관광소비가 지정 직후 18.9% 감소했고 2년차에도 지정 직전보다 18.0% 낮았습니다. 방문자 대비 소비도 함께 줄었습니다.",
        "good": "방문은 2년차에 회복했고 쉴랜드가 있는 인계면의 소비 비중도 올랐습니다.",
        "recommend": [["체류·소비 결합", "장류·음식 체험을 예약과 현장 결제로 연결하는 방안 검토"], ["지역 소비 동선", "쉴랜드 방문 전후에 이용할 식음·체험 코스 검토"], ["소비 상품 점검", "결제율과 객단가가 낮은 프로그램 확인"]],
        "avoid": ["방문객 유치 확대", "방문은 2년차에 회복했습니다. 우선 점검할 축은 소비입니다."],
        "checkMetric": "내국인관광소비_천원",
        "actions": [["지금", "체류·소비 결합 상품 점검"], ["6개월 뒤", "총소비와 방문자 대비 소비 회복 확인"], ["재지정 검토", "방문 이후 소비 수준 개선 여부 점검"]],
        "needs": [["시설 직접 이용", "프로그램별 이용·예약·결제 실적"], ["소비 경로", "업종별·프로그램별 결제와 객단가"], ["체류·이용", "숙박·식음·체험 이용 동선"]],
    },
    "전남완도": {
        "region": "완도군", "site": "완도 해양치유센터", "dong": "신지면", "year": 2024, "theme": "자연/숲치유",
        "longitude": 126.818435, "latitude": 34.328049, "geoName": "Wando", "image": "wando.jpg",
        "address": "전라남도 완도군 신지면 내정2길 52-1",
        "type": "소비 저하·시설거점형", "headline": "체류는 길지만 소비는 낮고, 소비가 시설 주변에 집중된다",
        "bottleneck": ["소비"], "bottleneckLabel": "소비", "strength": "중간",
        "contextWarning": "완도 해양치유센터 자체 성과가 아니라 완도군 전체 관광지표입니다.",
        "evidence": "총 관광소비는 2년차에 반등했지만 방문자 대비 소비는 2년 연속 줄었습니다. 신지면의 군내 소비 비중도 높아졌습니다.",
        "good": "체류는 가장 길고 장기체류 비율도 다른 지역보다 높습니다.",
        "recommend": [["타 읍면 연계 체험", "센터 이용객을 다른 읍면 체험·소비로 연결하는 방안 검토"], ["장기체류 소비 연계", "지역화폐·쿠폰 등 체류 중 소비수단 검토"], ["군 단위 순환 동선", "신지면 방문객의 군 내 이동경로와 연계지점 확인"]],
        "avoid": ["체류 기간 연장", "체류는 이미 가장 깁니다. 막힌 곳은 소비입니다."],
        "checkMetric": "방문자대비관광소비_천원_proxy",
        "actions": [["지금", "타 읍면 체험·소비 연계 가능성 점검"], ["6개월 뒤", "방문 대비 소비와 타 읍면 비중 확인"], ["재지정 검토", "소비 회복과 군 내 분포 변화 점검"]],
        "needs": [["시설 직접 이용", "예약·프로그램·재방문 실적"], ["시설 이용", "군 방문객 중 센터 실제 이용 비율"], ["소비 경로", "체류 중 결제와 타 읍면 이동"]],
    },
    "전북무주": {
        "region": "무주군", "site": "태권도원 상징지구", "dong": "설천면", "year": 2022, "theme": "힐링/명상",
        "longitude": 127.7754294, "latitude": 36.0095801, "geoName": "Muju", "image": "muju.jpg",
        "address": "전북특별자치도 무주군 설천면 무설로 1482",
        "type": "선행 성장형", "caseNote": "보강 사례 · 2022년 기준 · 코로나 회복기 포함", "headline": "성과는 좋지만 상승은 지정 전에 이미 시작됐다",
        "bottleneck": ["체류"], "bottleneckLabel": "체류", "strength": "약함",
        "contextWarning": "태권도원 상징지구 자체 성과가 아니라 무주군 전체 관광지표입니다.",
        "evidence": "탐색·방문·소비는 늘었지만 지정 시점의 뚜렷한 계단 변화는 없고 체류시간은 2년차에 줄었습니다.",
        "good": "탐색·방문·숙박·소비가 함께 오른 유일한 지역입니다.",
        "recommend": [["연박 프로그램", "태권도원 체험과 연계한 2박 이상 프로그램 검토"], ["장기체류 상품", "3박 이상 체류를 늘릴 수 있는 수요와 제약요인 확인"]],
        "avoid": ["무주 방식의 단순 복제", "성과 일부는 지정 전 코로나 회복기 흐름입니다."],
        "checkMetric": "평균체류시간_분",
        "actions": [["지금", "유입 확대보다 연박 프로그램 우선 검토"], ["6개월 뒤", "3박 이상 비율과 체류시간 확인"], ["확산 검토 전", "회복기 효과를 분리해 적용 요소 점검"]],
        "needs": [["시설 직접 이용", "방문·예약·체험 인원"], ["방문 목적", "웰니스 목적 방문 여부와 만족도"], ["체류 원인", "당일·연박 목적과 이동 동선"]],
    },
}


def records(name: str) -> list[dict]:
    frame = pd.read_csv(ANALYSIS / name, encoding="utf-8-sig")
    return json.loads(frame.to_json(orient="records", force_ascii=False))


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def main() -> None:
    (PUBLIC / "data").mkdir(parents=True, exist_ok=True)
    (PUBLIC / "sites").mkdir(parents=True, exist_ok=True)

    origins = pd.DataFrame(records("mobility_origin_by_period.csv"))
    origin_top = {}
    for key in SITES:
        group = origins[(origins["지역키"] == key) & (origins["기간"] == "P4")].copy()
        group["비율(%)"] = pd.to_numeric(group["비율(%)"], errors="coerce")
        origin_top[key] = json.loads(group.nlargest(5, "비율(%)").to_json(orient="records", force_ascii=False))

    category_change = records("category_change.csv")

    lodging_key = {
        "완주 아원고택": "전북완주",
        "순창 쉴랜드": "전북순창",
        "완도 해양치유센터": "전남완도",
        "무주 태권도원 상징지구": "전북무주",
    }
    lodging_current = pd.read_csv(SUPPORT / "4개_관광지_숙박공급_현재.csv", encoding="utf-8-sig")
    lodging_periods = pd.read_csv(SUPPORT / "4개_관광지_숙박공급_P1_P4.csv", encoding="utf-8-sig")
    lodging_inventory = pd.read_csv(SUPPORT / "4개_관광지_숙박업체_상세.csv", encoding="utf-8-sig")
    lodging_inventory["도로명주소"] = lodging_inventory["도로명주소"].str.replace(
        "전남광주통합특별시 완도군", "전라남도 완도군", regex=False
    )
    lodging_sensitivity = pd.read_csv(SUPPORT / "대형부지_5km반경_민감도.csv", encoding="utf-8-sig")
    lodging = {}
    for label, key in lodging_key.items():
        current = lodging_current[lodging_current["관광지"].eq(label)]
        periods_supply = lodging_periods[
            lodging_periods["관광지"].eq(label) & lodging_periods["반경_km"].eq(5)
        ]
        inventory = lodging_inventory[lodging_inventory["관광지"].eq(label)]
        sensitivity = lodging_sensitivity[lodging_sensitivity["관광지"].eq(label)]
        lodging[key] = {
            "current": json.loads(current.to_json(orient="records", force_ascii=False)),
            "periods": json.loads(periods_supply.to_json(orient="records", force_ascii=False)),
            "inventory": json.loads(inventory.to_json(orient="records", force_ascii=False)),
            "sensitivity": None if sensitivity.empty else json.loads(
                sensitivity.iloc[0].to_json(force_ascii=False)
            ),
        }

    payload = clean({
        "sites": SITES,
        "kpi": records("kpi_by_period.csv"),
        "growth": records("growth_bottleneck.csv"),
        "monthly": records("monthly_input.csv"),
        "periods": records("period_definitions.csv"),
        "its": records("its_designation_hac3.csv"),
        "robustness": records("its_robustness.csv"),
        "spread": records("spatial_relative_growth_available_sites.csv"),
        "origins": origin_top,
        "categoryChange": category_change,
        "lodging": lodging,
    })
    (PUBLIC / "data" / "dashboard.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    source_images = ROOT / "images"
    image_map = {"wanju.jpg": "완주아원고택.jpg", "sunchang.jpg": "순창쉴랜드.jpg", "wando.jpg": "완도해양치유센터.jpg", "muju.jpg": "무주태권도원.jpg"}
    for target, source in image_map.items():
        shutil.copy2(source_images / source, PUBLIC / "sites" / target)

    raw_geo = json.loads((ROOT / "assets" / "jeolla-municipalities-geo.json").read_text(encoding="utf-8"))
    raw_geo["features"] = [
        {**feature, "geometry": mapping(shape(feature["geometry"]).simplify(0.003, preserve_topology=True))}
        for feature in raw_geo["features"]
    ]
    province_geo = json.loads((ROOT / "assets" / "skorea-provinces-geo.json").read_text(encoding="utf-8"))
    raw_geo["provinceFeatures"] = [
        {**feature, "geometry": mapping(shape(feature["geometry"]).simplify(0.003, preserve_topology=True))}
        for feature in province_geo["features"]
    ]
    (PUBLIC / "data" / "jeolla.geojson").write_text(
        json.dumps(raw_geo, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"saved: {PUBLIC / 'data' / 'dashboard.json'}")
    print(f"saved: {PUBLIC / 'data' / 'jeolla.geojson'}")


if __name__ == "__main__":
    main()
