"""
============================================================
TITLE  : LapMonitor v5.04
PURPOSE: Garmin Connectから走行データを自動取得し、仮想ラップ解析と
         速度変化の5色スムーズグラデーション可視化を行う。

STRUCTURE:
    1. Imports & Setup: 外部ライブラリの集約、およびロガー・設定の最速初期化
    2. Helpers        : .envローダー、セキュアなパスワード入力（ terimos / msvcrt ）
    3. Authentication : garminconnect / garth によるクラウドセッション管理
    4. Analytics      : FIT パース、座標変換、最高速地点ベースのラップ判定
    5. Visualization  : matplotlib 補完による速度マップ (folium) 生成
    6. Output         : Lap_result フォルダへ CSV / HTML マップ / ログを出力

USAGE:
    uv run python LapMonitor_v5.04.py

CHANGELOG:
    v5.04 (2026-05-21)
        [Fix] import文をすべてファイル最上部に集約（コード構造の健全化）
        [Fix] loggerの初期化を最上部に移動し、secure_input()内での前方参照エラーを解消
        [Fix] garminconnectのconnectapi()がgarthのパス指定と互換性がない問題を修正
              (garminconnect使用時はフルURLを組み立ててリクエストを行うようラッパーを改善)
        [Fix] ドキュメント内の「对話入力」（簡体字）を「対話入力」に修正
    v5.03 (2026-05-14)
        [Fix] コンソール出力の文字化けを修正（stdout を UTF-8 に強制設定）
        [Fix] tqdm の挙動によるコンソールの「1」表示を完全に抑制するため、
              プログレスバー処理をダミー関数へ置き換え
============================================================
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import logging
import math
import os
import shutil
import sys
import webbrowser
import zipfile
from getpass import getpass
from pathlib import Path
from typing import Any

# サードパーティ製ライブラリのインポート
import fitparse
import folium
import matplotlib.colors as mcolors
from branca.element import MacroElement, Template

# garth は非推奨のため garminconnect を優先使用し、なければ garth にフォールバック
try:
    from garminconnect import Garmin as _GarminConnect
    _USE_GARMINCONNECT = True
except ImportError:
    _USE_GARMINCONNECT = False
    try:
        import garth
    except ImportError:
        raise ImportError(
            "Garmin 連携ライブラリが見つかりません。\n"
            "推奨: uv add garminconnect\n"
            "代替: uv add garth"
        )


# ---------------------------------------------------------------------------
# 1. 設定・ロガーの最優先初期化（前方参照エラー防止）
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "VERSION": "5.04",
    "LAP_LINE_WIDTH_M": 8.0,
    "MIN_LAP_DIST_M": 50.0,
    "SEARCH_DAYS": 7,
    "OUTPUT_DIR": "Lap_result",
    "SESSION_PATH": "./.garth_session",
    "COLOR_POINTS": ["#000080", "#0000FF", "#FFFF00", "#FF0000", "#800000"],
    "SPEED_POINTS": [0, 15, 30, 45, 60],
}

CONFIG_FILE = "config.json"


def _generate_default_config(path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=4)
    print(f"[Config] デフォルト設定を生成しました: {path}")


def _validate_config(conf_dict: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("LAP_LINE_WIDTH_M", "MIN_LAP_DIST_M"):
        if not isinstance(conf_dict.get(key), (int, float)):
            errors.append(f"{key} は数値である必要があります（現在値: {conf_dict.get(key)}）")
        elif conf_dict[key] <= 0:
            errors.append(f"{key} は正の値である必要があります（現在値: {conf_dict[key]}）")

    if not isinstance(conf_dict.get("SEARCH_DAYS"), int):
        errors.append(f"SEARCH_DAYS は整数である必要があります（現在値: {conf_dict.get('SEARCH_DAYS')}）")
    elif conf_dict["SEARCH_DAYS"] < 1:
        errors.append(f"SEARCH_DAYS は 1 以上である必要があります（現在値: {conf_dict['SEARCH_DAYS']}）")

    cp = conf_dict.get("COLOR_POINTS", [])
    sp = conf_dict.get("SPEED_POINTS", [])
    if not isinstance(cp, list) or not isinstance(sp, list):
        errors.append("COLOR_POINTS と SPEED_POINTS はリストである必要があります")
    elif len(cp) != len(sp):
        errors.append(f"COLOR_POINTS ({len(cp)}) と SPEED_POINTS ({len(sp)}) の要素数が一致しません")
    elif len(sp) < 2:
        errors.append("SPEED_POINTS は 2 要素以上必要です")
    elif max(sp) <= 0:
        errors.append("SPEED_POINTS の最大値は正の値である必要があります")
    return errors


def load_config(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        _generate_default_config(path)
        return DEFAULT_CONFIG.copy()
    try:
        with open(path, "r", encoding="utf-8") as f:
            user_conf: dict[str, Any] = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[Warning] {path} の読み込みに失敗しました。デフォルト設定を使用します。({e})")
        return DEFAULT_CONFIG.copy()

    merged = DEFAULT_CONFIG.copy()
    merged.update(user_conf)
    errors = _validate_config(merged)
    if errors:
        print("[Warning] config.json に問題があります。デフォルト値で補完します。")
        for err in errors:
            print(f"  - {err}")
        for key in DEFAULT_CONFIG:
            if key not in user_conf:
                merged[key] = DEFAULT_CONFIG[key]
    return merged


def setup_logger(output_dir: str, run_time: datetime.datetime) -> logging.Logger:
    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, f"run_{run_time.strftime('%Y%m%d_%H%M%S')}.log")

    logger_obj = logging.getLogger("LapMonitor")
    logger_obj.setLevel(logging.DEBUG)
    logger_obj.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    stdout = sys.stdout
    if hasattr(stdout, "reconfigure"):
        try:
            stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ch = logging.StreamHandler(stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger_obj.addHandler(ch)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger_obj.addHandler(fh)

    logger_obj.info(f"ログファイル: {log_path}")
    return logger_obj


# --- 初期化実行 ---
conf = load_config(CONFIG_FILE)
VERSION          = conf["VERSION"]
LAP_LINE_WIDTH_M = float(conf["LAP_LINE_WIDTH_M"])
MIN_LAP_DIST_M  = float(conf["MIN_LAP_DIST_M"])
SEARCH_DAYS     = int(conf["SEARCH_DAYS"])
OUTPUT_DIR      = conf["OUTPUT_DIR"]
SESSION_PATH    = conf["SESSION_PATH"]

_RUN_TIME = datetime.datetime.now()
logger = setup_logger(OUTPUT_DIR, _RUN_TIME)


# ---------------------------------------------------------------------------
# 2. ヘルパー関数群（tqdm無効化、.env、セキュア入力）
# ---------------------------------------------------------------------------

def tqdm(iterable: Any, **kwargs: Any) -> Any:
    """コンソールを汚さないよう、tqdm のプログレスバー表示を完全に無効化。"""
    return iterable


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def secure_input(prompt: str = "Password: ") -> str:
    """環境に応じて最適な方法でパスワードを非表示入力する（前方参照エラー解消済み）。"""
    is_windows = sys.platform.startswith("win")

    def _try_msvcrt() -> str | None:
        try:
            import msvcrt
            print(prompt, end="", flush=True)
            chars: list[str] = []
            while True:
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    print()
                    break
                elif ch == "\x03":
                    raise KeyboardInterrupt
                elif ch == "\x08":
                    if chars:
                        chars.pop()
                        print("\b \b", end="", flush=True)
                else:
                    chars.append(ch)
                    print("★", end="", flush=True)
            return "".join(chars)
        except ImportError:
            return None
        except Exception:
            return None

    def _try_termios() -> str | None:
        try:
            import termios
            import tty
            print(prompt, end="", flush=True)
            chars: list[str] = []
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ("\r", "\n"):
                        print()
                        break
                    elif ch == "\x03":
                        raise KeyboardInterrupt
                    elif ch in ("\x7f", "\x08"):
                        if chars:
                            chars.pop()
                            print("\b \b", end="", flush=True)
                    else:
                        chars.append(ch)
                        print("★", end="", flush=True)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            return "".join(chars)
        except Exception:
            return None

    def _try_getpass() -> str | None:
        try:
            return getpass(prompt) or None
        except Exception:
            return None

    if is_windows:
        methods = [_try_msvcrt, _try_getpass, _try_termios]
    else:
        methods = [_try_termios, _try_getpass]

    for method in methods:
        result = method()
        if result is not None:
            return result

    logger.warning("パスワードの非表示化に失敗しました。入力内容が画面に表示されます。")
    return input(prompt)


# ---------------------------------------------------------------------------
# 3. Garmin クラウド認証 & 通信制御（garminconnect 互換性修正）
# ---------------------------------------------------------------------------

_garmin_client = None   # garminconnect.Garmin インスタンス


def _garmin_get(path: str) -> Any:
    """garminconnect と garth の仕様差分を吸収してAPIを叩くラッパー。"""
    global _garmin_client
    if _USE_GARMINCONNECT:
        # garminconnectのconnectapi()はベースURLを含まないフルパス、またはフルのURLを期待するケースがあるため、
        # 安全のためにドメインを含めたフルURLでリクエストを行います。
        url = f"https://connect.garmin.com{path}"
        return _garmin_client.connectapi(url)
    else:
        return garth.connectapi(path)


def _garmin_download(path: str) -> bytes:
    """garminconnect と garth の仕様差分を吸収してダウンロードを行うラッパー。"""
    global _garmin_client
    if _USE_GARMINCONNECT:
        url = f"https://connect.garmin.com{path}"
        return _garmin_client.download(url)
    else:
        return garth.download(path)


def authenticate_garmin() -> None:
    """Garmin セッションを復元または新規ログインする。"""
    global _garmin_client
    _load_dotenv()

    email    = os.environ.get("GARMIN_EMAIL", "").strip()
    password = os.environ.get("GARMIN_PASSWORD", "").strip()

    if email and password:
        logger.info(".env / 環境変数からログイン情報を取得しました。")

    if _USE_GARMINCONNECT:
        # ── garminconnect ライブラリ ──────────────────────────────
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                if not email:
                    email = input("Email: ").strip()
                if not password:
                    print("[Info] パスワードは ★ 表示で入力できます。")
                    password = secure_input("Password: ")
                client = _GarminConnect(email, password)
                client.login()
                _garmin_client = client
                logger.info("Garmin ログイン完了 (garminconnect)。")
                break
            except Exception as e:
                last_err = e
                logger.warning(f"ログイン失敗 ({attempt}/3): {e}")
                if attempt < 3:
                    print("メールアドレスとパスワードを再入力してください。")
                    email = password = ""
        else:
            raise RuntimeError(f"Garmin ログインに3回失敗しました: {last_err}")
    else:
        # ── garth ライブラリ（フォールバック）────────────────────
        if os.path.exists(SESSION_PATH):
            try:
                garth.resume(SESSION_PATH)
                logger.info("Garmin セッションを復元しました (garth)。")
                return
            except Exception as e:
                logger.warning(f"セッション復元失敗。再ログインします。({e})")
                try:
                    if os.path.isfile(SESSION_PATH):
                        os.remove(SESSION_PATH)
                    elif os.path.isdir(SESSION_PATH):
                        shutil.rmtree(SESSION_PATH)
                except OSError as rm_err:
                    logger.debug(f"セッションファイル削除失敗（続行）: {rm_err}")

        last_err = None
        for attempt in range(1, 4):
            try:
                if not email:
                    email = input("Email: ").strip()
                if not password:
                    print("[Info] パスワードは ★ 表示で入力できます。")
                    password = secure_input("Password: ")
                garth.login(email, password)
                garth.save(SESSION_PATH)
                logger.info("Garmin ログイン完了 (garth)。")
                break
            except Exception as e:
                last_err = e
                logger.warning(f"ログイン失敗 ({attempt}/3): {e}")
                if attempt < 3:
                    print("メールアドレスとパスワードを再入力してください。")
                    email = password = ""
        else:
            raise RuntimeError(f"Garmin ログインに3回失敗しました: {last_err}")

    # .env 自動生成の提案
    if not os.path.exists(".env") and email and password:
        print()
        print("[提案] .env にログイン情報を保存すると次回から入力不要になります。")
        ans = input("  .env ファイルを生成しますか？ [y/N]: ").strip().lower()
        if ans == "y":
            with open(".env", "w", encoding="utf-8") as ef:
                ef.write(f"GARMIN_EMAIL={email}\n")
                ef.write(f"GARMIN_PASSWORD={password}\n")
            print("[Success] .env ファイルを作成しました。次回から自動ログインします。")


# ---------------------------------------------------------------------------
# 4. カラーマップ & 幾何学計算演算
# ---------------------------------------------------------------------------

_MAX_SPEED = max(conf["SPEED_POINTS"]) if max(conf["SPEED_POINTS"]) > 0 else 1
_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "speed_map_v500",
    list(zip([p / _MAX_SPEED for p in conf["SPEED_POINTS"]], conf["COLOR_POINTS"])),
)


def get_speed_color(speed_kmh: float) -> str:
    norm_speed = min(max(speed_kmh, 0) / _MAX_SPEED, 1.0)
    return mcolors.to_hex(_CMAP(norm_speed))


class LocalCoord:
    def __init__(self, origin_lat: float, origin_lon: float) -> None:
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.lat_scale = 111319.9
        self.lon_scale = 111319.9 * math.cos(math.radians(origin_lat))

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.origin_lon) * self.lon_scale, (lat - self.origin_lat) * self.lat_scale

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        return self.origin_lat + y / self.lat_scale, self.origin_lon + x / self.lon_scale


def get_unit_vector(p_start: dict[str, float], p_end: dict[str, float]) -> tuple[tuple[float, float], float]:
    vx = p_end["x"] - p_start["x"]
    vy = p_end["y"] - p_start["y"]
    length = math.hypot(vx, vy)
    if length > 0.001:
        return (vx / length, vy / length), length
    return (0.0, 0.0), 0.0


def get_intersection_ratio(p1: dict[str, float], p2: dict[str, float], l1: tuple[float, float], l2: tuple[float, float]) -> float | None:
    x1, y1 = p1["x"], p1["y"]
    x2, y2 = p2["x"], p2["y"]
    x3, y3 = l1
    x4, y4 = l2
    denom = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)
    if denom == 0:
        return None
    ua = ((x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)) / denom
    ub = ((x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)) / denom
    if 0.0 <= ua <= 1.0 and 0.0 <= ub <= 1.0:
        return ua
    return None


# ---------------------------------------------------------------------------
# 5. 可視化・マップパネル UI 構築
# ---------------------------------------------------------------------------

def _build_lap_rows_html(csv_rows: list[dict[str, Any]], best_lap_num: int) -> str:
    rows: list[str] = []
    for r in csv_rows:
        style = "color:red;font-weight:bold;" if r["Virtual_Lap"] == best_lap_num else ""
        rows.append(
            f'<tr style="{style}">'
            f'<td>L{r["Virtual_Lap"]}</td>'
            f'<td>{r["Total_Time"]}s</td>'
            f'<td>{r["Max_Speed"]}k</td>'
            f"</tr>"
        )
    return "".join(rows)


def add_custom_panels(m: folium.Map, csv_rows: list[dict[str, Any]], best_lap_num: int, session_datetime: str) -> None:
    laps_html = _build_lap_rows_html(csv_rows, best_lap_num)
    gradient_css = f"linear-gradient(to top, {', '.join(conf['COLOR_POINTS'])})"

    lap_panel = (
        '<div style="position:fixed;top:10px;right:10px;width:180px;max-height:300px;'
        'background-color:white;border:2px solid black;z-index:9999;font-size:12px;'
        'padding:10px;opacity:0.9;overflow-y:auto;">'
        "<b>LAP DATA</b><br>"
        f'<span style="font-size:10px;color:#666;">{session_datetime}</span>'
        '<table style="width:100%;border-collapse:collapse;margin-top:5px;text-align:left;">'
        '<tr style="border-bottom:1px solid #ccc;"><th>Lap</th><th>Time</th><th>Max</th></tr>'
        f"{laps_html}"
        "</table></div>"
    )

    legend_panel = (
        f'<div style="position:fixed;bottom:30px;left:10px;width:140px;'
        f"background:{gradient_css};"
        'border:2px solid black;z-index:9999;font-size:11px;padding:0;opacity:0.9;'
        'text-align:center;color:white;text-shadow:1px 1px 1px black;">'
        '<div style="padding:5px;">'
        "<b>Speed Map</b><br>"
        '60+ <br><div style="height:25px;"></div>'
        '45 <br><div style="height:25px;"></div>'
        '30 (Yellow)<br><div style="height:25px;"></div>'
        '15 <br><div style="height:25px;"></div>'
        "0 km/h"
        "</div></div>"
    )

    template = "{% macro html(this, kwargs) %}" + lap_panel + legend_panel + "{% endmacro %}"
    macro = MacroElement()
    macro._template = Template(template)
    m.get_root().add_child(macro)


# ---------------------------------------------------------------------------
# 6. データ出力 & FIT 解析ロジック
# ---------------------------------------------------------------------------

def save_to_csv(csv_rows: list[dict[str, Any]], start_time: datetime.datetime) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    file_name = f"result_{start_time.strftime('%Y%m%d_%H%M%S')}.csv"
    path = os.path.join(OUTPUT_DIR, file_name)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Virtual_Lap", "Total_Time", "Max_Speed"])
        writer.writeheader()
        writer.writerows(csv_rows)
    logger.info(f"CSV 保存: {path}")
    return os.path.abspath(path)


def _extract_fit_bytes(raw_bytes: bytes) -> bytes:
    if not raw_bytes.startswith(b"PK\x03\x04"):
        return raw_bytes
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
        for info in z.infolist():
            if info.filename.lower().endswith(".fit"):
                return z.read(info)
    raise ValueError("ZIP ファイル内に .fit ファイルが見つかりませんでした。")


def _parse_records(fitfile: fitparse.FitFile) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for r in fitfile.get_messages("record"):
        values = r.get_values()
        if "position_lat" not in values or "position_long" not in values:
            continue
        speed_raw = values.get("enhanced_speed") or values.get("speed") or 0.0
        records.append({
            "lat"      : values["position_lat"] * (180.0 / 2**31),
            "lon"      : values["position_long"] * (180.0 / 2**31),
            "dist"     : values.get("distance", 0.0),
            "time"     : values["timestamp"] + datetime.timedelta(hours=9),
            "speed_kmh": speed_raw * 3.6,
        })
    return records


def _find_max_speed_index(records: list[dict[str, Any]]) -> int:
    max_idx, max_speed = 0, -1.0
    for i, r in enumerate(records):
        if r["speed_kmh"] > max_speed:
            max_speed = r["speed_kmh"]
            max_idx = i
    return max(max_idx, 1)


def analyze_fit_data(fit_data_bytes: bytes) -> dict[str, Any] | None:
    tmp_fit = "temp_activity.fit"
    try:
        fit_bytes = _extract_fit_bytes(fit_data_bytes)
        with open(tmp_fit, "wb") as f:
            f.write(fit_bytes)

        fitfile = fitparse.FitFile(tmp_fit)

        session_msg = next(fitfile.get_messages("session"), None)
        if session_msg:
            start_time_jst = session_msg.get_value("start_time") + datetime.timedelta(hours=9)
        else:
            start_time_jst = datetime.datetime.now()
        dt_str  = start_time_jst.strftime("%Y%m%d_%H%M%S")
        disp_dt = start_time_jst.strftime("%Y/%m/%d %H:%M:%S")

        all_records = _parse_records(fitfile)
        if len(all_records) < 10:
            logger.warning("レコード数が不足しているためスキップします。")
            return None

        conv = LocalCoord(all_records[0]["lat"], all_records[0]["lon"])
        for r in all_records:
            r["x"], r["y"] = conv.to_xy(r["lat"], r["lon"])

        max_idx = _find_max_speed_index(all_records)
        unit_v, _ = get_unit_vector(all_records[max_idx - 1], all_records[max_idx])
        mx, my    = all_records[max_idx]["x"], all_records[max_idx]["y"]
        perp      = (-unit_v[1], unit_v[0])
        p1 = (mx + perp[0] * LAP_LINE_WIDTH_M, my + perp[1] * LAP_LINE_WIDTH_M)
        p2 = (mx - perp[0] * LAP_LINE_WIDTH_M, my - perp[1] * LAP_LINE_WIDTH_M)

        crossing_points: list[dict[str, Any]] = []
        last_cross_dist = -999.0
        for i in range(len(all_records) - 1):
            ratio = get_intersection_ratio(all_records[i], all_records[i + 1], p1, p2)
            if ratio is None:
                continue
            seg_dist = all_records[i + 1]["dist"] - all_records[i]["dist"]
            cross_dist = all_records[i]["dist"] + seg_dist * ratio
            if (cross_dist - last_cross_dist) <= MIN_LAP_DIST_M:
                continue
            seg_time = (all_records[i + 1]["time"] - all_records[i]["time"]).total_seconds()
            cross_time = all_records[i]["time"] + datetime.timedelta(seconds=seg_time * ratio)
            crossing_points.append({"index": i, "time": cross_time, "dist": cross_dist})
            last_cross_dist = cross_dist

        if len(crossing_points) < 2:
            logger.warning("ラップラインの通過回数が不足しているためスキップします。")
            return None

        csv_rows: list[dict[str, Any]] = []
        best_time    = float("inf")
        best_lap_num = 0
        for i in range(len(crossing_points) - 1):
            seg_start = crossing_points[i]
            seg_end   = crossing_points[i + 1]
            lap_time  = (seg_end["time"] - seg_start["time"]).total_seconds()
            if lap_time < best_time:
                best_time    = lap_time
                best_lap_num = i + 1
            lap_records = all_records[seg_start["index"]: seg_end["index"] + 2]
            max_speed   = max(r["speed_kmh"] for r in lap_records)
            csv_rows.append({
                "Virtual_Lap": i + 1,
                "Total_Time" : round(lap_time, 3),
                "Max_Speed"  : round(max_speed, 2),
            })

        if csv_rows:
            save_to_csv(csv_rows, start_time_jst)

        m = folium.Map()
        folium.PolyLine(
            [conv.to_latlon(*p1), conv.to_latlon(*p2)],
            color="black", weight=6, opacity=0.8,
        ).add_to(m)

        bounds: list[list[float]] = []
        for i in range(len(all_records) - 1):
            lat_a, lon_a = all_records[i]["lat"],     all_records[i]["lon"]
            lat_b, lon_b = all_records[i + 1]["lat"], all_records[i + 1]["lon"]
            bounds.append([lat_a, lon_a])
            folium.PolyLine(
                [[lat_a, lon_a], [lat_b, lon_b]],
                color=get_speed_color(all_records[i]["speed_kmh"]),
                weight=4, opacity=0.8,
            ).add_to(m)

        m.fit_bounds(bounds)
        add_custom_panels(m, csv_rows, best_lap_num, disp_dt)

        map_path  = os.path.join(OUTPUT_DIR, f"map_{dt_str}.html")
        m.save(map_path)
        full_path = os.path.abspath(map_path)
        logger.info(f"マップ保存: {full_path}")

        try:
            webbrowser.open("file://" + full_path.replace("\\", "/"))
        except Exception:
            pass

        return {
            "start_time": disp_dt,
            "lap_count" : len(csv_rows),
            "best_lap"  : best_lap_num,
            "best_time" : round(best_time, 3),
        }

    except Exception as e:
        logger.error(f"FIT 解析エラー: {e}", exc_info=True)
        return None
    finally:
        if os.path.exists(tmp_fit):
            os.remove(tmp_fit)


# ---------------------------------------------------------------------------
# 7. メイン実行フロー
# ---------------------------------------------------------------------------

def main() -> None:
    """メイン実行フロー。"""
    logger.info(f"--- Garmin Cloud Sync (v{VERSION}) ---")

    try:
        authenticate_garmin()
    except Exception as e:
        logger.critical(f"認証フェーズで致命的なエラーが発生しました: {e}")
        return

    today = datetime.date.today()
    target_dates = [(today - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(SEARCH_DAYS)]

    logger.info(f"過去 {SEARCH_DAYS} 日間のアクティビティを検索中...")

    try:
        activities = _garmin_get("/activitylist-service/activities/search/activities?limit=30")
    except Exception as e:
        logger.critical(f"アクティビティリストの取得に失敗しました: {e}")
        return

    targets = []
    for act in activities:
        start_time_local = act.get("startTimeLocal", "")
        if any(d in start_time_local for d in target_dates):
            try:
                act_time = datetime.datetime.strptime(start_time_local, "%Y-%m-%d %H:%M:%S")
                expected_filepath = os.path.join(OUTPUT_DIR, f"result_{act_time.strftime('%Y%m%d_%H%M%S')}.csv")
                if os.path.exists(expected_filepath):
                    logger.debug(f"既存スキップ ID: {act['activityId']} ({start_time_local})")
                    continue
                targets.append(act)
            except ValueError:
                continue

    if not targets:
        logger.info("処理対象となる新しいアクティビティは見つかりませんでした。")
        return

    logger.info(f"新規アクティビティを {len(targets)} 件検出しました。解析を開始します。")

    summaries: list[dict[str, Any]] = []

    for act in tqdm(targets):
        act_id = act["activityId"]
        act_time = act["startTimeLocal"]
        logger.info(f"--- 処理中 ID: {act_id} ({act_time}) ---")

        try:
            fit_data_bytes = _garmin_download(f"/download-service/files/activity/{act_id}")
            if not fit_data_bytes:
                logger.warning(f"データが空のためスキップします。 ID: {act_id}")
                continue

            res = analyze_fit_data(fit_data_bytes)
            if res:
                summaries.append(res)

        except Exception as e:
            logger.error(f"アクティビティ処理中にエラーが発生しました (ID: {act_id}): {e}")

    if summaries:
        logger.info("========================================")
        logger.info("  処理完了サマリー")
        logger.info("========================================")
        for s in summaries:
            logger.info(f" 日時: {s['start_time']} | 周回数: {s['lap_count']} | Best: Lap {s['best_lap']} ({s['best_time']}s)")
        logger.info("========================================")


if __name__ == "__main__":
    main()