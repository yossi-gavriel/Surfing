import os
import json
import time
import boto3
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from src.config import config
from src.db import UsersDB
from src.matcher import Matcher
from shared.utils.logger import get_logger

logger = get_logger("matching-service")
sqs_client = boto3.client('sqs', region_name=config.aws_region)

def main():
    logger.info("Starting Contextual Matching Ecosystem Endpoint")
    
    db = UsersDB(config.users_db_path)
    matcher = Matcher(db)
    
    while True:
        try:
            response = sqs_client.receive_message(
                QueueUrl=config.input_sqs_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20
            )
            
            messages = response.get('Messages', [])
            for message in messages:
                receipt_handle = message['ReceiptHandle']
                body = json.loads(message['Body'])
                
                try:
                    track_id = body.get("track_id")
                    camera_id = body.get("camera_id")
                    emb = body.get("face_embedding")
                    emb_conf = body.get("embedding_confidence")
                    
                    if not track_id or not emb or emb_conf is None:
                        logger.error("Missing essential envelope architectures parsing payload actively.")
                        sqs_client.delete_message(QueueUrl=config.input_sqs_url, ReceiptHandle=receipt_handle)
                        continue
                        
                    match_result = matcher.match(track_id, emb, emb_conf)
                    
                    if match_result:
                        output_msg = {
                            "track_id": track_id,
                            "user_id": match_result["user_id"],
                            "score": match_result["score"],
                            "confidence": match_result["confidence"]
                        }
                        
                        msg_body = json.dumps(output_msg)

                        try:
                            sqs_client.send_message(
                                QueueUrl=config.output_sqs_url,
                                MessageBody=msg_body
                            )
                        except Exception as e:
                            logger.error(f"[{track_id}] Failed sending to matching-queue: {e}")

                        if config.clipper_output_sqs_url:
                            try:
                                sqs_client.send_message(
                                    QueueUrl=config.clipper_output_sqs_url,
                                    MessageBody=msg_body
                                )
                            except Exception as e:
                                logger.error(f"[{track_id}] Failed sending to clipper-queue: {e}")

                        logger.info(f"[{track_id}] Match sent to API + Clipper queues for user: {match_result['user_id']}")
                        
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle
                    )
                except Exception as e:
                    logger.error(f"Computation processing evaluation internal pipeline disrupted implicitly: {e}")
                    
        except KeyboardInterrupt:
            logger.info("Shutting workflow execution containers safely natively...")
            break
        except Exception as e:
            logger.error(f"SQS Network reception failure log execution: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
