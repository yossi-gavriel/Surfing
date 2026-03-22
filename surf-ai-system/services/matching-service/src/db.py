import json
import os
import numpy as np
from shared.utils.logger import get_logger

logger = get_logger("db")

class UsersDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.users = [] 
        self.load_users()
        
    def load_users(self):
        try:
            if not os.path.exists(self.db_path):
                logger.warning(f"Users database functionally missing at {self.db_path}.")
                return
                
            import fcntl
            with open(self.db_path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                data = json.load(f)
                fcntl.flock(f, fcntl.LOCK_UN)
                
            loaded = []
            for user in data.get("users", []):
                loaded.append({
                    "user_id": user["user_id"],
                    "embedding": np.array(user["embedding"], dtype=np.float32)
                })
            self.users = loaded
        except Exception as e:
            logger.error(f"Error parsing database logical artifacts natively: {e}")

    def get_all_users(self):
        self.load_users()
        return self.users
