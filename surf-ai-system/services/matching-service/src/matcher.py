import numpy as np
from src.config import config
from shared.utils.logger import get_logger

logger = get_logger("matcher")

def cosine_similarity(e1, e2):
    n1 = np.linalg.norm(e1)
    n2 = np.linalg.norm(e2)
    if n1 == 0 or n2 == 0: return 0.0
    return np.dot(e1, e2) / (n1 * n2)

class Matcher:
    def __init__(self, db):
        self.db = db
        
    def match(self, track_id, track_embedding, embedding_confidence):
        """
        Calculates exact analytical arrays verifying strictly against global bounds protecting against erroneous validations.
        """
        # Rule 3c: Embedding Confidence verification
        if embedding_confidence < config.min_emb_confidence:
            logger.info(f"[{track_id}] Rejected: Valid embedding_confidence ({embedding_confidence:.2f}) < {config.min_emb_confidence} requirement.")
            return None
            
        users = self.db.get_all_users()
        if not users:
            logger.debug(f"[{track_id}] Output skipped executing identically 0 physical targets actively cached.")
            return None
            
        target_emb = np.array(track_embedding, dtype=np.float32)
        
        matches = []
        for u in users:
            sim = cosine_similarity(target_emb, u["embedding"])
            matches.append({
                "user_id": u["user_id"],
                "similarity": float(sim)
            })
            
        matches.sort(key=lambda x: x["similarity"], reverse=True)
        top_matches = matches[:3]
        
        best_match = top_matches[0]
        best_score = best_match["similarity"]
        
        logger.debug(f"[{track_id}] Top mapped topological similarities computed: {top_matches}")
        
        # Rule 3a: Absolute minimal alignment score
        if best_score < config.min_best_score:
            logger.info(f"[{track_id}] Rejected: best_score ({best_score:.3f}) geometrically breached absolute limit {config.min_best_score}. Structure: {top_matches}")
            return None
            
        # Rule 3b: Minimum differential separating highest alignments accurately dodging false positives logically
        if len(top_matches) > 1:
            second_best_score = top_matches[1]["similarity"]
            margin = best_score - second_best_score
            if margin < config.min_score_margin:
                logger.info(f"[{track_id}] Rejected: margin interval ({margin:.3f}) mathematically insufficient < {config.min_score_margin} limits. Ambiguity: {best_score:.3f} vs {second_best_score:.3f}")
                return None
                
        # Sub Rule 4: Synthetic aggregate scoring formula logic identically calculated
        final_score = best_score * 0.7 + embedding_confidence * 0.3
        
        # Sub Rule 5: Enumerated explicit grouping bounds
        if final_score > 0.8:
            conf_level = "high"
        elif final_score > 0.7:
            conf_level = "medium"
        else:
            conf_level = "low"
            
        logger.info(f"[{track_id}] Valid match secured: User {best_match['user_id']} | Physical Similarity: {best_score:.3f} | Synthetic Confidence Finality: {final_score:.3f}")
        
        return {
            "user_id": best_match["user_id"],
            "score": final_score,
            "confidence": conf_level
        }
