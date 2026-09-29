"""
좌표 기반 숙박 공급 capacity(객실수) 파이프라인

88개 웰니스 관광지(data/웰니스관광지_88개_좌표.csv, WGS84)를 기준으로, 전국
숙박업 인허가 원자료(data/문화_숙박업.csv, EPSG:5174 투영좌표)에서 반경
1/2/5km 내 객실 수를 계산한다. data/행정동경계.zip(EPSG:5186, 3,559개
행정동 폴리곤)으로 "같은 행정동" 기준 교차검증도 병행한다.

핵심 공식(사용자 지정):
    Rooms_i = 양실수_i + 한실수_i
    RoomSupply_{g,t} = sum_{i in g} Rooms_i * Active_{i,t}

Active_{i,t}는 "재고(stock)" 지표다 — t 시점에 실제로 영업 중이었는가
(인허가일자 <= t and (폐업일자 is null or 폐업일자 > t)). 이는 기간 중
개업-폐업 건수를 보는 순증(flow) 지표와 다른 질문에 답한다: 순증="이
기간 시장이 늘었나 줄었나", 재고="이
시점에 실제 존재한 객실이 몇 개인가". 둘 다 legitimate하고, 보고서에는
나란히 제시한다.

CRS 검증(별도로 pyproj 랜드마크 대조 3곳 — 해운대/서울/제주 — 로 실측
확인됨, scripts/geocode_wellness88.py 실행 로그 참고):
  - 문화_숙박업.csv 좌표정보(X/Y): EPSG:5174
  - 행정동경계.zip: EPSG:5186 (.prj WKT 파라미터와 정확히 일치)
  - data/웰니스관광지_88개_좌표.csv mapx/mapy: EPSG:4326(WGS84, TourAPI 표준)

실행:
    venv/bin/python3 scripts/geo_room_capacity.py

출력:
    output/대시보드_보조데이터/geo_room_capacity.csv         - 방법(radius_1/2/5km|dong) x 기간(P1~P4)
    output/대시보드_보조데이터/geo_room_capacity_monthly.csv - 방법(radius_1/2/5km) x 연월(60개월)
    output/대시보드_보조데이터/geo_room_capacity_dong_detail.csv - 시설/업체 행정동 배정 상세

한계(명시):
  - 좌표 결측 9.8%(5,774건)는 분석에서 빠짐 — 이만큼 과소추정 가능성.
  - 스테이 테마 8개소는 자기 자신이 숙박업 등록부에도 있어 반경 0m에서
    자기 자신이 잡히는 문제가 있다 — 이름+근접거리(<50m)로 자기매칭을
    찾아 해당 시설 자신의 RoomSupply 계산에서만 제외한다.
  - 일부 시설 좌표는 도로/읍면 중심점 근사치(수동 지오코딩 시 확인,
    data/웰니스관광지_88개_좌표.csv의 match_confidence 참고) — low 등급은 특히
    반경 1km 결과의 신뢰도가 떨어질 수 있다.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyproj
import shapefile
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output" / "대시보드_보조데이터"
OUT.mkdir(parents=True, exist_ok=True)

CRS_LODGING = "EPSG:5174"
CRS_DONG = "EPSG:5186"
CRS_WGS84 = "EPSG:4326"

RADII_M = {"radius_1km": 1000, "radius_2km": 2000, "radius_5km": 5000}

HIGH_END = ["일반호텔", "관광호텔", "휴양콘도미니엄업"]
LOW_END = ["여관업", "여인숙업", "숙박업(생활)"]

PERIODS = {
    2023: {"P1": ("2021-04-01", "2022-03-31"), "P2": ("2022-04-01", "2023-03-31"),
           "P3": ("2023-04-01", "2024-03-31"), "P4": ("2024-04-01", "2025-03-31")},
    2024: {"P1": ("2022-04-01", "2023-03-31"), "P2": ("2023-04-01", "2024-03-31"),
           "P3": ("2024-04-01", "2025-03-31"), "P4": ("2025-04-01", "2026-03-31")},
}
MONTH_START, MONTH_END = "2021-04", "2026-03"


# --------------------------------------------------------------------------
# 로딩 + 좌표 변환
# --------------------------------------------------------------------------

def load_facilities() -> pd.DataFrame:
    df = pd.read_csv(ROOT / "data/웰니스관광지_88개_좌표.csv")
    df = df.dropna(subset=["mapx", "mapy"]).copy()
    t = pyproj.Transformer.from_crs(CRS_WGS84, "EPSG:5179", always_xy=True)
    df["x5179"], df["y5179"] = t.transform(df["mapx"].values, df["mapy"].values)
    t2 = pyproj.Transformer.from_crs(CRS_WGS84, CRS_DONG, always_xy=True)
    df["x5186"], df["y5186"] = t2.transform(df["mapx"].values, df["mapy"].values)
    df["is_stay_theme"] = df["테마"] == "스테이"
    yr_map = {}
    yr_map.update({n: 2023 for n in [
        "산림힐링재단(하이힐링원)", "삼척 활기 치유의숲", "숲애서", "국립칠곡숲체원", "국립제천치유의숲"]})
    yr_map.update({n: 2024 for n in [
        "완도 해양치유센터", "쉴(SHIL)랜드", "레인보우 힐링센터", "아원고택", "소백산생태탐방원"]})
    df["코호트"] = df["시설명"].map(yr_map)
    return df


def load_lodging() -> pd.DataFrame:
    df = pd.read_csv(DATA / "문화_숙박업.csv", encoding="cp949", low_memory=False)
    df = df.dropna(subset=["좌표정보(X)", "좌표정보(Y)"]).copy()
    # 명백한 이상치(대한민국 영역을 크게 벗어나는 값) 제거
    df = df[(df["좌표정보(X)"].between(-10000, 700000)) & (df["좌표정보(Y)"].between(-10000, 700000))]
    df["인허가일자"] = pd.to_datetime(df["인허가일자"], errors="coerce")
    df["폐업일자"] = pd.to_datetime(df["폐업일자"], errors="coerce")
    df["rooms"] = df["양실수"].fillna(0) + df["한실수"].fillna(0)
    t = pyproj.Transformer.from_crs(CRS_LODGING, "EPSG:5179", always_xy=True)
    df["x5179"], df["y5179"] = t.transform(df["좌표정보(X)"].values, df["좌표정보(Y)"].values)
    t2 = pyproj.Transformer.from_crs(CRS_LODGING, CRS_DONG, always_xy=True)
    df["x5186"], df["y5186"] = t2.transform(df["좌표정보(X)"].values, df["좌표정보(Y)"].values)
    df["tier"] = np.where(df["업태구분명"].isin(HIGH_END), "고급",
                   np.where(df["업태구분명"].isin(LOW_END), "저가", "기타"))
    return df.reset_index(drop=True)


def load_dong_polygons():
    z = zipfile.ZipFile(DATA / "행정동경계.zip")
    names = z.namelist()
    shp = io.BytesIO(z.read(next(n for n in names if n.endswith(".shp"))))
    dbf = io.BytesIO(z.read(next(n for n in names if n.endswith(".dbf"))))
    shx = io.BytesIO(z.read(next(n for n in names if n.endswith(".shx"))))
    sf = shapefile.Reader(shp=shp, dbf=dbf, shx=shx, encoding="cp949")
    polys, adm_cds, adm_nms = [], [], []
    for sr in sf.iterShapeRecords():
        polys.append(shape(sr.shape.__geo_interface__))
        adm_cds.append(sr.record["ADM_CD"])
        adm_nms.append(sr.record["ADM_NM"])
    return polys, adm_cds, adm_nms


def assign_dong(x5179: np.ndarray, y5179: np.ndarray, polys, adm_cds, adm_nms) -> list[str | None]:
    tree = STRtree(polys)
    idx_by_id = {id(p): i for i, p in enumerate(polys)}
    out = []
    for x, y in zip(x5179, y5179):
        pt = Point(x, y)
        cand_idx = tree.query(pt)
        found = None
        for ci in cand_idx:
            poly = polys[ci]
            if poly.contains(pt) or poly.intersects(pt):
                found = ci
                break
        out.append(adm_cds[found] if found is not None else None)
    return out


# --------------------------------------------------------------------------
# 자기매칭(스테이 8개소) 제외 대상 탐색
# --------------------------------------------------------------------------

def find_self_matches(fac: pd.DataFrame, lodge: pd.DataFrame) -> dict[int, int]:
    """스테이 테마 시설이 문화_숙박업.csv 자기 자신 행을 찾는다(<=80m, 이름
    한 토큰 이상 공유). 반환: {facility_번호: lodging_row_index}"""
    self_map = {}
    stay = fac[fac["is_stay_theme"]]
    for _, f in stay.iterrows():
        dx = lodge["x5179"].values - f["x5179"]
        dy = lodge["y5179"].values - f["y5179"]
        dist = np.hypot(dx, dy)
        near_idx = np.where(dist <= 80)[0]
        if len(near_idx) == 0:
            continue
        name_tokens = [t for t in str(f["시설명"]).split() if len(t) >= 2]
        best = None
        for i in near_idx:
            biz_name = str(lodge.iloc[i]["사업장명"])
            if any(tok in biz_name for tok in name_tokens):
                best = i
                break
        if best is None:
            best = near_idx[np.argmin(dist[near_idx])]
        self_map[int(f["번호"])] = int(best)
    return self_map


# --------------------------------------------------------------------------
# Active(재고) 행렬
# --------------------------------------------------------------------------

def month_ends(start: str, end: str) -> pd.DatetimeIndex:
    return pd.date_range(start=start, end=end, freq="ME")


def active_matrix(lodge: pd.DataFrame, snapshots: pd.DatetimeIndex) -> np.ndarray:
    lic = lodge["인허가일자"].values.astype("datetime64[ns]")
    clo = lodge["폐업일자"].values.astype("datetime64[ns]")
    snap = snapshots.values.astype("datetime64[ns]")
    licensed = lic[:, None] <= snap[None, :]
    not_closed = pd.isna(clo)[:, None] | (clo[:, None] > snap[None, :])
    return licensed & not_closed  # (N_biz, N_snap)


# --------------------------------------------------------------------------
# A-1. 최근접 숙박업체까지 실제 거리
# --------------------------------------------------------------------------

def nearest_lodging(fac: pd.DataFrame, lodge: pd.DataFrame, dist: np.ndarray,
                     active_last: np.ndarray, self_matches: dict[int, int]) -> pd.DataFrame:
    """반경 구간과 무관하게, 마지막 스냅샷(P4) 기준 영업 중인 숙박업체 중
    가장 가까운 곳까지의 실제 거리(km)를 계산한다. "반경 Nkm 안에 몇 실"이
    아니라 "실제로 몇 km를 가야 하는지"를 단일 숫자로 보여주는 보완 지표."""
    rows = []
    for fac_idx, frow in fac.iterrows():
        num = int(frow["번호"])
        d = dist[fac_idx].copy()
        mask = active_last.copy()
        exclude = self_matches.get(num)
        if exclude is not None:
            mask = mask.copy()
            mask[exclude] = False
        d_masked = np.where(mask, d, np.inf)
        if not np.isfinite(d_masked).any():
            rows.append({"번호": num, "시설명": frow["시설명"], "최근접_거리_km": None,
                         "최근접_업체명": None, "최근접_업태": None, "최근접_객실수": None})
            continue
        j = int(np.argmin(d_masked))
        rows.append({
            "번호": num, "시설명": frow["시설명"],
            "최근접_거리_km": round(float(d_masked[j]) / 1000, 2),
            "최근접_업체명": lodge.iloc[j]["사업장명"],
            "최근접_업태": lodge.iloc[j]["업태구분명"],
            "최근접_객실수": float(lodge.iloc[j]["rooms"]),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# A-3. 전국 대비 백분위 벤치마킹
# --------------------------------------------------------------------------

def national_dong_percentile(fac: pd.DataFrame, lodge: pd.DataFrame,
                              active_last: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    """전국 3,559개 행정동 전체의 (마지막 스냅샷 기준) 활성 객실수 합계
    분포를 구하고, 우리 88개소가 위치한 행정동이 전국에서 몇 백분위인지
    계산한다. assign_dong()으로 이미 만든 dong_adm_cd 배정을 재사용."""
    lodge_active = lodge[active_last].copy()
    national = lodge_active.groupby("dong_adm_cd").agg(
        객실수_합계=("rooms", "sum"), 업체수=("rooms", "size")).reset_index()
    national = national.sort_values("객실수_합계").reset_index(drop=True)
    national["전국_백분위"] = (national.index + 1) / len(national) * 100

    fac_pct = fac[["번호", "시설명", "dong_adm_cd"]].merge(
        national[["dong_adm_cd", "객실수_합계", "업체수", "전국_백분위"]],
        on="dong_adm_cd", how="left")
    fac_pct["객실수_합계"] = fac_pct["객실수_합계"].fillna(0)
    fac_pct["전국_백분위"] = fac_pct["전국_백분위"].fillna(0)
    return national, fac_pct


# --------------------------------------------------------------------------
# 메인
# --------------------------------------------------------------------------

def main():
    print("데이터 로딩...")
    fac = load_facilities()
    lodge = load_lodging()
    print(f"  시설 {len(fac)}/88 (좌표 있음), 숙박업체 {len(lodge)}건 (좌표 있음, "
          f"전체 대비 {len(lodge)/58661*100:.1f}%)")

    self_matches = find_self_matches(fac, lodge)
    print(f"  스테이 자기매칭 {len(self_matches)}건 발견 → 해당 시설 자신의 집계에서 제외")

    # ---- 반경 거리 행렬 (88 x N_biz) ----
    fx, fy = fac["x5179"].values, fac["y5179"].values
    bx, by = lodge["x5179"].values, lodge["y5179"].values
    dist = np.hypot(fx[:, None] - bx[None, :], fy[:, None] - by[None, :])  # (88, N)

    # ---- 행정동 배정 (정적, 1회) ----
    print("행정동 폴리곤 로딩 및 point-in-polygon 배정...")
    polys, adm_cds, adm_nms = load_dong_polygons()
    fac_dong = assign_dong(fac["x5186"].values, fac["y5186"].values, polys, adm_cds, adm_nms)
    biz_dong = assign_dong(lodge["x5186"].values, lodge["y5186"].values, polys, adm_cds, adm_nms)
    fac = fac.reset_index(drop=True)
    fac["dong_adm_cd"] = fac_dong
    lodge["dong_adm_cd"] = biz_dong
    same_dong = (np.array(fac_dong)[:, None] == np.array(biz_dong)[None, :]) & \
                (np.array([d is not None for d in fac_dong])[:, None])

    dong_detail_rows = []
    for i, row in fac.iterrows():
        dong_detail_rows.append({"번호": row["번호"], "시설명": row["시설명"],
                                  "type": "facility", "dong_adm_cd": row["dong_adm_cd"]})
    pd.DataFrame(dong_detail_rows).to_csv(OUT / "geo_room_capacity_dong_detail.csv",
                                           index=False, encoding="utf-8-sig")

    # ---- Active 재고 행렬 (월별 스냅샷) ----
    print("월별 Active(재고) 행렬 계산...")
    snapshots = month_ends(MONTH_START, MONTH_END)
    active = active_matrix(lodge, snapshots)  # (N_biz, N_month)

    rooms = lodge["rooms"].values
    tier = lodge["tier"].values
    is_high = tier == "고급"
    is_low = tier == "저가"
    is_other = tier == "기타"

    method_masks = {name: (dist <= r) for name, r in RADII_M.items()}
    method_masks["dong"] = same_dong

    def compute_supply(mask_2d: np.ndarray, exclude_row: int | None, fac_idx: int):
        m = mask_2d[fac_idx].copy()
        if exclude_row is not None:
            m = m.copy()
            m[exclude_row] = False
        rooms_all = (m[:, None] * active * rooms[:, None]).sum(axis=0)
        rooms_high = (m[:, None] * active * (rooms * is_high)[:, None]).sum(axis=0)
        rooms_low = (m[:, None] * active * (rooms * is_low)[:, None]).sum(axis=0)
        rooms_other = (m[:, None] * active * (rooms * is_other)[:, None]).sum(axis=0)
        cnt_all = (m[:, None] & active).sum(axis=0)
        cnt_high = (m[:, None] & active & is_high[:, None]).sum(axis=0)
        cnt_low = (m[:, None] & active & is_low[:, None]).sum(axis=0)
        cnt_other = (m[:, None] & active & is_other[:, None]).sum(axis=0)
        return rooms_all, rooms_high, rooms_low, rooms_other, cnt_all, cnt_high, cnt_low, cnt_other

    print("반경/행정동 x 월별 공급량 계산...")
    monthly_rows = []
    period_rows = []
    month_labels = [d.strftime("%Y-%m") for d in snapshots]

    for fac_idx, frow in fac.iterrows():
        num = int(frow["번호"])
        exclude = self_matches.get(num)
        cohort = frow["코호트"]
        for method, mask2d in method_masks.items():
            (r_all, r_high, r_low, r_other,
             c_all, c_high, c_low, c_other) = compute_supply(mask2d, exclude, fac_idx)

            for mi, mlabel in enumerate(month_labels):
                monthly_rows.append({
                    "번호": num, "지역": frow["시군구"], "시설명": frow["시설명"], "테마": frow["테마"],
                    "is_stay_theme": frow["is_stay_theme"], "방법": method, "연월": mlabel,
                    "숙박시설수_전체": int(c_all[mi]), "숙박시설수_고급": int(c_high[mi]),
                    "숙박시설수_저가": int(c_low[mi]), "숙박시설수_기타": int(c_other[mi]),
                    "객실수_전체": float(r_all[mi]), "객실수_고급": float(r_high[mi]),
                    "객실수_저가": float(r_low[mi]), "객실수_기타": float(r_other[mi]),
                })

            if pd.notna(cohort):
                for pname, (s, e) in PERIODS[int(cohort)].items():
                    sel = [i for i, m in enumerate(month_labels)
                           if s[:7] <= m <= e[:7]]
                    period_rows.append({
                        "번호": num, "지역": frow["시군구"], "시설명": frow["시설명"], "테마": frow["테마"],
                        "is_stay_theme": frow["is_stay_theme"], "코호트": int(cohort),
                        "방법": method, "기간": pname,
                        "숙박시설수_전체": round(float(np.mean(c_all[sel])), 1),
                        "숙박시설수_고급": round(float(np.mean(c_high[sel])), 1),
                        "숙박시설수_저가": round(float(np.mean(c_low[sel])), 1),
                        "숙박시설수_기타": round(float(np.mean(c_other[sel])), 1),
                        "객실수_전체": round(float(np.mean(r_all[sel])), 1),
                        "객실수_고급": round(float(np.mean(r_high[sel])), 1),
                        "객실수_저가": round(float(np.mean(r_low[sel])), 1),
                        "객실수_기타": round(float(np.mean(r_other[sel])), 1),
                    })

    monthly_df = pd.DataFrame(monthly_rows)
    period_df = pd.DataFrame(period_rows)
    monthly_df.to_csv(OUT / "geo_room_capacity_monthly.csv", index=False, encoding="utf-8-sig")
    period_df.to_csv(OUT / "geo_room_capacity.csv", index=False, encoding="utf-8-sig")

    print(f"\n저장 완료: {OUT}/geo_room_capacity.csv ({len(period_df)}행), "
          f"geo_room_capacity_monthly.csv ({len(monthly_df)}행)")

    # ---- A-1. 최근접 숙박업체까지 실제 거리 ----
    active_last = active[:, -1]  # 마지막 스냅샷(2026-02 또는 03) 기준
    nearest_df = nearest_lodging(fac, lodge, dist, active_last, self_matches)
    nearest_df.to_csv(OUT / "geo_nearest_lodging.csv", index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUT}/geo_nearest_lodging.csv ({len(nearest_df)}행)")

    # ---- A-3. 전국 대비 백분위 벤치마킹 ----
    national, fac_pct = national_dong_percentile(fac, lodge, active_last)
    national.to_csv(OUT / "geo_dong_national_percentile.csv", index=False, encoding="utf-8-sig")
    fac_pct.to_csv(OUT / "geo_facility_national_percentile.csv", index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUT}/geo_dong_national_percentile.csv (전국 {len(national)}개 행정동), "
          f"geo_facility_national_percentile.csv (88개소)")

    print("\n=== 핵심 10개소, 최근접 숙박업체 거리 + 전국 백분위 ===")
    core_nearest = nearest_df[nearest_df["번호"].isin(fac[fac["코호트"].notna()]["번호"])]
    core_pct = fac_pct[fac_pct["번호"].isin(fac[fac["코호트"].notna()]["번호"])]
    preview = core_nearest.merge(core_pct[["번호", "전국_백분위"]], on="번호")
    print(preview[["시설명", "최근접_거리_km", "최근접_업체명", "전국_백분위"]].to_string(index=False))

    # ---- 핵심 10개소 P1~P4 요약 미리보기 ----
    core10 = period_df[period_df["코호트"].notna()]
    print("\n=== 핵심 10개소, 반경 2km, 객실수_전체 P1~P4 ===")
    pv = core10[core10["방법"] == "radius_2km"].pivot_table(
        index="시설명", columns="기간", values="객실수_전체", aggfunc="first")
    print(pv.reindex(columns=["P1", "P2", "P3", "P4"]).to_string())


if __name__ == "__main__":
    main()
