import math
import json

def calculate_iou(box1, box2):
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    
    inter_area = max(0, x2 - x1) * max(0, y2 - y1)
    if inter_area == 0:
        return 0.0
        
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union_area = area1 + area2 - inter_area
    
    return inter_area / float(union_area) if union_area > 0 else 0.0

class IoUTracker:
    def __init__(self, prefix_id: str, camera_id: str, redis_client=None, iou_threshold=0.3, center_dist_threshold=100.0, max_lost=5, max_active=10, smooth_alpha=0.7, max_speed=50.0, conf_decay=0.9):
        self.prefix_id = prefix_id
        self.camera_id = camera_id
        self.redis_client = redis_client
        self.iou_threshold = iou_threshold
        self.center_dist_threshold = center_dist_threshold
        self.max_lost = max_lost
        self.max_active = max_active
        self.smooth_alpha = smooth_alpha
        self.max_speed = max_speed
        self.conf_decay = conf_decay
        
        self.tracks = self._load_state()
        self.next_id = 1
        
        if self.tracks:
            for tid in self.tracks:
                try:
                    num = int(tid.split("_")[-1])
                    if num >= self.next_id:
                        self.next_id = num + 1
                except ValueError:
                    pass
        
    def _load_state(self):
        if not self.redis_client: return {}
        try:
            data = self.redis_client.get(f"tracker_state_{self.camera_id}")
            if data:
                return json.loads(data)
        except Exception:
            pass
        return {}

    def save_state(self):
        if self.redis_client:
            try:
                self.redis_client.setex(
                    f"tracker_state_{self.camera_id}", 
                    5, 
                    json.dumps(self.tracks)
                )
            except Exception:
                pass

    def _get_center(self, bbox):
        return ((bbox[0] + bbox[2])/2.0, (bbox[1] + bbox[3])/2.0)
        
    def _smooth_bbox(self, old_bbox, new_bbox):
        if old_bbox is None:
            return new_bbox
        return [
            old_bbox[i] * (1.0 - self.smooth_alpha) + new_bbox[i] * self.smooth_alpha
            for i in range(4)
        ]
        
    def _nms_tracks(self):
        tids = list(self.tracks.keys())
        to_delete = set()
        for i in range(len(tids)):
            for j in range(i+1, len(tids)):
                t1, t2 = tids[i], tids[j]
                if t1 in to_delete or t2 in to_delete: continue
                iou = calculate_iou(self.tracks[t1]["bbox"], self.tracks[t2]["bbox"])
                if iou > 0.8:
                    if self.tracks[t1]["lost"] < self.tracks[t2]["lost"]:
                        to_delete.add(t2)
                    elif self.tracks[t2]["lost"] < self.tracks[t1]["lost"]:
                        to_delete.add(t1)
                    else:
                        to_delete.add(t2)
        for tid in to_delete:
            del self.tracks[tid]

    def update(self, detections):
        current_frame_tracks = []
        
        for tid, track in self.tracks.items():
            vx, vy = track["velocity"]
            speed = math.hypot(vx, vy)
            if speed > self.max_speed:
                factor = self.max_speed / float(speed)
                vx *= factor
                vy *= factor
                track["velocity"] = (vx, vy)
                
            if vx != 0 or vy != 0:
                track["predicted_bbox"] = [
                    track["bbox"][0] + vx,
                    track["bbox"][1] + vy,
                    track["bbox"][2] + vx,
                    track["bbox"][3] + vy
                ]
            else:
                track["predicted_bbox"] = track["bbox"]
                
        if not detections:
            tids = list(self.tracks.keys())
            for tid in tids:
                self.tracks[tid]["lost"] += 1
                self.tracks[tid]["conf"] *= self.conf_decay
                if self.tracks[tid]["lost"] > self.max_lost or self.tracks[tid]["conf"] < 0.1:
                    del self.tracks[tid]
            self._nms_tracks()
            return current_frame_tracks
            
        unmatched_dets = []
        
        if not self.tracks:
            for det in detections:
                if len(self.tracks) >= self.max_active:
                    break
                tid = f"{self.prefix_id}_{self.next_id}"
                self.tracks[tid] = {
                    "bbox": det["bbox"], 
                    "predicted_bbox": det["bbox"],
                    "velocity": [0.0, 0.0],
                    "lost": 0,
                    "conf": det["conf"]
                }
                current_frame_tracks.append((tid, det["bbox"], det["conf"]))
                self.next_id += 1
            self._nms_tracks()
            return current_frame_tracks
            
        tids = list(self.tracks.keys())
        used_tracks = set()
        
        for det in detections:
            best_iou = 0
            best_tid = None
            for tid in tids:
                if tid in used_tracks: continue
                iou = calculate_iou(det["bbox"], self.tracks[tid]["predicted_bbox"])
                if iou > best_iou and iou >= self.iou_threshold:
                    best_iou = iou
                    best_tid = tid
                    
            if best_tid is not None:
                used_tracks.add(best_tid)
                old_center = self._get_center(self.tracks[best_tid]["bbox"])
                new_center = self._get_center(det["bbox"])
                vx = new_center[0] - old_center[0]
                vy = new_center[1] - old_center[1]
                
                smoothed_bbox = self._smooth_bbox(self.tracks[best_tid]["bbox"], det["bbox"])
                self.tracks[best_tid]["bbox"] = smoothed_bbox
                self.tracks[best_tid]["velocity"] = [vx, vy]
                self.tracks[best_tid]["lost"] = 0
                self.tracks[best_tid]["conf"] = det["conf"]
                current_frame_tracks.append((best_tid, smoothed_bbox, det["conf"]))
            else:
                unmatched_dets.append(det)

        remaining_dets = []
        for det in unmatched_dets:
            best_dist = float('inf')
            best_tid = None
            det_center = self._get_center(det["bbox"])
            
            for tid in tids:
                if tid in used_tracks: continue
                track_center = self._get_center(self.tracks[tid]["predicted_bbox"])
                dist = math.hypot(det_center[0] - track_center[0], det_center[1] - track_center[1])
                
                if dist < best_dist and dist <= self.center_dist_threshold:
                    best_dist = dist
                    best_tid = tid
                    
            if best_tid is not None:
                used_tracks.add(best_tid)
                old_center = self._get_center(self.tracks[best_tid]["bbox"])
                new_center = self._get_center(det["bbox"])
                vx = new_center[0] - old_center[0]
                vy = new_center[1] - old_center[1]
                
                smoothed_bbox = self._smooth_bbox(self.tracks[best_tid]["bbox"], det["bbox"])
                self.tracks[best_tid]["bbox"] = smoothed_bbox
                self.tracks[best_tid]["velocity"] = [vx, vy]
                self.tracks[best_tid]["lost"] = 0
                self.tracks[best_tid]["conf"] = det["conf"]
                current_frame_tracks.append((best_tid, smoothed_bbox, det["conf"]))
            else:
                remaining_dets.append(det)

        for tid in tids:
            if tid not in used_tracks:
                self.tracks[tid]["lost"] += 1
                self.tracks[tid]["conf"] *= self.conf_decay
                if self.tracks[tid]["lost"] > self.max_lost or self.tracks[tid]["conf"] < 0.1:
                    del self.tracks[tid]
                    
        for det in remaining_dets:
            if len(self.tracks) >= self.max_active:
                break
            tid = f"{self.prefix_id}_{self.next_id}"
            self.tracks[tid] = {"bbox": det["bbox"], "predicted_bbox": det["bbox"], "velocity": [0.0, 0.0], "lost": 0, "conf": det["conf"]}
            current_frame_tracks.append((tid, det["bbox"], det["conf"]))
            self.next_id += 1
            
        self._nms_tracks()
        
        filtered_tracks = [t for t in current_frame_tracks if t[0] in self.tracks]
        return filtered_tracks
