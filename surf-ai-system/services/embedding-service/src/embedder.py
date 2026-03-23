from shared.utils.embeddings import normalize_embedding_vector


class FaceEmbedder:
    def __init__(self):
        pass
        
    def extract_embedding(self, face_obj):
        """
        Strictly resolves the underlying 512-dimensional output vectors embedded dynamically by the InsightFace inference context.
        """
        normalized = normalize_embedding_vector(face_obj.embedding)
        if normalized is None:
            raise ValueError("Could not normalize embedding output")
        return normalized
