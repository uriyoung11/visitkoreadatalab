"""
88개 웰니스 관광지의 좌표를 한국관광공사 TourAPI(KorService2 searchKeyword2)로
확보해 data/웰니스관광지_88개_좌표.csv에 캐시한다.

실행:
    venv/bin/python3 scripts/geocode_wellness88.py            # 미확보/미검토분만 호출
    venv/bin/python3 scripts/geocode_wellness88.py --force    # geocode_source=api 인 행 재조회
                                                                 (manual 행은 --force로도 절대 건드리지 않음)

동작 원리:
- searchKeyword2는 등록된 title에 대한 부분일치만 지원하고 유사도 검색은
  하지 않는다 — "허브아일랜드 허브힐링센터"처럼 공백/특수문자로 이어진
  긴 합성 명칭을 그대로 던지면 대부분 0건이 나온다(실측 확인됨). 이를
  보완하기 위해 전체명 -> 특수문자 정리본 -> 첫/마지막 토큰 -> 최장 토큰
  순으로 후보 질의어를 순차 시도하고(`query_candidates`), 모든 질의의
  결과를 모아 addr1에 해당 행의 시군구 토큰이 포함되는 후보만 채택한다
  (지역이 안 맞는 동명이인 결과를 자동으로 걸러내기 위함 — 예: "레인보우
  힐링센터"를 "레인보우"로 검색하면 전국의 무관한 "레인보우 OO"가 섞여
  나오므로 반드시 지역 일치를 요구한다).
- 지역이 일치하는 후보가 있으면 match_confidence=high, 결과는 있으나
  지역이 하나도 안 맞으면 low(자동 채택하지 않고 후보만 기록), 아예
  결과가 없으면 unmatched로 review_needed=True 표시한다.
- geocode_source=manual인 행은 사용자가 직접 좌표를 확인/수정한 것이므로
  --force를 줘도 절대 재호출하지 않는다. 이번 세션에 data/웰니스관광지_88개_목록.csv의
  시군구 7개소를 사용자가 직접 고친 것과 동일한 human-in-the-loop 워크플로.
- 실행이 끝나면 review_needed 행을 표로 출력한다 — data/웰니스관광지_88개_좌표.csv를
  직접 열어 mapx/mapy를 고치고 geocode_source를 manual로 바꿔주면 된다.
  TourAPI 자체에 등록되지 않은 시설(아원고택 등 다수 확인됨)은 API로는
  근본적으로 해결이 안 되므로 수동 검색(네이버지도/구글맵 좌표 복사 등)이
  필요하다.

무료 계정 기준 TourAPI 호출 한도는 1일 1,000건 — 88건은 여유롭다.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
WELLNESS_CSV = ROOT / "data/웰니스관광지_88개_목록.csv"
GEOCODED_CSV = ROOT / "data/웰니스관광지_88개_좌표.csv"
ENV_PATH = ROOT / ".env"

API_BASE = "https://apis.data.go.kr/B551011/KorService2/searchKeyword2"

GEOCODE_COLS = [
    "mapx", "mapy", "geocode_source", "match_confidence", "review_needed",
    "tour_api_title", "tour_api_addr", "tour_api_candidates", "geocoded_at",
]


def load_api_key() -> str:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("TOUR_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"TOUR_API_KEY not found in {ENV_PATH}")


def query_candidates(name: str) -> list[tuple[str, str]]:
    """searchKeyword2는 등록된 title에 대한 '부분일치'만 하고 유사도 검색은
    하지 않는다(공백/&로 이어진 긴 합성 명칭은 그대로 검색하면 0건이 흔함).
    전체 명칭 -> 특수문자 정리본 -> 첫/마지막 토큰 -> 최장 토큰 순으로
    후보 질의어를 생성해 순차 시도한다.

    각 후보에 level("strict"|"loose")을 함께 매긴다. "삼척"·"제원"·"스파"
    같은 1~3글자 토큰으로 fallback 검색을 하면 지역만 맞고 완전히 다른
    업소(노브랜드 삼척중앙시장점, 올리브영 제주제원점 등)가 걸리는 사례가
    실제로 다수 확인됐다 — 원본명 그대로/구두점 정리본으로 찾은 결과만
    "strict"(자동 high 확정), 토큰 fallback으로 찾은 결과는 지역이 맞아도
    "loose"로 남겨 항상 수동 검토를 거치게 한다."""
    strict = [name]
    cleaned = re.sub(r"[&·/]", " ", name)
    cleaned = re.sub(r"[()]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned != name:
        strict.append(cleaned)

    loose = []
    tokens = cleaned.split()
    if len(tokens) >= 2:
        loose.append(tokens[0])
        loose.append(tokens[-1])
    if tokens:
        longest = max(tokens, key=len)
        if longest not in loose:
            loose.append(longest)

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for c in strict:
        if c not in seen and len(c) >= 2:
            seen.add(c)
            out.append((c, "strict"))
    for c in loose:
        if c not in seen and len(c) >= 2:
            seen.add(c)
            out.append((c, "loose"))
    return out


def search_keyword(keyword: str, api_key: str, num_rows: int = 10) -> list[dict]:
    params = {
        "serviceKey": api_key,
        "MobileOS": "ETC",
        "MobileApp": "wellness_pipeline",
        "numOfRows": num_rows,
        "pageNo": 1,
        "_type": "json",
        "keyword": keyword,
    }
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    header = body.get("response", {}).get("header", {})
    if header.get("resultCode") != "0000":
        raise RuntimeError(f"TourAPI error: {header}")
    items = body.get("response", {}).get("body", {}).get("items", "")
    if items == "" or items is None:
        return []
    item = items.get("item", [])
    if isinstance(item, dict):
        item = [item]
    return item


def region_matches(item: dict, gugun: str) -> bool:
    addr = item.get("addr1", "") or ""
    # data/웰니스관광지_88개_목록.csv의 "시군구" 필드는 대부분 "시도 시군구" 2토큰이지만,
    # 사용자가 일부 행을 "시도 시군구 행정동"(3~4토큰)으로 직접 보정해둔
    # 경우가 있다 — 항상 두 번째 토큰(구/군/시)이 실제 구군명이다.
    parts = gugun.split() if gugun and gugun != "(확인필요)" else []
    gugun_tok = parts[1] if len(parts) >= 2 else (parts[0] if parts else "")
    return bool(gugun_tok) and gugun_tok in addr


def geocode_row(row: pd.Series, api_key: str) -> dict:
    name = str(row["시설명"])
    gugun = str(row.get("시군구", ""))
    now = datetime.now(timezone.utc).isoformat()
    pool: list[dict] = []
    tried: list[str] = []
    matched = None
    matched_query = None
    matched_level = None

    for q, level in query_candidates(name):
        tried.append(q)
        try:
            results = search_keyword(q, api_key)
        except Exception as e:
            return {
                "mapx": None, "mapy": None, "geocode_source": "api",
                "match_confidence": "", "review_needed": True,
                "tour_api_title": f"ERROR: {e}", "tour_api_addr": "",
                "tour_api_candidates": "", "geocoded_at": now,
            }
        time.sleep(0.12)
        pool.extend(results)
        # 이 질의 응답 안에서 지역이 맞는 후보가 여러 개일 수 있다(예: "제원"으로
        # 검색하면 지역이 맞는 "올리브영 제주제원점"과 "제원하늘농원"이 둘 다 나옴).
        # 첫 번째로 나온 것을 덮어씌우지 말고, 원본 시설명과 title의 유사도가
        # 가장 높은 후보를 고른다.
        region_ok = [r for r in results if region_matches(r, gugun)]
        if region_ok:
            best = max(region_ok, key=lambda r: SequenceMatcher(None, name, r.get("title", "")).ratio())
            matched = best
            matched_query = q
            matched_level = level
        if matched is not None:
            # query_candidates()는 strict 후보를 loose보다 먼저 반환하므로,
            # 여기서 멈추면 strict 매치를 항상 loose보다 우선한 것이 된다.
            break

    if matched is not None:
        others = [r for r in pool if r is not matched][:4]
        candidates = "|".join(f"{r.get('title','')}({r.get('addr1','')})" for r in others)
        if matched_level == "strict":
            return {
                "mapx": float(matched.get("mapx")), "mapy": float(matched.get("mapy")),
                "geocode_source": "api", "match_confidence": "high",
                "review_needed": False,
                "tour_api_title": matched.get("title", ""), "tour_api_addr": matched.get("addr1", ""),
                "tour_api_candidates": f"[query={matched_query}] " + candidates,
                "geocoded_at": now,
            }
        # loose(토큰 fallback)로만 지역이 맞은 경우 — 좌표는 최선의 추정치로 채워두되
        # 반드시 사람이 확인하도록 review_needed는 True로 남긴다(자동 확정 금지).
        return {
            "mapx": float(matched.get("mapx")), "mapy": float(matched.get("mapy")),
            "geocode_source": "api", "match_confidence": "medium",
            "review_needed": True,
            "tour_api_title": matched.get("title", ""), "tour_api_addr": matched.get("addr1", ""),
            "tour_api_candidates": f"[fallback query={matched_query}, 이름 일부만 일치 — 확인 필요] " + candidates,
            "geocoded_at": now,
        }

    if pool:
        # 결과는 있으나 지역이 하나도 안 맞음 — 자동 채택하지 않고, 이름이
        # 그나마 가장 비슷한 후보 순으로 정렬해 사람이 보기 편하게 남긴다
        ranked = sorted(pool, key=lambda r: SequenceMatcher(None, name, r.get("title", "")).ratio(), reverse=True)
        top4 = ranked[:4]
        candidates = "|".join(f"{r.get('title','')}({r.get('addr1','')})" for r in top4)
        return {
            "mapx": None, "mapy": None, "geocode_source": "api",
            "match_confidence": "low", "review_needed": True,
            "tour_api_title": "", "tour_api_addr": "",
            "tour_api_candidates": f"[tried={','.join(tried)}] " + candidates,
            "geocoded_at": now,
        }

    return {
        "mapx": None, "mapy": None, "geocode_source": "api",
        "match_confidence": "", "review_needed": True,
        "tour_api_title": "", "tour_api_addr": "",
        "tour_api_candidates": f"[tried={','.join(tried)}, 전부 0건]", "geocoded_at": now,
    }


def main(force: bool = False):
    base = pd.read_csv(WELLNESS_CSV)
    if GEOCODED_CSV.exists():
        existing = pd.read_csv(GEOCODED_CSV)
    else:
        existing = base.copy()
        for c in GEOCODE_COLS:
            existing[c] = None

    api_key = load_api_key()
    n_called = n_skipped_manual = n_skipped_cached = 0

    for idx, row in existing.iterrows():
        source = row.get("geocode_source")
        has_coords = pd.notna(row.get("mapx")) and pd.notna(row.get("mapy"))
        if source == "manual":
            n_skipped_manual += 1
            continue
        if has_coords and not force:
            n_skipped_cached += 1
            continue
        base_row = base.iloc[idx]
        result = geocode_row(base_row, api_key)
        for k, v in result.items():
            existing.at[idx, k] = v
        n_called += 1
        time.sleep(0.15)

    existing.to_csv(GEOCODED_CSV, index=False, encoding="utf-8-sig")

    print(f"API 호출: {n_called}건 / manual 보존: {n_skipped_manual}건 / 캐시 재사용: {n_skipped_cached}건")
    conf_counts = existing["match_confidence"].fillna("(none)").value_counts()
    print("신뢰도 분포:", conf_counts.to_dict())

    review = existing[existing["review_needed"] == True]  # noqa: E712
    if len(review):
        print(f"\n=== 수동 검토 필요 {len(review)}건 ===")
        cols = ["번호", "시설명", "시군구", "tour_api_title", "tour_api_addr", "tour_api_candidates"]
        print(review[cols].to_string(index=False))
        print(f"\n-> {GEOCODED_CSV.name} 을 직접 열어 mapx/mapy를 확인·수정하고 "
              f"geocode_source=manual, review_needed=False 로 표시해주세요.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="geocode_source=api 인 행도 재조회")
    args = parser.parse_args()
    main(force=args.force)
