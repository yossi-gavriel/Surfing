import os

class MatchingConfig:
    def __init__(self):
        self.aws_region = os.environ.get("AWS_REGION", "us-east-1")
        self.input_sqs_url = os.environ.get("MATCHING_INPUT_SQS_URL")
        self.output_sqs_url = os.environ.get("MATCHING_OUTPUT_SQS_URL")
        self.clipper_output_sqs_url = os.environ.get("CLIPPER_OUTPUT_SQS_URL")
        
        self.min_best_score = float(os.environ.get("MIN_BEST_SCORE", "0.75"))
        self.min_score_margin = float(os.environ.get("MIN_SCORE_MARGIN", "0.05"))
        self.min_emb_confidence = float(os.environ.get("MIN_EMB_CONFIDENCE", "0.6"))
        
        self.users_db_path = os.environ.get("USERS_DB_PATH", "/app/data/users.json")
        
        if not self.input_sqs_url or not self.output_sqs_url:
            raise ValueError("MATCHING_INPUT_SQS_URL and MATCHING_OUTPUT_SQS_URL are required")

config = MatchingConfig()
