"""Wave + surfer YOLO detector.

Wraps Ultralytics YOLOv8 with the 2-class wave_surfer model.
Source: Surfing-analysis python/detectors/yolo_detector.py (rewritten for production).
"""

from dataclasses import dataclass


@dataclass
class Detection:
    label: str       # "surfer" or "wave"
    confidence: float
    bbox: list[float]  # [x_min, y_min, x_max, y_max]


# Normalize labels from YOLO model to canonical names
LABEL_MAP = {
    "surfing_person": "surfer",
    "surfer": "surfer",
    "person": "surfer",
    "wave": "wave",
}


class WaveSurferDetector:
    """YOLOv8-based detector for surfer and wave detection.

    Model is loaded once at init. Inference runs per-frame.
    """

    def __init__(self, model_path: str, device: str = "cpu", logger=None):
        from ultralytics import YOLO

        self.logger = logger
        self.device = device
        self.model = YOLO(model_path)
        self.class_names = {}

        # Build class name mapping from model metadata
        if hasattr(self.model, "names") and self.model.names:
            self.class_names = {int(k): str(v) for k, v in self.model.names.items()}

        if logger:
            logger.info(
                "WaveSurferDetector loaded: model=%s device=%s classes=%s",
                model_path, device, self.class_names,
            )

    def detect(
        self,
        frame,
        surfer_confidence: float = 0.5,
        wave_confidence: float = 0.3,
    ) -> list[Detection]:
        """Run detection on a single frame.

        Returns list of Detection objects filtered by per-class confidence thresholds.
        """
        results = self.model.predict(
            frame,
            device=self.device,
            verbose=False,
            conf=min(surfer_confidence, wave_confidence),
        )

        detections = []
        if not results or len(results) == 0:
            return detections

        result = results[0]
        if result.boxes is None:
            return detections

        for box in result.boxes:
            cls_id = int(box.cls[0].item()) if box.cls is not None else -1
            conf = float(box.conf[0].item()) if box.conf is not None else 0.0
            xyxy = box.xyxy[0].tolist() if box.xyxy is not None else [0, 0, 0, 0]

            raw_label = self.class_names.get(cls_id, str(cls_id))
            label = LABEL_MAP.get(raw_label.lower(), raw_label.lower())

            # Apply per-class confidence threshold
            if label == "surfer" and conf < surfer_confidence:
                continue
            if label == "wave" and conf < wave_confidence:
                continue
            if label not in ("surfer", "wave"):
                continue

            detections.append(Detection(
                label=label,
                confidence=round(conf, 4),
                bbox=[round(c, 2) for c in xyxy],
            ))

        return detections
