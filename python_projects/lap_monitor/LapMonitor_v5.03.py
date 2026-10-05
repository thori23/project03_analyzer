"""
============================================================
TITLE  : LapMonitor v5.03
PURPOSE: Garmin Connectから走行データを自動取得し、仮想ラップ解析と
         速度変化の5色スムーズグラデーション可視化を行う。

STRUCTURE:
    1. Configuration  : config.json の読み込み・バリデーション・自動生成
    2. Logging        : ファイル＋コンソール同時出力のロガー設定
    3. Authentication : garth によるクラウドセッション管理
    4. Analytics      : FIT パース、座標変換、最高速地点ベースのラップ判定
    5. Visualization  : matplotlib 補完による速度マップ (folium) 生成
    6. Output         : Lap_result フォルダへ CSV / HTML マップ / ログを出力

USAGE:
    uv run python LapMonitor_v5.00.py

NOTES:
    - 初回のみ Garmin のログイン情報を入力（以降はセッション維持）。
    - 30 km/h を黄色 (Yellow) の起点とした直感的な配色を採用。
    - config.json が存在しない場合はデフォルト値で自動生成する。
    - Chromebook 等の Linux 環境ではブラウザが自動起動しない場合がある。
      その際は出力された HTML ファイルを直接開いてください。

CHANGELOG:
    v5.03 (2026-05-14)
        [Fix] コンソール出力の文字化けを修正（stdout を UTF-8 に強制設定）
        [Fix] garth 非推奨警告に対応。garminconnect を優先使用し
              インストールされていない場合のみ garth にフォールバック
        [New] _garmin_get() / _garmin_download() ラッパーで
              garminconnect / garth の差異を吸収
        移行方法: uv add garminconnect
    v5.02 (2026-05-13)
        [Fix] Chromebook(Linux) で getpass がカーソル固まる問題を修正。
              Windows 以外では termios+tty による ★ マスク入力を最優先に変更。
        [Fix] パスワード入力失敗時に最大3回リトライするよう改善。
        [New] .env ファイルが存在しない場合、初回ログイン後に自動生成を提案。
    v5.01 (2026-05-13)
        [Fix] getpass が使えない環境（VS Code / IDE 組み込みターミナル等）向けに
              フォールバック付きパスワード入力関数 secure_input() を実装。
              優先順位: getpass → msvcrt(Windows) → ★マスク表示入力
        [New] .env ファイル（GARMIN_EMAIL / GARMIN_PASSWORD）による
              ログイン情報の事前設定に対応。毎回の手入力を省略可能。
    v5.00 (2026-05-12)
        [New] logging モジュールによるログファイル出力（run_YYYYMMDD_HHMMSS.log）
        [New] config.json が存在しない場合にデフォルト値で自動生成
        [New] config.json の型・値域バリデーション（起動時に問題を検出）
        [New] tqdm による処理進捗バー表示（インストール済みの場合のみ有効）
        [New] 複数アクティビティの一括処理サマリーをログ末尾に出力
        [Fix] config.json のキー欠損時に DEFAULT_CONFIG で補完
        [Fix] SPEED_POINTS 最大値がゼロの場合のゼロ除算を防止
        [Fix] temp_activity.fit を finally ブロックで確実に削除
        [Fix] garth.resume() 失敗時にセッションファイルを削除し再ログイン可能に
        [Perf] 最高速インデックス取得を 2 重走査から enumerate() の 1 回走査に変更
        [Perf] route_coords の冗長なリスト構築を廃止し fit_bounds に統合
        [Refactor] セミコロン区切り複数文をすべて分割
        [Refactor] add_custom_panels() の HTML を変数に分離
        [Refactor] 処理単位ごとに関数を分割（_extract_fit_bytes 等）
        [Refactor] main() 関数を定義してエントリーポイントを明確化
        [Docs] 全関数に docstring・型ヒントを追加
    v4.18 (2026-05-12)
        [Fix] config.json キー欠損時の補完
        [Fix] ゼロ除算防止、tmp 削除 finally 化、セッション再ログイン対応
        [Perf] 最高速インデックス 1 回走査化
        [Refactor] 可読性全般の改善
    v4.17 (2026-03-29)
        初版リリース
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

# ---------------------------------------------------------------------------
# .env ローダー（python-dotenv 非依存・軽量実装）
# ---------------------------------------------------------------------------

def _load_dotenv(path: str = ".env") -> None:
    """
    .env ファイルを読み込み、環境変数に設定する（既存の変数は上書きしない）。
    python-dotenv がない環境でも動作する簡易実装。

    .env の書式例::

        GARMIN_EMAIL=your@email.com
        GARMIN_PASSWORD=yourpassword

    Args:
        path: .env ファイルのパス。存在しない場合は何もしない。
    """
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


# ---------------------------------------------------------------------------
# 安全なパスワード入力（環境依存フォールバック付き）
# ---------------------------------------------------------------------------

def secure_input(prompt: str = "Password: ") -> str:
    """
    環境に応じて最適な方法でパスワードを非表示入力する。

    Chromebook (Linux / Crostini) では getpass が TTY を正しく取得できず
    カーソルが止まって見える問題があるため、Windows 以外では
    termios + tty による ★ マスク表示入力を最優先とする。

    優先順位:
        - Windows : msvcrt → getpass → ★ マスク（termios）→ 通常 input
        - その他  : ★ マスク（termios）→ getpass → 通常 input

    Args:
        prompt: 入力プロンプト文字列。

    Returns:
        入力されたパスワード文字列。
    """
    is_windows = sys.platform.startswith("win")

    def _try_msvcrt() -> str | None:
        """Windows 専用: msvcrt による文字単位マスク入力。"""
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
        """Linux / Mac / Chromebook: termios + tty による ★ マスク入力。"""
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
                    elif ch in ("\x7f", "\x08"):   # Backspace / DEL
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
        """標準ターミナル向け getpass（入力は完全非表示）。"""
        try:
            return getpass(prompt) or None
        except Exception:
            return None

    # --- 実行順序を環境で切り替え ---
    if is_windows:
        methods = [_try_msvcrt, _try_getpass, _try_termios]
    else:
        # Chromebook / Linux / Mac: termios を最優先（★ フィードバックあり）
        methods = [_try_termios, _try_getpass]

    for method in methods:
        result = method()
        if result:
            return result

    # --- 最終手段: 入力が画面に表示されるが動作は保証 ---
    logger.warning("パスワードの非表示化に失敗しました。入力内容が画面に表示されます。")
    return input(prompt)

import fitparse
import folium
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
import matplotlib.colors as mcolors
from branca.element import MacroElement, Template

# tqdm はオプション依存（インストールされていない場合はフォールバック）
try:
    from tqdm import tqdm as _tqdm

    def tqdm(iterable: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return _tqdm(iterable, **kwargs)

except ImportError:
    def tqdm(iterable: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return iterable

# ---------------------------------------------------------------------------
# デフォルト設定
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "VERSION": "5.03",
    "LAP_LINE_WIDTH_M": 8.0,
    "MIN_LAP_DIST_M": 50.0,
    "SEARCH_DAYS": 7,
    "OUTPUT_DIR": "Lap_result",
    "SESSION_PATH": "./.garth_session",
    "COLOR_POINTS": ["#000080", "#0000FF", "#FFFF00", "#FF0000", "#800000"],
    "SPEED_POINTS": [0, 15, 30, 45, 60],
}

CONFIG_FILE = "config.json"

# ---------------------------------------------------------------------------
# 設定の読み込み・バリデーション・自動生成
# ---------------------------------------------------------------------------

def _generate_default_config(path: str) -> None:
    """
    デフォルト設定を config.json として書き出す。

    Args:
        path: 書き出し先のファイルパス。
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=4)
    print(f"[Config] デフォルト設定を生成しました: {path}")


