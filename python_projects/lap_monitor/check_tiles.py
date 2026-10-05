"""
check_tiles.py : 地図タイルサーバーの疎通確認用スクリプト（単体実行）

USAGE (WSL):
    python check_tiles.py

浜松付近 (z=14) のタイル1枚を各サーバーから取得し、
ステータスコード・Content-Type・サイズを表示する。
「API KEY REQUIRED」画像が返る場合は 200 でもサイズや内容が異なるため、
タイルを tile_<名前>.png として保存する。画像ビューアで目視確認すること。
"""
import math
import urllib.request

LAT, LON, Z = 34.7108, 137.7261, 14  # 浜松付近


def deg2tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


X, Y = deg2tile(LAT, LON, Z)

TILES = {
    "carto_positron": f"https://a.basemaps.cartocdn.com/light_all/{Z}/{X}/{Y}.png",
    "gsi_pale":       f"https://cyberjapandata.gsi.go.jp/xyz/pale/{Z}/{X}/{Y}.png",
    "gsi_std":        f"https://cyberjapandata.gsi.go.jp/xyz/std/{Z}/{X}/{Y}.png",
    "esri_imagery":   f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{Z}/{Y}/{X}",
}

for name, url in TILES.items():
    req = urllib.request.Request(url, headers={"User-Agent": "LapMonitor-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
            print(f"[{name}] status={r.status} type={r.headers.get('Content-Type')} size={len(data)}")
            ext = "jpg" if "jpeg" in (r.headers.get("Content-Type") or "") else "png"
            with open(f"tile_{name}.{ext}", "wb") as f:
                f.write(data)
    except Exception as e:
        print(f"[{name}] ERROR: {e}")
