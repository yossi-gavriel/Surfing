import json
import os
import fcntl

class JsonDB:
    def __init__(self, data_dir="/app/data"):
        self.users_file = os.path.join(data_dir, "users.json")
        self.rides_file = os.path.join(data_dir, "rides.json")
        
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
            
        if not os.path.exists(self.users_file):
            with open(self.users_file, "w") as f:
                json.dump({"users": []}, f)
                
        if not os.path.exists(self.rides_file):
            with open(self.rides_file, "w") as f:
                json.dump({"rides": []}, f)
                
    def _read(self, filepath):
        with open(filepath, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
            return data
            
    def _write(self, filepath, data):
        with open(filepath, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(data, f)
            fcntl.flock(f, fcntl.LOCK_UN)

    def add_user(self, user_id, embedding):
        data = self._read(self.users_file)
        data["users"].append({
            "user_id": user_id,
            "embedding": embedding
        })
        self._write(self.users_file, data)
        return user_id

    def add_ride(self, user_id, track_id, score, confidence, bucket):
        data = self._read(self.rides_file)
        # Avoid duplicate assignments securely natively over polling duplications logically
        if not any(r["track_id"] == track_id for r in data["rides"]):
            data["rides"].append({
                "user_id": user_id,
                "track_id": track_id,
                "score": score,
                "confidence": confidence,
                "video_url": f"s3://{bucket}/rides/{track_id}.mp4"
            })
            self._write(self.rides_file, data)

    def get_rides(self, user_id):
        data = self._read(self.rides_file)
        return [r for r in data["rides"] if r["user_id"] == user_id]