def _validate_config(conf: dict[str, Any]) -> list[str]:
    """
    設定値の型と値域を検証し、問題点のリストを返す。

    Args:
        conf: 検証対象の設定辞書。

    Returns:
        問題点を説明する文字列のリスト。空リストは検証通過を意味する。
    """
    errors: list[str] = []

    # 数値型チェック
    for key in ("LAP_LINE_WIDTH_M", "MIN_LAP_DIST_M"):
        if not isinstance(conf.get(key), (int, float)):
            errors.append(f"{key} は数値である必要があります（現在値: {conf.get(key)}）")
        elif conf[key] <= 0:
            errors.append(f"{key} は正の値である必要があります（現在値: {conf[key]}）")

    if not isinstance(conf.get("SEARCH_DAYS"), int):
        errors.append(f"SEARCH_DAYS は整数である必要があります（現在値: {conf.get('SEARCH_DAYS')}）")
    elif conf["SEARCH_DAYS"] < 1:
        errors.append(f"SEARCH_DAYS は 1 以上である必要があります（現在値: {conf['SEARCH_DAYS']}）")

    # リスト長一致チェック
    cp = conf.get("COLOR_POINTS", [])
    sp = conf.get("SPEED_POINTS", [])
    if not isinstance(cp, list) or not isinstance(sp, list):
        errors.append("COLOR_POINTS と SPEED_POINTS はリストである必要があります")
    elif len(cp) != len(sp):
        errors.append(
            f"COLOR_POINTS ({len(cp)}) と SPEED_POINTS ({len(sp)}) の要素数が一致しません"
        )
    elif len(sp) < 2:
        errors.append("SPEED_POINTS は 2 要素以上必要です")
    elif max(sp) <= 0:
        errors.append("SPEED_POINTS の最大値は正の値である必要があります")

    return errors


