import pyaudio
import numpy as np

# --- 設定 ---
CHUNK = 4096
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 48000  # 48000に上げたほうが安定することが多いです

p = pyaudio.PyAudio()

try:
    stream = p.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK)
except Exception as e:
    print(f"ストリーム開始エラー: {e}")
    exit()

print(f"解析中... (最大測定可能周波数: {RATE/2} Hz)")
print("音量レベルとピーク周波数を表示します。終了は Ctrl+C")

try:
    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        astream = np.frombuffer(data, dtype="int16")
        
        # 音量の計算（マイクが機能しているか確認用）
        rms = np.sqrt(np.mean(astream.astype(float)**2))
        
        # FFT解析
        fft_data = np.abs(np.fft.fft(astream))
        freqList = np.fft.fftfreq(CHUNK, d=1.0/RATE)
        
        peak_idx = np.argmax(fft_data[:CHUNK//2])
        peak_freq = abs(freqList[peak_idx]) # 絶対値で取得
        
        # 出力を強化：音量(RMS)と周波数を同時に表示
        # RMSが10以上なら何か音を拾っています
        print(f"音量: {rms:7.2f} | ピーク周波数: {peak_freq:8.2f} Hz", end="\r")

except KeyboardInterrupt:
    print("\n停止しました。")
finally:
    stream.stop_stream()
    stream.close()
    p.terminate()