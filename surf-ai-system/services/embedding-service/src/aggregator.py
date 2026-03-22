import numpy as np

def cosine_similarity(e1, e2):
    n1 = np.linalg.norm(e1)
    n2 = np.linalg.norm(e2)
    if n1 == 0 or n2 == 0: return 0.0
    return np.dot(e1, e2) / (n1 * n2)

class EmbeddingAggregator:
    def __init__(self, max_similarity=0.95, min_samples=2):
        self.max_similarity = max_similarity
        self.min_samples = min_samples
        
    def aggregate(self, faces_data):
        if not faces_data:
            return None, 0.0, 0, 0.0, 0.0
            
        # 1. Diversity Filtering natively
        diverse_data = []
        for fd in faces_data:
            emb = fd["embedding"]
            is_duplicate = False
            for d in diverse_data:
                sim = cosine_similarity(emb, d["embedding"])
                if sim > self.max_similarity:
                    if fd["quality_score"] > d["quality_score"]:
                        d["embedding"] = emb
                        d["quality_score"] = fd["quality_score"]
                        d["det_score"] = fd["det_score"]
                    is_duplicate = True
                    break
            if not is_duplicate:
                diverse_data.append(fd)
                
        if not diverse_data:
            return None, 0.0, 0, 0.0, 0.0

        # 2. Outlier Removal algorithms mapping distance topologies securely
        embeddings = np.array([fd["embedding"] for fd in diverse_data])
        
        if len(diverse_data) >= 3:
            mean_emb = np.mean(embeddings, axis=0)
            sims = [cosine_similarity(e, mean_emb) for e in embeddings]
            
            mean_sim = np.mean(sims)
            std_sim = np.std(sims)
            threshold = mean_sim - std_sim
            
            filtered_data = []
            for i, fd in enumerate(diverse_data):
                if sims[i] >= threshold:
                    filtered_data.append(fd)
                    
            if not filtered_data:
                filtered_data = diverse_data
            diverse_data = filtered_data

        embeddings = np.array([fd["embedding"] for fd in diverse_data])
        weights = [fd["quality_score"] for fd in diverse_data]
        det_scores = [fd["det_score"] for fd in diverse_data]
        
        # 3. Consistency Indexing
        consistency = 1.0
        n = len(diverse_data)
        if n >= 2:
            sim_sum = 0
            count = 0
            for i in range(n):
                for j in range(i+1, n):
                    sim_sum += cosine_similarity(embeddings[i], embeddings[j])
                    count += 1
            consistency = float(sim_sum / float(count))
            
        # 4. Aggregations mathematically standardizing weights against L2 geometry maps
        total_weight = sum(weights)
        if total_weight > 0:
            norm_weights = [w / float(total_weight) for w in weights]
        else:
            norm_weights = [1.0 / n] * n
            
        agg_emb = np.average(embeddings, axis=0, weights=norm_weights)
        
        norm = np.linalg.norm(agg_emb)
        if norm > 0:
            agg_emb = agg_emb / norm
            
        avg_quality = float(np.mean(weights))
        avg_det = float(np.mean(det_scores))
        
        # 5. Composite End-Confidence Formulations penalizing minor hits strictly linearly
        sample_penalty = min(n / float(self.min_samples), 1.0)
        final_confidence = avg_det * consistency * sample_penalty
        
        return agg_emb.tolist(), float(final_confidence), n, avg_quality, consistency