def load_config(path: str) -> dict[str, Any]:
    """
    config.json を読み込む。ファイルが存在しない場合は自動生成する。
    キーが不足している場合は DEFAULT_CONFIG で補完し、値域バリデーションを実施する。

    Args:
        path: config.json のパス。

    Returns:
        検証済みの設定辞書。
    """
    if not os.path.exists(path):
        _generate_default_config(path)
        return DEFAULT_CONFIG.copy()

    try:
        with open(path, "r", encoding="utf-8") as f:
            user_conf: dict[str, Any] = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[Warning] {path} の読み込みに失敗しました。デフォルト設定を使用します。({e})")
        return DEFAULT_CONFIG.copy()

    # 欠損キーをデフォルト値で補完
    merged = DEFAULT_CONFIG.copy()
    merged.update(user_conf)

    # バリデーション
    errors = _validate_config(merged)
    if errors:
        print("[Warning] config.json に以下の問題があります。問題のある項目はデフォルト値を使用します。")
        for err in errors:
            print(f"  - {err}")
        # 問題のあるキーのみデフォルト値に戻す
        for key in DEFAULT_CONFIG:
            if key not in user_conf:
                merged[key] = DEFAULT_CONFIG[key]

    return merged


# ---------------------------------------------------------------------------
# ロガーの設定
# ---------------------------------------------------------------------------

