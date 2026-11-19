#!/usr/bin/env python3
"""Fetch KMA ASOS daily observations for every station and write web/data/weather.js.

Source: 기상청_지상(종관, ASOS) 일자료 조회서비스
        https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList

The endpoint authenticates ONLY with the `serviceKey` query parameter; it ignores
an `Authorization` header (verified: a header-only request returns
SERVICE_KEY_IS_NULL). Put the "Decoding" key from data.go.kr in the environment:

    DATA_GO_KR_SERVICE_KEY=... python3 scripts/fetch_asos.py --days 7

The API publishes a day's record the following day, so the newest date you can
get is yesterday (KST). Standard library only.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
ROOT = Path(__file__).resolve().parent.parent
STATIONS_CSV = ROOT / "scripts" / "stations.csv"
OUT_JS = ROOT / "web" / "data" / "weather.js"
KST = dt.timezone(dt.timedelta(hours=9))

# API field -> short key in the output. Units: m/s, degrees (wind FROM, 16-point),
# mm, degC, %, hPa, cm.
FIELDS = {
    "avgWs": "ws",          # 평균 풍속
    "maxWd": "wd",          # 최다풍향 (most frequent direction)
    "sumRn": "rn",          # 일강수량
    "hr1MaxRn": "rn1h",     # 1시간 최다강수량
    "maxWs": "wsMax",       # 최대 풍속 (10-min mean)
    "maxWsWd": "wdMax",     # 최대 풍속 풍향
    "maxInsWs": "gust",     # 최대 순간 풍속
    "avgTa": "ta",          # 평균 기온
    "minTa": "tmin",
    "maxTa": "tmax",
    "avgRhm": "rh",         # 평균 상대습도
    "avgPs": "ps",          # 평균 해면기압
    "ddMes": "snow",        # 일 최심적설
}


def num(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_stations():
    with STATIONS_CSV.open(encoding="utf-8") as f:
        return [
            {"id": int(r["id"]), "ko": r["ko"], "en": r["en"], "lat": float(r["lat"]), "lon": float(r["lon"])}
            for r in csv.DictReader(f)
        ]


def service_key() -> str:
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not key:
        sys.exit(
            "DATA_GO_KR_SERVICE_KEY is not set.\n"
            "This endpoint reads the key only from the serviceKey query parameter, so a\n"
            "proxy-injected Authorization header is not enough. Set the variable to the\n"
            "data.go.kr 'Decoding' key (the 'Encoding' key also works)."
        )
    # An 'Encoding' key is already percent-encoded; don't encode it twice.
    return key if "%" in key else urllib.parse.quote(key, safe="")


def fetch_station(key: str, stn: int, start: str, end: str, retries: int = 4):
    q = urllib.parse.urlencode(
        {
            "pageNo": 1,
            "numOfRows": 400,
            "dataType": "JSON",
            "dataCd": "ASOS",
            "dateCd": "DAY",
            "startDt": start,
            "endDt": end,
            "stnIds": stn,
        }
    )
    url = f"{ENDPOINT}?serviceKey={key}&{q}"
    last = None
    req = urllib.request.Request(url, headers={"User-Agent": "korea-asos-flow/1.0", "Accept": "application/json"})
    for attempt in range(retries):
        try:
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as he:
                # The gateway reports key problems as HTTP 401/403 with an explanatory body.
                body = he.read().decode("utf-8", "replace")
                if he.code >= 500 or not body.strip():
                    raise
            if body.lstrip().startswith("<") or "OpenAPI_ServiceResponse" in body:
                # Gateway errors come back as XML/JSON envelopes without a 'response' key.
                raise RuntimeError(f"gateway error: {body.strip()[:300]}")
            data = json.loads(body)
            head = data["response"]["header"]
            code = head.get("resultCode")
            if code == "03":  # NO_DATA
                return []
            if code != "00":
                raise RuntimeError(f"API {code}: {head.get('resultMsg')}")
            items = data["response"]["body"]["items"]["item"]
            return items if isinstance(items, list) else [items]
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, KeyError) as e:
            last = e
            if "SERVICE_KEY" in str(e) or "SERVICE ERROR" in str(e):
                raise  # auth problems won't fix themselves
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"station {stn}: {last}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7, help="number of days ending at --end (default 7)")
    ap.add_argument("--end", help="last date YYYYMMDD (default: yesterday, KST)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--min-coverage", type=float, default=0.6,
                    help="drop dates where fewer than this share of stations reported (default 0.6)")
    args = ap.parse_args()

    key = service_key()
    today = dt.datetime.now(KST).date()
    end = dt.datetime.strptime(args.end, "%Y%m%d").date() if args.end else today - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days - 1)
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    stations = load_stations()
    print(f"Fetching {len(stations)} stations, {s}–{e} …", file=sys.stderr)

    obs: dict[str, dict[str, dict]] = {}
    names: dict[int, str] = {}
    errors = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(fetch_station, key, st["id"], s, e): st for st in stations}
        for fut in cf.as_completed(futs):
            st = futs[fut]
            try:
                rows = fut.result()
            except Exception as ex:  # noqa: BLE001
                errors.append(f"{st['id']} {st['ko']}: {ex}")
                if "SERVICE_KEY" in str(ex):
                    pool.shutdown(cancel_futures=True)
                    sys.exit(f"Authentication failed: {ex}")
                continue
            for r in rows:
                day = str(r.get("tm", "")).replace("-", "")
                if not day:
                    continue
                names[st["id"]] = r.get("stnNm", "")
                rec = {short: num(r.get(api)) for api, short in FIELDS.items()}
                # Blank sumRn means "no precipitation" when the station otherwise reported.
                if rec["rn"] is None and rec["ta"] is not None:
                    rec["rn"] = 0.0
                if rec["snow"] is None and rec["ta"] is not None:
                    rec.pop("snow")
                obs.setdefault(day, {})[str(st["id"])] = {k: v for k, v in rec.items() if v is not None}

    for st in stations:
        api_name = names.get(st["id"])
        if api_name and api_name != st["ko"]:
            print(f"warning: station {st['id']} is '{api_name}' in the API, '{st['ko']}' in stations.csv",
                  file=sys.stderr)

    dates = sorted(d for d, v in obs.items() if len(v) >= args.min_coverage * len(stations))
    dropped = sorted(set(obs) - set(dates))
    if dropped:
        print(f"Dropped incomplete dates: {', '.join(dropped)}", file=sys.stderr)
    if not dates:
        sys.exit("No complete dates returned. Errors:\n  " + "\n  ".join(errors[:10]))

    reporting = {int(i) for d in dates for i in obs[d]}
    payload = {
        "source": "KMA ASOS daily observations (기상청 종관기상관측 일자료) via data.go.kr",
        "endpoint": ENDPOINT,
        "generated": dt.datetime.now(KST).isoformat(timespec="minutes"),
        "dates": dates,
        "stations": [st for st in stations if st["id"] in reporting],
        "obs": {d: obs[d] for d in dates},
    }
    OUT_JS.parent.mkdir(parents=True, exist_ok=True)
    OUT_JS.write_text(
        "// Generated by scripts/fetch_asos.py — do not edit.\nwindow.ASOS="
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {OUT_JS.relative_to(ROOT)}: {len(dates)} dates ({dates[0]}–{dates[-1]}), "
        f"{len(payload['stations'])} stations, {len(errors)} station errors",
        file=sys.stderr,
    )
    for msg in errors[:10]:
        print("  " + msg, file=sys.stderr)


if __name__ == "__main__":
    main()
