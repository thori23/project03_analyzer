"""
診断スクリプト: garminconnect の正しい API 呼び出し方を確認する
実行: uv run python check_garmin_api.py
"""
import os, sys

# .env 読み込み
def _load_dotenv(path=".env"):
    if not os.path.exists(path): return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ: os.environ[k] = v

_load_dotenv()

try:
    from garminconnect import Garmin
except ImportError:
    print("garminconnect が見つかりません: uv add garminconnect")
    sys.exit(1)

email    = os.environ.get("GARMIN_EMAIL", "")
password = os.environ.get("GARMIN_PASSWORD", "")
if not email or not password:
    email    = input("Email: ")
    password = input("Password: ")

print(f"\n[1] ログイン中: {email}")
client = Garmin(email, password)
client.login()
print("    ログイン成功")

# ── パターンA: パスのみ（正しい形式）──
print("\n[2] パターンA: パスのみ")
try:
    path_a = "/activitylist-service/activities/search/activities"
    result = client.connectapi(path_a, params={"limit": 5})
    print(f"    成功: {len(result)} 件取得")
    print(f"    最初のキー: {list(result[0].keys())[:5] if result else 'なし'}")
except Exception as e:
    print(f"    失敗: {e}")

# ── パターンB: garminconnect 専用メソッド ──
print("\n[3] パターンB: get_activities() メソッド")
try:
    acts = client.get_activities(0, 5)
    print(f"    成功: {len(acts)} 件取得")
    if acts:
        a = acts[0]
        print(f"    最初のアクティビティ: {a.get('startTimeLocal')} / {a.get('activityName')}")
except Exception as e:
    print(f"    失敗: {e}")

# ── パターンC: 別パス ──
print("\n[4] パターンC: modern-enhancements-service")
try:
    path_c = "/activitylist-service/activities/search/activities"
    result = client.connectapi(path_c, params={"limit": 3, "start": 0})
    print(f"    成功: {len(result)} 件")
except Exception as e:
    print(f"    失敗: {e}")

print("\n診断完了。結果を共有してください。")