def setup_logger(output_dir: str, run_time: datetime.datetime) -> logging.Logger:
    """
    コンソールとファイルに同時出力するロガーを設定する。

    Args:
        output_dir: ログファイルを保存するディレクトリ。
        run_time: 実行開始時刻（ログファイル名に使用）。

    Returns:
        設定済みの Logger インスタンス。
    """
    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, f"run_{run_time.strftime('%Y%m%d_%H%M%S')}.log")

    logger = logging.getLogger("LapMonitor")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    # コンソールハンドラ（INFO 以上）
    # Chromebook / Windows 等で stdout が UTF-8 でない場合に強制変換
    stdout = sys.stdout
    if hasattr(stdout, "reconfigure"):
        try:
            stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ch = logging.StreamHandler(stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # ファイルハンドラ（DEBUG 以上）
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.info(f"ログファイル: {log_path}")
    return logger


# ---------------------------------------------------------------------------
# 設定・ロガーの初期化（モジュールレベル）
# ---------------------------------------------------------------------------

conf = load_config(CONFIG_FILE)

VERSION         = conf["VERSION"]
LAP_LINE_WIDTH_M = float(conf["LAP_LINE_WIDTH_M"])
MIN_LAP_DIST_M  = float(conf["MIN_LAP_DIST_M"])
SEARCH_DAYS     = int(conf["SEARCH_DAYS"])
OUTPUT_DIR      = conf["OUTPUT_DIR"]
SESSION_PATH    = conf["SESSION_PATH"]

_RUN_TIME = datetime.datetime.now()
logger = setup_logger(OUTPUT_DIR, _RUN_TIME)

# ---------------------------------------------------------------------------
# カラーマップ
# ---------------------------------------------------------------------------

_MAX_SPEED = max(conf["SPEED_POINTS"]) if max(conf["SPEED_POINTS"]) > 0 else 1

_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "speed_map_v500",
    list(zip(
        [p / _MAX_SPEED for p in conf["SPEED_POINTS"]],
        conf["COLOR_POINTS"],
    )),
)


def get_speed_color(speed_kmh: float) -> str:
    """
    速度 (km/h) をカラーマップの HEX カラーコードに変換する。

    Args:
        speed_kmh: 速度（km/h）。負の値はゼロとして扱う。

    Returns:
        "#RRGGBB" 形式のカラーコード。
    """
    norm_speed = min(max(speed_kmh, 0) / _MAX_SPEED, 1.0)
    return mcolors.to_hex(_CMAP(norm_speed))


# ---------------------------------------------------------------------------
# 座標変換
# ---------------------------------------------------------------------------

class LocalCoord:
    """緯度経度 ↔ ローカル XY 座標（メートル）の変換クラス。"""

    def __init__(self, origin_lat: float, origin_lon: float) -> None:
        """
        Args:
            origin_lat: 原点の緯度（度）。
            origin_lon: 原点の経度（度）。
        """
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.lat_scale = 111319.9
        self.lon_scale = 111319.9 * math.cos(math.radians(origin_lat))

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        """緯度経度をローカル XY 座標（メートル）に変換する。"""
        return (lon - self.origin_lon) * self.lon_scale, (lat - self.origin_lat) * self.lat_scale

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        """ローカル XY 座標（メートル）を緯度経度に変換する。"""
        return self.origin_lat + y / self.lat_scale, self.origin_lon + x / self.lon_scale


# ---------------------------------------------------------------------------
# ジオメトリ計算
# ---------------------------------------------------------------------------

def get_unit_vector(
    p_start: dict[str, float], p_end: dict[str, float]
) -> tuple[tuple[float, float], float]:
    """
    2 点間の単位ベクトルとユークリッド距離を返す。

    Args:
        p_start: 始点。'x', 'y' キーを持つ辞書。
        p_end  : 終点。'x', 'y' キーを持つ辞書。

    Returns:
        (unit_vector, length) のタプル。長さがほぼゼロの場合は ((0, 0), 0) を返す。
    """
    vx = p_end["x"] - p_start["x"]
    vy = p_end["y"] - p_start["y"]
    length = math.hypot(vx, vy)
    if length > 0.001:
        return (vx / length, vy / length), length
    return (0.0, 0.0), 0.0


