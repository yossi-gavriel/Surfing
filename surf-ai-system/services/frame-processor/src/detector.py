import cv2
from ultralytics import YOLO

class PersonDetector:
    def __init__(self, model_name: str = "yolov8n.pt", min_confidence: float = 0.5, 
                 inference_size: tuple = (640, 640), min_bbox_area: int=500, max_aspect_ratio: float=3.0):
        self.model = YOLO(model_name)
        self.person_class_id = 0
        self.min_confidence = min_confidence
        self.inference_size = inference_size
        self.min_bbox_area = min_bbox_area
        self.max_aspect_ratio = max_aspect_ratio

    def detect(self, frame):
        orig_h, orig_w = frame.shape[:2]
        resized = cv2.resize(frame, self.inference_size)
        
        results = self.model(resized, classes=[self.person_class_id], verbose=False)
        scale_x = orig_w / float(self.inference_size[0])
        scale_y = orig_h / float(self.inference_size[1])
        
        bboxes = []
        for result in results:
            boxes = result.boxes
            for i, box in enumerate(boxes):
                conf = float(box.conf[0])
                if conf < self.min_confidence:
                    continue
                    
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                x1 *= scale_x
                y1 *= scale_y
                x2 *= scale_x
                y2 *= scale_y
                
                width = x2 - x1
                height = y2 - y1
                area = width * height
                
                # Minimum area drop
                if area < self.min_bbox_area:
                    continue
                    
                # Aspect ratio limitation drop
                if width > 0 and height > 0:
                    aspect_ratio = max(width/height, height/width)
                    if aspect_ratio > self.max_aspect_ratio:
                        continue
                else:
                    continue
                    
                bboxes.append({
                    "bbox": [x1, y1, x2, y2],
                    "conf": conf
                })
                
        return bboxes
