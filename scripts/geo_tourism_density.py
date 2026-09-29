"""
가설 E — 시설 주변 "관광 생태계 밀도" 분석

88개 웰니스 관광지 각각의 반경 5km 안에 TourAPI에 등록된 다른 관광
콘텐츠(관광지·문화시설·축제/공연·여행코스·레포츠·숙박·쇼핑·음식점)가
얼마나 있는지 세어, "이 시설은 관광 클러스터 안에 있는가, 고립된
단일 시설인가"를 정량화한다. §8-4/§8-5에서 "완도·레인보우는 숙박은
있는데도 전환이 안 된다"고 확인했는데, 혹시 먹거리·볼거리(관광
생태계) 자체가 부족해서인지 확인하는 것이 목적이다.

실행:
    venv/bin/python3 scripts/geo_tourism_density.py

캐시 원칙: (시설, contentTypeId) 조합별로 결과를 `output/
대시보드_보조데이터/geo_tourism_density.csv`에 누적 저장한다. 이미 받은 조합은 재호출하지
않아, 무료 계정 일일 호출 한도(1,000회/일)를 넘으면 다음 날 이어서
실행할 수 있다.

contentTypeId 코드(TourAPI 표준, 실제 응답으로 확인함):
    12=관광지, 14=문화시설, 15=축제공연행사, 25=여행코스,
    28=레포츠, 32=숙박, 38=쇼핑, 39=음식점

한계: TourAPI 콘텐츠 등록이 전수조사가 아니라 자발적 등록에 가까워
농촌·소규모 지역은 실제보다 과소 등록됐을 수 있다(문화_숙박업.csv
같은 인허가 기반 행정통계가 아님) — 절대값보다 88개소 간 **상대
비교**로 해석해야 한다.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "대시보드_보조데이터"
OUT.mkdir(parents=True, exist_ok=True)
CACHE_CSV = OUT / "geo_tourism_density.csv"

API_BASE = "https://apis.data.go.kr/B551011/KorService2/locationBasedList2"
RADIUS_M = 5000

CONTENT_TYPES = {
    "12": "관광지", "14": "문화시설", "15": "축제공연행사", "25": "여행코스",
    "28": "레포츠", "32": "숙박", "38": "쇼핑", "39": "음식점",
}


def load_api_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TOUR_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("TOUR_API_KEY not found")


def get_total_count(mapx: float, mapy: float, content_type_id: str, api_key: str) -> int | None:
    params = {
        "serviceKey": api_key, "MobileOS": "ETC", "MobileApp": "wellness_pipeline",
        "numOfRows": 1, "pageNo": 1, "_type": "json",
        "mapX": mapx, "mapY": mapy, "radius": RADIUS_M, "contentTypeId": content_type_id,
    }
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        header = body.get("response", {}).get("header", {})
        if header.get("resultCode") != "0000":
            return None
        return int(body.get("response", {}).get("body", {}).get("totalCount", 0))
    except Exception:
        return None


def main():
    api_key = load_api_key()
    fac = pd.read_csv(ROOT / "data/웰니스관광지_88개_좌표.csv")
    fac = fac.dropna(subset=["mapx", "mapy"])

    if CACHE_CSV.exists():
        cache = pd.read_csv(CACHE_CSV)
    else:
        cache = pd.DataFrame(columns=["번호", "시설명", "contentTypeId", "구분", "반경_km", "totalCount"])
    done = set(zip(cache["번호"], cache["contentTypeId"]))

    rows = list(cache.itertuples(index=False))
    n_called = n_skipped = n_failed = 0

    for _, frow in fac.iterrows():
        num = int(frow["번호"])
        for ctid, label in CONTENT_TYPES.items():
            if (num, ctid) in done or (num, int(ctid)) in done:
                n_skipped += 1
                continue
            cnt = get_total_count(frow["mapx"], frow["mapy"], ctid, api_key)
            n_called += 1
            if cnt is None:
                n_failed += 1
                cnt = None
            rows.append((num, frow["시설명"], ctid, label, RADIUS_M / 1000, cnt))
            time.sleep(0.15)
            if n_called % 50 == 0:
                pd.DataFrame(rows, columns=["번호", "시설명", "contentTypeId", "구분", "반경_km", "totalCount"]) \
                    .to_csv(CACHE_CSV, index=False, encoding="utf-8-sig")
                print(f"  진행: {n_called}건 호출, {n_skipped}건 캐시 재사용, {n_failed}건 실패")

    df = pd.DataFrame(rows, columns=["번호", "시설명", "contentTypeId", "구분", "반경_km", "totalCount"])
    df.to_csv(CACHE_CSV, index=False, encoding="utf-8-sig")
    print(f"\n완료: 신규 호출 {n_called}건, 캐시 재사용 {n_skipped}건, 실패 {n_failed}건")
    print(f"저장: {CACHE_CSV} ({len(df)}행)")

    # ---- 요약: 시설별 총 콘텐츠 수(숙박 제외) ----
    non_lodging = df[df["contentTypeId"] != "32"]
    summary = non_lodging.groupby(["번호", "시설명"])["totalCount"].sum().reset_index()
    summary.columns = ["번호", "시설명", "관광생태계_밀도_숙박제외"]
    summary = summary.sort_values("관광생태계_밀도_숙박제외")
    print("\n=== 관광 생태계 밀도(반경 5km, 숙박 제외 총합), 하위 15개소 ===")
    print(summary.head(15).to_string(index=False))
    print("\n=== 핵심 10개소 ===")
    fac88 = pd.read_csv(ROOT / "data/웰니스관광지_88개_좌표.csv")
    core_names = ["산림힐링재단(하이힐링원)", "삼척 활기 치유의숲", "숲애서", "국립칠곡숲체원",
                  "국립제천치유의숲", "완도 해양치유센터", "쉴(SHIL)랜드", "레인보우 힐링센터",
                  "아원고택", "소백산생태탐방원"]
    print(summary[summary["시설명"].isin(core_names)].to_string(index=False))


if __name__ == "__main__":
    main()