def get_intersection_ratio(
    p1: dict[str, float],
    p2: dict[str, float],
    l1: tuple[float, float],
    l2: tuple[float, float],
) -> float | None:
    """
    線分 p1→p2 とラップライン l1→l2 の交差パラメータ (ua) を返す。

    ua は p1→p2 上の交点位置を [0, 1] で表す。
    交差しない場合は None を返す。

    Args:
        p1: 移動線分の始点辞書 ('x', 'y')。
        p2: 移動線分の終点辞書 ('x', 'y')。
        l1: ラップラインの端点1 (x, y)。
        l2: ラップラインの端点2 (x, y)。

    Returns:
        交差パラメータ ua（0〜1）、交差しない場合は None。
    """
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
# 地図パネル
# ---------------------------------------------------------------------------

def _build_lap_rows_html(csv_rows: list[dict[str, Any]], best_lap_num: int) -> str:
    """
    ラップデータをHTMLテーブル行文字列に変換する。ベストラップは赤太字で強調。

    Args:
        csv_rows    : ラップデータのリスト。
        best_lap_num: ベストラップ番号。

    Returns:
        HTML テーブル行の結合文字列。
    """
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


def add_custom_panels(
    m: folium.Map,
    csv_rows: list[dict[str, Any]],
    best_lap_num: int,
    session_datetime: str,
) -> None:
    """
    ラップデータパネルと速度凡例を folium マップに追加する。

    Args:
        m               : 追加先の folium.Map インスタンス。
        csv_rows        : ラップデータのリスト。
        best_lap_num    : ベストラップ番号（赤太字で強調）。
        session_datetime: 表示用のセッション日時文字列。
    """
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
# CSV 出力
# ---------------------------------------------------------------------------

