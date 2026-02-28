import pyaudio
import numpy as np

# --- 設定 ---
CHUNK = 4096             # 1回に読み込むデータ量
FORMAT = pyaudio.paInt16 # 16bit
CHANNELS = 1             # モノラル
RATE = 44100             # サンプリングレート (48000 や 96000 に変更可)

p = pyaudio.PyAudio()

# ストリームの開始
stream = p.open(format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK)

print(f"解析中... (最大測定可能周波数: {RATE/2} Hz)")

try:
    while True:
        # 音声データの取得
        data = stream.read(CHUNK, exception_on_overflow=False)
        astream = np.frombuffer(data, dtype="int16")
        
        # 高速フーリエ変換 (FFT) で周波数成分に分解
        fft_data = np.abs(np.fft.fft(astream))
        freqList = np.fft.fftfreq(CHUNK, d=1.0/RATE)
        
        # 最も強度の高い周波数を特定
        peak_idx = np.argmax(fft_data[:CHUNK//2])
        peak_freq = freqList[peak_idx]
        
        # 結果を表示 (1000Hz以上を検知した場合に表示するなど調整可)
        if fft_data[peak_idx] > 1000: # 一定以上の音量がある場合
            print(f"ピーク周波数: {peak_freq:.2f} Hz", end="\r")

except KeyboardInterrupt:
    print("\n停止しました。")

# 終了処理
stream.stop_stream()
stream.close()
p.terminate()