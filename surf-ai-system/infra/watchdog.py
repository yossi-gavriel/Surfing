#!/usr/bin/env python3
"""
Surf AI Watchdog — Auto-shutdown monitor.

Runs on EC2 as a systemd service.
Once ingestion has been stopped (signalled by /tmp/ingestion-stopped),
monitors all 5 SQS pipeline queues.
When all queues have been empty for WATCHDOG_EMPTY_MINUTES, stops
this EC2 instance automatically to save costs.
"""
import boto3
import os
import time
import logging
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/var/log/surf-ai-watchdog.log'),
    ]
)
log = logging.getLogger('watchdog')

REGION        = os.environ.get('AWS_REGION', 'us-east-1')
EMPTY_MINUTES = int(os.environ.get('WATCHDOG_EMPTY_MINUTES', '20'))
CHECK_SECONDS = int(os.environ.get('WATCHDOG_CHECK_SECONDS', '60'))
SIGNAL_FILE   = '/tmp/ingestion-stopped'

# All 5 unique SQS queues in the pipeline (from .env on EC2)
QUEUES = list({
    os.environ['INPUT_SQS_URL'],            # video-chunks-queue
    os.environ['OUTPUT_SQS_URL'],            # tracks-queue
    os.environ['EMBEDDING_OUTPUT_SQS_URL'],  # embeddings-queue
    os.environ['MATCHING_OUTPUT_SQS_URL'],   # matching-queue
    os.environ['CLIPPER_INPUT_SQS_URL'],     # clipper-queue
})


def get_instance_id() -> str:
    """Fetch this instance's ID via IMDSv2 (with v1 fallback)."""
    try:
        token_req = urllib.request.Request(
            'http://169.254.169.254/latest/api/token',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '21600'},
            method='PUT'
        )
        token = urllib.request.urlopen(token_req, timeout=2).read().decode()
        req = urllib.request.Request(
            'http://169.254.169.254/latest/meta-data/instance-id',
            headers={'X-aws-ec2-metadata-token': token}
        )
        return urllib.request.urlopen(req, timeout=2).read().decode()
    except Exception:
        return urllib.request.urlopen(
            'http://169.254.169.254/latest/meta-data/instance-id',
            timeout=2
        ).read().decode()


def total_queue_depth(sqs_client) -> int:
    """Sum of visible + in-flight messages across all pipeline queues."""
    total = 0
    for url in QUEUES:
        try:
            attrs = sqs_client.get_queue_attributes(
                QueueUrl=url,
                AttributeNames=[
                    'ApproximateNumberOfMessages',
                    'ApproximateNumberOfMessagesNotVisible',
                ]
            )['Attributes']
            visible   = int(attrs.get('ApproximateNumberOfMessages', 0))
            in_flight = int(attrs.get('ApproximateNumberOfMessagesNotVisible', 0))
            total += visible + in_flight
        except Exception as exc:
            log.warning('Could not read queue depth for %s: %s', url, exc)
    return total


def ingestion_stopped() -> bool:
    return os.path.exists(SIGNAL_FILE)


def main():
    instance_id = get_instance_id()
    log.info('Watchdog started — instance=%s  queues=%d  threshold=%dmin  check=%ds',
             instance_id, len(QUEUES), EMPTY_MINUTES, CHECK_SECONDS)

    sqs = boto3.client('sqs', region_name=REGION)
    ec2 = boto3.client('ec2',  region_name=REGION)

    empty_since: float | None = None

    while True:
        try:
            if not ingestion_stopped():
                log.info('Ingestion active — watchdog standing by')
                empty_since = None
                time.sleep(CHECK_SECONDS)
                continue

            depth = total_queue_depth(sqs)

            if depth == 0:
                if empty_since is None:
                    empty_since = time.time()
                    log.info('All queues empty — drain timer started (%dmin window)', EMPTY_MINUTES)
                else:
                    elapsed_min = (time.time() - empty_since) / 60
                    remaining   = max(0.0, EMPTY_MINUTES - elapsed_min)
                    log.info('Queues empty %.1fmin — shutdown in %.1fmin', elapsed_min, remaining)

                    if elapsed_min >= EMPTY_MINUTES:
                        log.info('Drain window complete — stopping instance %s', instance_id)
                        ec2.stop_instances(InstanceIds=[instance_id])
                        log.info('Stop command sent. Watchdog exiting.')
                        return
            else:
                if empty_since is not None:
                    log.info('Work detected (depth=%d) — drain timer reset', depth)
                empty_since = None
                log.info('Queue depth: %d — pipeline active', depth)

        except Exception as exc:
            log.error('Watchdog loop error: %s', exc, exc_info=True)

        time.sleep(CHECK_SECONDS)


if __name__ == '__main__':
    main()