def save_to_csv(csv_rows: list[dict[str, Any]], start_time: datetime.datetime) -> str:
    """
    ラップデータを CSV ファイルとして保存する。

    Args:
        csv_rows  : 保存するラップデータのリスト。
        start_time: セッション開始時刻（ファイル名に使用）。

    Returns:
        保存した CSV ファイルの絶対パス。
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    file_name = f"result_{start_time.strftime('%Y%m%d_%H%M%S')}.csv"
    path = os.path.join(OUTPUT_DIR, file_name)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Virtual_Lap", "Total_Time", "Max_Speed"])
        writer.writeheader()
        writer.writerows(csv_rows)
    logger.info(f"CSV 保存: {path}")
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# FIT 解析
# ---------------------------------------------------------------------------

def _extract_fit_bytes(raw_bytes: bytes) -> bytes:
    """
    ZIP 形式のバイト列から .fit ファイルを取り出す。
    ZIP でない場合はそのまま返す。

    Args:
        raw_bytes: Garmin からダウンロードした生バイト列。

    Returns:
        .fit ファイルのバイト列。

    Raises:
        ValueError: ZIP 内に .fit ファイルが見つからない場合。
    """
    if not raw_bytes.startswith(b"PK\x03\x04"):
        return raw_bytes
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
        for info in z.infolist():
            if info.filename.lower().endswith(".fit"):
                return z.read(info)
    raise ValueError("ZIP ファイル内に .fit ファイルが見つかりませんでした。")


def _parse_records(fitfile: fitparse.FitFile) -> list[dict[str, Any]]:
    """
    FIT ファイルから GPS レコードを抽出し、整形して返す。

    Args:
        fitfile: 解析済みの FitFile インスタンス。

    Returns:
        GPS レコードの辞書リスト。各辞書は lat, lon, dist, time, speed_kmh を持つ。
    """
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
    """
    最高速レコードのインデックスを 1 回の走査で返す。
    インデックス 0 の場合は前点が取れないため 1 に補正する。

    Args:
        records: GPS レコードのリスト。

    Returns:
        最高速レコードのインデックス（最小値 1）。
    """
    max_idx, max_speed = 0, -1.0
    for i, r in enumerate(records):
        if r["speed_kmh"] > max_speed:
            max_speed = r["speed_kmh"]
            max_idx = i
    return max(max_idx, 1)


def analyze_fit_data(fit_data_bytes: bytes) -> dict[str, Any] | None:
    """
    FIT データを解析し、ラップ CSV と速度マップ HTML を出力する。

    Args:
        fit_data_bytes: Garmin からダウンロードした FIT ファイルのバイト列。

    Returns:
        処理結果のサマリー辞書（start_time, lap_count, best_lap, best_time）。
        解析が成立しない場合は None。
    """
    tmp_fit = "temp_activity.fit"
    try:
        fit_bytes = _extract_fit_bytes(fit_data_bytes)
        with open(tmp_fit, "wb") as f:
            f.write(fit_bytes)

        fitfile = fitparse.FitFile(tmp_fit)

        # セッション情報
        session_msg = next(fitfile.get_messages("session"), None)
        if session_msg:
            start_time_jst = session_msg.get_value("start_time") + datetime.timedelta(hours=9)
        else:
            start_time_jst = datetime.datetime.now()
        dt_str  = start_time_jst.strftime("%Y%m%d_%H%M%S")
        disp_dt = start_time_jst.strftime("%Y/%m/%d %H:%M:%S")

        # GPS レコードの解析
        all_records = _parse_records(fitfile)
        if len(all_records) < 10:
            logger.warning("レコード数が不足しているためスキップします。")
            return None

        # ローカル座標変換
        conv = LocalCoord(all_records[0]["lat"], all_records[0]["lon"])
        for r in all_records:
            r["x"], r["y"] = conv.to_xy(r["lat"], r["lon"])

        # ラップライン決定（最高速地点を垂直に横切る線）
        max_idx = _find_max_speed_index(all_records)
        unit_v, _ = get_unit_vector(all_records[max_idx - 1], all_records[max_idx])
        mx, my    = all_records[max_idx]["x"], all_records[max_idx]["y"]
        perp      = (-unit_v[1], unit_v[0])
        p1 = (mx + perp[0] * LAP_LINE_WIDTH_M, my + perp[1] * LAP_LINE_WIDTH_M)
        p2 = (mx - perp[0] * LAP_LINE_WIDTH_M, my - perp[1] * LAP_LINE_WIDTH_M)

        # ラップライン通過点の検出
        crossing_points: list[dict[str, Any]] = []
        last_cross_dist = -999.0
        for i in range(len(all_records) - 1):
            ratio = get_intersection_ratio(all_records[i], all_records[i + 1], p1, p2)
            if ratio is None:
                continue
            seg_dist   = all_records[i + 1]["dist"] - all_records[i]["dist"]
            cross_dist = all_records[i]["dist"] + seg_dist * ratio
            if (cross_dist - last_cross_dist) <= MIN_LAP_DIST_M:
                continue
            seg_time   = (all_records[i + 1]["time"] - all_records[i]["time"]).total_seconds()
            cross_time = all_records[i]["time"] + datetime.timedelta(seconds=seg_time * ratio)
            crossing_points.append({"index": i, "time": cross_time, "dist": cross_dist})
            last_cross_dist = cross_dist

        if len(crossing_points) < 2:
            logger.warning("ラップラインの通過回数が不足しているためスキップします。")
            return None

        # ラップ集計
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

        # 地図の生成
        m = folium.Map()

        # ラップライン（黒線）
        folium.PolyLine(
            [conv.to_latlon(*p1), conv.to_latlon(*p2)],
            color="black", weight=6, opacity=0.8,
        ).add_to(m)

        # 速度カラーでルートを描画（fit_bounds 用に座標も収集）
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

        # HTML 保存
        map_path  = os.path.join(OUTPUT_DIR, f"map_{dt_str}.html")
        m.save(map_path)
        full_path = os.path.abspath(map_path)
        logger.info(f"マップ保存: {full_path}")

        # ブラウザで開く（失敗してもスキップ）
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
# Garmin 認証
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Garmin クライアント（モジュールレベルで保持）
# ---------------------------------------------------------------------------
_garmin_client = None   # garminconnect.Garmin インスタンス


def _garmin_get(path: str) -> Any:
    """
    garminconnect / garth の違いを吸収した API 呼び出しラッパー。

    Args:
        path: Garmin Connect API のパス。

    Returns:
        JSON レスポンスの dict / list。
    """
    global _garmin_client
    if _USE_GARMINCONNECT:
        return _garmin_client.connectapi(path)
    else:
        return garth.connectapi(path)


def _garmin_download(path: str) -> bytes:
    """
    garminconnect / garth の違いを吸収したダウンロードラッパー。

    Args:
        path: Garmin Connect ダウンロード API のパス。

    Returns:
        ダウンロードされたバイト列。
    """
    global _garmin_client
    if _USE_GARMINCONNECT:
        return _garmin_client.download(path)
    else:
        return garth.download(path)


def authenticate_garmin() -> None:
    """
    Garmin セッションを復元または新規ログインする。
    garminconnect を優先し、なければ garth にフォールバックする。

    ログイン情報の取得優先順位:
        1. 環境変数 ``GARMIN_EMAIL`` / ``GARMIN_PASSWORD``
           （.env ファイルまたはシェルで事前に設定）
        2. 対話入力（Email は通常入力、Password は secure_input で非表示）

    Raises:
        RuntimeError: 3回ログインに失敗した場合。
    """
    global _garmin_client
    _load_dotenv()

    # 環境変数からログイン情報を取得
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
            print("  .env を生成しました（平文保存のため取り扱いにご注意ください）。")
            logger.info(".env ファイルを生成しました。")


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> None:
    """メイン処理。Garmin Connect からアクティビティを取得してラップ解析を実行する。"""
    today        = datetime.date.today()
    target_dates = [
        (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(SEARCH_DAYS)
    ]

    logger.info(f"=== LapMonitor v{VERSION} 開始 ===")
    logger.info(f"対象期間: {target_dates[-1]} 〜 {target_dates[0]}")

    try:
        authenticate_garmin()

        activities: list[dict[str, Any]] = _garmin_get(
            "/activitylist-service/activities/search/activities?limit=30"
        )

        # 対象アクティビティのフィルタリング
        targets = [
            act for act in activities
            if any(d in act["startTimeLocal"] for d in target_dates)
        ]
        logger.info(f"対象アクティビティ数: {len(targets)}")

        # 処理サマリー
        results: list[dict[str, Any]] = []
        skipped = 0

        for act in tqdm(targets, desc="Processing activities", unit="act"):
            act_time = datetime.datetime.strptime(act["startTimeLocal"], "%Y-%m-%d %H:%M:%S")
            expected_csv = os.path.join(
                OUTPUT_DIR, f"result_{act_time.strftime('%Y%m%d_%H%M%S')}.csv"
            )

            if os.path.exists(expected_csv):
                logger.debug(f"スキップ（処理済み）: {act['activityId']}")
                skipped += 1
                continue

            logger.info(f"取得中: Activity ID={act['activityId']}")
            fit_data_bytes = _garmin_download(
                f"/download-service/files/activity/{act['activityId']}"
            )
            if not fit_data_bytes:
                logger.warning(f"データが空です: {act['activityId']}")
                continue

            result = analyze_fit_data(fit_data_bytes)
            if result:
                results.append(result)

        # 処理サマリーのログ出力
        logger.info("=" * 50)
        logger.info(f"=== 処理完了サマリー ===")
        logger.info(f"  新規処理: {len(results)} 件 / スキップ（処理済み）: {skipped} 件")
        for r in results:
            logger.info(
                f"  [{r['start_time']}] "
                f"{r['lap_count']} laps, "
                f"Best: Lap{r['best_lap']} ({r['best_time']}s)"
            )
        logger.info("=" * 50)

    except Exception as e:
        logger.critical(f"致命的エラー: {e}", exc_info=True)


if __name__ == "__main__":
    main()
