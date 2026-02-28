import pyaudio
import numpy as np
import matplotlib.pyplot as plt

# --- 設定 ---
CHUNK = 4096
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 48000  # ChromeOSで安定する48kHz

p = pyaudio.PyAudio()
stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

# グラフの準備
plt.ion() # 対話モードON
fig, ax = plt.subplots()
x = np.fft.fftfreq(CHUNK, d=1.0/RATE)[:CHUNK//2]
line, = ax.plot(x, np.zeros(CHUNK//2))

ax.set_xlim(0, 24000)      # 0Hzから24kHzまで表示
ax.set_ylim(0, 5000)       # 縦軸（強さ）は環境に合わせて調整
ax.set_xlabel("Frequency (Hz)")
ax.set_ylabel("Amplitude")
ax.set_title("Real-time Spectrum Analyzer")

print("グラフを表示中... 終了するにはグラフのウィンドウを閉じるか、Ctrl+C")

try:
    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        astream = np.frombuffer(data, dtype="int16")
        
        # FFT解析
        fft_data = np.abs(np.fft.fft(astream))[:CHUNK//2]
        
        # グラフ更新
        line.set_ydata(fft_data)
        
        # ピーク周波数の特定と表示
        peak_freq = x[np.argmax(fft_data)]
        ax.set_title(f"Peak Frequency: {peak_freq:.2f} Hz")
        
        plt.pause(0.01) # 描画更新

except KeyboardInterrupt:
    print("停止しました。")
finally:
    stream.stop_stream()
    stream.close()
    p.terminate()