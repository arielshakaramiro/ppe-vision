import os
import time
import threading
from pathlib import Path

import cv2
import numpy as np


class CameraReader:
    def __init__(self, camera_id: str, source: str, loop: bool = False, realtime: bool = False,
                 base_dir: Path = Path(".")):
        self.camera_id = camera_id
        self.source = source
        self.loop = loop
        self.realtime = realtime
        self.base_dir = base_dir  # untuk path file video relatif; pipeline tidak meng-import app/
        self.is_stream = source.startswith(("rtsp://", "http://", "https://"))
        self._stop = threading.Event()
        self._frame = None
        self._latest_frame = None
        self._frame_seq = 0
        self._latest_seq = 0
        self.last_read_seq: int | None = None
        self._lock = threading.Lock()
        self.fps = 0.0
        self.frames_total = 0   # kumulatif frame yang dibaca dari sumber (untuk hitung % frame dilewati)
        self.running = False
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"cam-{camera_id}")

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()

    def read(self) -> np.ndarray | None:
        with self._lock:
            f = self._frame
            if f is not None:
                self.last_read_seq = self._frame_seq
            self._frame = None
            return f

    def latest(self) -> np.ndarray | None:
        """Kembalikan frame mentah terbaru tanpa mengonsumsinya.

        Scheduler inferensi tetap memakai `read()` agar frame lama dibuang dan hanya frame terbaru
        diproses. Stream live memakai salinan ringan ini supaya tampilan bisa tetap halus walaupun
        inferensi tidak sempat memproses setiap frame.
        """
        with self._lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    def latest_with_seq(self) -> tuple[np.ndarray | None, int | None]:
        """Kembalikan frame mentah terbaru beserta nomor urutnya tanpa mengonsumsinya."""
        with self._lock:
            if self._latest_frame is None:
                return None, None
            return self._latest_frame.copy(), self._latest_seq

    def _run(self):
        self.running = True
        delay = 2.0
        frames_read = 0
        last_time = time.time()

        while not self._stop.is_set():
            src = self.source
            if not self.is_stream and not src.isdigit() and not os.path.isabs(src):
                src = str(self.base_dir / src)

            cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
            if not cap.isOpened():
                self.error = "Sumber video tidak bisa dibuka"
                cap.release()
                if not self.is_stream:
                    break
                self._stop.wait(delay)
                delay = min(delay * 1.5, 30.0)
                continue

            self.error = None
            delay = 2.0

            fps_prop = cap.get(cv2.CAP_PROP_FPS)
            wait_time = 1.0 / fps_prop if fps_prop > 0 else 0.04

            while not self._stop.is_set():
                ret, frame = cap.read()
                if not ret:
                    if self.loop and not self.is_stream:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    break

                with self._lock:
                    self._frame_seq += 1
                    self._frame = frame
                    self._latest_frame = frame
                    self._latest_seq = self._frame_seq

                frames_read += 1
                self.frames_total += 1
                now = time.time()
                if now - last_time >= 1.0:
                    self.fps = frames_read / (now - last_time)
                    frames_read = 0
                    last_time = now

                if self.realtime and not self.is_stream:
                    self._stop.wait(wait_time)

            cap.release()
            if not self.is_stream and not self.loop:
                break

        self.running = False
