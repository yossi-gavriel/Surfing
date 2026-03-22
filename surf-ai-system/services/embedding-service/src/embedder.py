class FaceEmbedder:
    def __init__(self):
        pass
        
    def extract_embedding(self, face_obj):
        """
        Strictly resolves the underlying 512-dimensional output vectors embedded dynamically by the InsightFace inference context.
        """
        return face_obj.embedding
