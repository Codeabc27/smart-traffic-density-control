"""In-memory latest-frame transport for the existing video-processing pipeline.

Only the latest JPEG frame per active/recent job is retained. No YOLO inference is
performed here; the processor publishes the already-annotated frame it produced.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class FrameSnapshot:
    sequence: int
    jpeg: bytes
    timestamp: float


class FrameHub:
    def __init__(self, max_jobs: int = 12) -> None:
        self.max_jobs = max_jobs
        self._lock = threading.RLock()
        self._frames: "OrderedDict[str, FrameSnapshot]" = OrderedDict()
        self._sequences: dict[str, int] = {}

    def publish(self, job_id: str, frame: np.ndarray) -> Optional[int]:
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            return None
        with self._lock:
            sequence = self._sequences.get(job_id, 0) + 1
            self._sequences[job_id] = sequence
            self._frames[job_id] = FrameSnapshot(sequence, encoded.tobytes(), time.time())
            self._frames.move_to_end(job_id)
            while len(self._frames) > self.max_jobs:
                old_job, _ = self._frames.popitem(last=False)
                self._sequences.pop(old_job, None)
            return sequence

    def snapshot(self, job_id: str) -> Optional[FrameSnapshot]:
        with self._lock:
            snapshot = self._frames.get(job_id)
            if snapshot:
                self._frames.move_to_end(job_id)
            return snapshot

    def clear(self, job_id: str) -> None:
        with self._lock:
            self._frames.pop(job_id, None)
            self._sequences.pop(job_id, None)


frame_hub = FrameHub()
