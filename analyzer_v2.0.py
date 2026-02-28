import pyaudio
import numpy as np
import matplotlib.pyplot as plt
import time

# --- 設定 ---
CHUNK = 4096
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 48000
INTERVAL = 2.0  # ★何秒ごとに最大周波数を表示するか（1.0 や 2.0 など任意に設定）

p = pyaudio.PyAudio()
stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

# グラフとデータ保持用の変数
plt.ion()
fig, ax = plt.subplots()
x = np.fft.fftfreq(CHUNK, d=1.0/RATE)[:CHUNK//2]
line, = ax.plot(x, np.zeros(CHUNK//2))
ax.set_ylim(0, 50) # 音圧の表示範囲（環境に合わせて調整してください）
ax.set_xlabel("Frequency [Hz]")
ax.set_ylabel("Amplitude")

last_time = time.time()
peak_amp = 0
peak_freq = 0

print(f"計測開始: {INTERVAL}秒ごとの最大周波数を表示します。")

try:
    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        y = np.frombuffer(data, dtype=np.int16)
        
        # FFT（高速フーリエ変換）で周波数成分を計算
        fft_data = np.abs(np.fft.fft(y))[:CHUNK//2]
        fft_data = fft_data / (CHUNK / 2) # 正規化
        
        # グラフ更新（視覚的な確認用）
        line.set_ydata(fft_data)
        fig.canvas.draw()
        fig.canvas.flush_events()

        # 現在のチャンク内での最大値を探す
        current_max_idx = np.argmax(fft_data)
        current_max_amp = fft_data[current_max_idx]
        
        # 指定秒数内での「最大中の最大」を更新
        if current_max_amp > peak_amp:
            peak_amp = current_max_amp
            peak_freq = x[current_max_idx]

        # 指定した秒数が経過したら表示
        current_time = time.time()
        if current_time - last_time >= INTERVAL:
            # 50km/h以上の速度表示（赤茶色）のルールに則り、
            # ここでは「大きな音（例：振幅10以上）」を強調して表示する遊び心を追加
            color_code = "\033[38;5;88m" if peak_amp > 10 else "" 
            reset_code = "\033[0m"
            
            print(f"[{INTERVAL}秒間の最大値] 周波数: {peak_freq:.1f} Hz (音圧: {color_code}{peak_amp:.2f}{reset_code})")
            
            # 次の計測のためにリセット
            peak_amp = 0
            last_time = current_time

except KeyboardInterrupt:
    print("計測を終了します。")
    stream.stop_stream()
    stream.close()
    p.terminate()