import cv2
from insightface.app import FaceAnalysis

class FaceDetector:
    def __init__(self, model_name="buffalo_s", ctx_id=-1):
        self.app = FaceAnalysis(name=model_name, providers=['CPUExecutionProvider'])
        self.app.prepare(ctx_id=ctx_id, det_size=(640, 640))
        
    def detect(self, img):
        return self.app.get(img)

    def check_pose(self, face, max_yaw=30.0, max_pitch=30.0):
        """
        Natively verifies topological facial orientation angles explicitly purging 
        off-axis angles utilizing yaw and pitch validation against model structures.
        """
        if hasattr(face, 'pose') and face.pose is not None:
            pitch, yaw, roll = face.pose
            if abs(pitch) > max_pitch or abs(yaw) > max_yaw:
                return False
        return True

    def get_blur_score(self, img, bbox):
        x1, y1, x2, y2 = [int(v) for v in bbox]
        face_crop = img[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if face_crop.size == 0: 
            return 0.0
            
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        variance = cv2.Laplacian(gray, cv2.CV_64F).var()
        return variance
