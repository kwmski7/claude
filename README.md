# Korea ASOS wind & rain map

A full-screen map of South Korea in the style of [earth.nullschool.net](https://earth.nullschool.net/):
animated flow lines show the wind, and the shading shows **daily precipitation** instead of wind speed.
Data comes from the KMA ASOS daily service on data.go.kr
(`1360000/AsosDalyInfoService/getWthrDataList`, 기상청 종관기상관측 일자료).

```
scripts/stations.csv     95 ASOS stations: id, Korean/English name, lat, lon
scripts/fetch_asos.py    fetches the last N days for every station -> web/data/weather.js
scripts/build_coast.mjs  Natural Earth 10m coastline/borders, clipped -> web/data/coast.js (committed)
web/index.html           the page (canvas, no libraries)
```

## Refresh the data

The endpoint authenticates **only** with the `serviceKey` query parameter. A key injected as an
`Authorization` header (how a cloud-environment network secret is sent) is ignored: the gateway answers
`SERVICE_KEY_IS_NULL`. Give the script the key through an environment variable instead
(the data.go.kr "Decoding" key; the "Encoding" key also works):

```bash
DATA_GO_KR_SERVICE_KEY=... python3 scripts/fetch_asos.py --days 7
```

```bash
python3 -m http.server 8000 --directory web
```

Then open http://localhost:8000. Opening `web/index.html` directly also works, since the data loads as scripts.

Options: `--end YYYYMMDD` (default: yesterday in KST), `--days N`, `--min-coverage 0.6`
(drops a date if fewer than 60% of stations have reported it yet). The script warns if a station name returned by
the API does not match `stations.csv`, which catches a wrong station ID.

## How the map is made

- **Wind**: daily mean speed (`avgWs`) blowing from the day's most frequent direction (`maxWd`, 16 points),
  converted to u/v components.
- **Precipitation**: `sumRn`, stepped at 0.1 / 1 / 5 / 10 / 20 / 50 mm on a single-hue blue ramp
  (checked for monotone lightness, step separation, and contrast against the map background).
  A blank `sumRn` counts as 0 mm when the station reported other values that day.
- **Gridding**: two-pass Barnes analysis on a 0.05° grid (Koch, DesJardins & Kocin 1983) with
  κ₀ ≈ 2,500 km² (35 km mean station spacing) and γ = 0.3. The field fades out between 45 and 115 km from
  the nearest station, so open sea and areas north of the DMZ are shown faded, or not at all.
- **Flow lines**: about 1 particle per 420 px² is advected through the gridded u/v field on a Web Mercator
  projection. Lines switch to dark ink over ≥ 20 mm shading to stay visible.

## Limits

- **Not real-time.** This is a daily product, and the newest day available is yesterday (KST). For current
  conditions use the KMA ultra-short-term nowcast (`VilageFcstInfoService_2.0/getUltraSrtNcst`, hourly, on a 5 km grid)
  or ASOS hourly data (`AsosHourlyInfoService`).
- The daily API has no vector-mean wind. Mean speed combined with the modal direction overstates organised flow on
  days when the wind turned (front passages, land/sea breezes).
- About 95 land and island stations, roughly 35 km apart, smooth out convective rainfall peaks.
  The station table on the page lists the observed values.
- Station coordinates in `stations.csv` were compiled by hand (to about 0.0001°, roughly 10 m) and are
  used only for placement. Replace them with KMA's official station metadata if you need survey accuracy.
