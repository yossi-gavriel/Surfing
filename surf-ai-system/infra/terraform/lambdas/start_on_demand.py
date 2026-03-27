"""
Lambda: surf-ai-start-on-demand
POST /start-system — called directly from the Angular admin frontend.

Checks EC2 state and starts it if stopped.
Safe against duplicate calls (idempotent).
Also clears /tmp/ingestion-stopped so watchdog doesn't immediately re-shut down.
"""
import boto3
import json
import os
import logging

log = logging.getLogger()
log.setLevel(logging.INFO)

INSTANCE_ID = os.environ['INSTANCE_ID']
REGION = os.environ['AWS_REGION']

CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type,X-Api-Key',
    'Access-Control-Allow-Methods': 'POST,OPTIONS',
}


def respond(status_code: int, body: dict) -> dict:
    return {
        'statusCode': status_code,
        'headers': {**CORS, 'Content-Type': 'application/json'},
        'body': json.dumps(body),
    }


def handler(event, context):
    # Handle CORS preflight
    if event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return respond(200, {'ok': True})

    ec2 = boto3.client('ec2', region_name=REGION)
    ssm = boto3.client('ssm', region_name=REGION)

    resp = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    state = resp['Reservations'][0]['Instances'][0]['State']['Name']
    log.info('Instance %s state: %s', INSTANCE_ID, state)

    if state == 'running':
        # Clear the ingestion-stopped signal so watchdog doesn't immediately shut down
        try:
            ssm.send_command(
                InstanceIds=[INSTANCE_ID],
                DocumentName='AWS-RunShellScript',
                Parameters={'commands': [
                    'rm -f /tmp/ingestion-stopped',
                    'docker start ingestion-service || true',
                ]},
                Comment='Surf AI — resume ingestion on running instance',
            )
        except Exception as exc:
            log.warning('Could not resume ingestion: %s', exc)

        return respond(200, {
            'status': 'already_running',
            'instance_id': INSTANCE_ID,
            'message': 'System is already running — ingestion resumed.',
        })

    if state in ('stopped', 'stopping'):
        ec2.start_instances(InstanceIds=[INSTANCE_ID])
        log.info('Start command sent for %s', INSTANCE_ID)
        return respond(200, {
            'status': 'starting',
            'instance_id': INSTANCE_ID,
            'message': 'System is booting (~60s). Services will start automatically.',
        })

    # pending / shutting-down / terminated / etc.
    return respond(200, {
        'status': state,
        'instance_id': INSTANCE_ID,
        'message': f'Instance is currently in state: {state}. Please try again shortly.',
    })
