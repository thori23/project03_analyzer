import os
import ollama

# WSL2からWindows側のOllamaを見に行くための設定
# localhostで繋がらない場合は、ここを 'http://172.xx.xx.xx:11434'（WindowsのIP）に書き換えます
os.environ["OLLAMA_HOST"] = "http://localhost:11434"

def ask_gemma_with_image(image_path, instruction):
    try:
        # 1. 画像ファイルをバイナリモード（'rb'）で読み込み
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"ファイル '{image_path}' が見つかりません。同じフォルダにあるか確認してください。")
            
        with open(image_path, 'rb') as f:
            image_data = f.read()

        print(f"--- '{image_path}' を読み込みました。Gemma 4 で画像解析中... ---")

        # 2. Ollama (Gemma 4) に画像付きで問い合わせ
        response = ollama.chat(
            model='gemma4:26b',  # お手元の正確なモデル名（gemma4:12bなど）に合わせてください
            messages=[
                {
                    'role': 'user',
                    'content': instruction,
                    'images': [image_data]  # ここに画像の生データを配列で渡します
                }
            ]
        )

        # 3. 結果の表示
        print("\n--- AIからの回答 ---")
        print(response['message']['content'])

    except FileNotFoundError as e:
        print(f"エラー: {e}")
    except Exception as e:
        print(f"エラーが発生しました: {e}")

# --- 実行部分 ---
if __name__ == "__main__":
    # 解析したい画像ファイル名
    target_image = "2026-07-01 232533.png"  
    
    # AIへの指示（画像を見ながら答えてもらう内容）
    user_instruction = "いま一緒に添付して送った画像をしっかり見て、その内容を読み取り、主要なポイントを3行で要約してください。必ず日本語で回答してください。"

    ask_gemma_with_image(target_image, user_instruction)