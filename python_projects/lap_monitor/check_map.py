"""
check_map.py : Garmin 接続なしで folium の背景地図だけを確認する単体スクリプト

USAGE (WSL):
    uv run python check_map.py

カレントディレクトリに check_map.html を出力する。
Windows 側ブラウザで開く場合のパス例:
    \\\\wsl.localhost\\Ubuntu\\home\\toru\\python_projects\\lap_monitor\\check_map.html
"""
import os
import folium

CENTER = [34.7108, 137.7261]  # 浜松付近

m = folium.Map(location=CENTER, zoom_start=14, tiles=None)

folium.TileLayer(
    tiles="https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
    attr='<a href="https://maps.gsi.go.jp/development/ichiran.html">国土地理院</a>',
    name="国土地理院 淡色地図",
    max_zoom=18,
).add_to(m)

folium.TileLayer(
    tiles="https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png",
    attr='<a href="https://maps.gsi.go.jp/development/ichiran.html">国土地理院</a>',
    name="国土地理院 標準地図",
    max_zoom=18,
    show=False,
).add_to(m)

folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr="Tiles &copy; Esri",
    name="Esri 衛星画像",
    max_zoom=19,
    show=False,
).add_to(m)

folium.PolyLine(
    [[34.705, 137.720], [34.710, 137.726], [34.715, 137.730]],
    color="blue", weight=4,
).add_to(m)

folium.LayerControl().add_to(m)

out = os.path.abspath("check_map.html")
m.save(out)
print("saved:", out)
