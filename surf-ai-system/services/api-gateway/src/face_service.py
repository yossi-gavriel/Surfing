import os

import cv2
import numpy as np
from insightface.app import FaceAnalysis

from shared.utils.embeddings import normalize_embedding_vector
from shared.utils.face_preprocessing import preprocess_face, summarize_face_tensor


class FaceUploadError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class FaceUploadService:
    def __init__(self):
        model_name = os.environ.get("INSIGHTFACE_MODEL", "buffalo_s")
        ctx_id = int(os.environ.get("INSIGHTFACE_CTX", "-1"))

        self.min_face_size = int(os.environ.get("MIN_FACE_SIZE", "40"))
        self.min_confidence = float(os.environ.get("MIN_CONFIDENCE", "0.5"))
        self.min_blur_score = float(os.environ.get("MIN_BLUR_SCORE", "50.0"))

        self.face_app = FaceAnalysis(
            name=model_name,
            providers=["CPUExecutionProvider"],
        )
        self.face_app.prepare(ctx_id=ctx_id, det_size=(640, 640))

    def extract_embedding(self, image_bytes: bytes) -> dict[str, float | list[float]]:
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image is None:
            raise FaceUploadError(
                code="bad_image",
                message="Unsupported or invalid image file",
            )

        faces = self.face_app.get(image)
        if len(faces) == 0:
            raise FaceUploadError(
                code="no_face_detected",
                message="No face detected in the uploaded image",
            )
        if len(faces) > 1:
            raise FaceUploadError(
                code="multiple_faces_detected",
                message="Upload an image with exactly one face",
            )

        face = faces[0]
        x1, y1, x2, y2 = [int(value) for value in face.bbox]
        face_size = max(x2 - x1, y2 - y1)
        if face_size < self.min_face_size:
            raise FaceUploadError(
                code="bad_image",
                message="Face is too small. Upload a closer image.",
            )

        if float(face.det_score) < self.min_confidence:
            raise FaceUploadError(
                code="bad_image",
                message="Face quality is too low. Upload a clearer image.",
            )

        blur_score = self._get_blur_score(image, face.bbox)
        if blur_score < self.min_blur_score:
            raise FaceUploadError(
                code="bad_image",
                message="Image is too blurry. Upload a sharper image.",
            )

        processed_face = preprocess_face(
            image,
            bbox=face.bbox,
            kps=getattr(face, "kps", None),
        )
        print(
            {
                "stage": "embedding_input",
                **summarize_face_tensor(processed_face),
            }
        )

        embedding = self._normalize_embedding(face.embedding)
        return {
            "embedding": embedding.tolist(),
            "face_size": float(face_size),
            "blur_score": float(blur_score),
            "det_score": float(face.det_score),
        }

    def _get_blur_score(self, image: np.ndarray, bbox: np.ndarray) -> float:
        x1, y1, x2, y2 = [int(value) for value in bbox]
        face_crop = image[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if face_crop.size == 0:
            return 0.0

        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def _normalize_embedding(self, embedding: np.ndarray) -> np.ndarray:
        normalized = normalize_embedding_vector(embedding)
        if normalized is None:
            raise FaceUploadError(
                code="bad_image",
                message="Could not extract a usable face embedding",
                status_code=422,
            )
        return normalized
