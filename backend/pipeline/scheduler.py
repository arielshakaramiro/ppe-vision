import threading
import time
import logging

log = logging.getLogger("ppe.scheduler")


class InferenceScheduler:
    def __init__(self, detector, cameras, lock=None):
        self.detector = detector
        self._cameras = {c.camera_id: c for c in cameras}
        self._camera_lock = threading.Condition()
        self._in_flight: dict[int, int] = {}
        # Lock opsional yang dibagi dengan worker analisis rekaman, supaya model tidak dipanggil
        # dari dua thread sekaligus (ultralytics predict tidak dijamin aman-thread).
        self.lock = lock
        self.running = False
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="scheduler")

    def add_camera(self, camera):
        with self._camera_lock:
            if camera.camera_id in self._cameras:
                raise ValueError(f"Kamera {camera.camera_id} masih terdaftar")
            self._cameras[camera.camera_id] = camera

    def remove_camera(self, camera_id: str):
        """Lepas kamera dan tunggu batch yang masih memakai sesinya selesai."""
        with self._camera_lock:
            camera = self._cameras.pop(camera_id, None)
            if camera is not None:
                while self._in_flight.get(id(camera), 0):
                    self._camera_lock.wait()
            return camera

    def start(self):
        self.running = True
        self.thread.start()

    def stop(self):
        self._stop.set()
        self.running = False

    def _run(self):
        log.info("InferenceScheduler started")
        while not self._stop.is_set():
            with self._camera_lock:
                active_cams = [c for c in self._cameras.values() if c.reader and c.reader.running]
                for c in active_cams:
                    key = id(c)
                    self._in_flight[key] = self._in_flight.get(key, 0) + 1
            try:
                if not active_cams:
                    self._stop.wait(0.1)
                    continue

                frames = []
                cams_with_frames = []
                for c in active_cams:
                    f = c.reader.read()
                    if f is not None:
                        frames.append(f)
                        cams_with_frames.append(c)

                if not frames:
                    self._stop.wait(0.01)
                    continue

                run_ppe = any(c.needs_ppe() for c in cams_with_frames)
                t0 = time.time()
                try:
                    if self.lock:
                        with self.lock:
                            pose_res, ppe_res = self.detector.batch_predict(frames, run_ppe)
                    else:
                        pose_res, ppe_res = self.detector.batch_predict(frames, run_ppe)
                    t_batch = time.time() - t0
                except Exception:
                    log.exception("Batch predict error")
                    self._stop.wait(0.1)
                    continue

                for i, c in enumerate(cams_with_frames):
                    try:
                        c.process(frames[i], pose_res[i], ppe_res[i] if run_ppe else None, t_batch)
                    except Exception:
                        log.exception("Gagal memproses kamera %s", c.camera_id)
            finally:
                with self._camera_lock:
                    for c in active_cams:
                        key = id(c)
                        count = self._in_flight[key] - 1
                        if count:
                            self._in_flight[key] = count
                        else:
                            del self._in_flight[key]
                    self._camera_lock.notify_all()
