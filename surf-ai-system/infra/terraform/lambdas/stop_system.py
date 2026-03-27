"""
Lambda: surf-ai-stop-system
POST /stop-system — called from Angular admin frontend.

Stops ingestion via SSM and signals the watchdog.
Watchdog drains the pipeline queues, then stops the EC2 instance.
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
    if event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return respond(200, {'ok': True})

    ec2 = boto3.client('ec2', region_name=REGION)
    ssm = boto3.client('ssm', region_name=REGION)

    resp = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    state = resp['Reservations'][0]['Instances'][0]['State']['Name']
    log.info('Instance %s state: %s', INSTANCE_ID, state)

    if state != 'running':
        return respond(200, {
            'status': 'not_running',
            'instance_id': INSTANCE_ID,
            'message': f'System is already stopped (state: {state}).',
        })

    cmd = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName='AWS-RunShellScript',
        Parameters={
            'commands': [
                'docker stop ingestion-service || true',
                'touch /tmp/ingestion-stopped',
                'echo "Manual stop triggered at $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> /var/log/surf-ai-watchdog.log',
            ]
        },
        Comment='Surf AI — manual stop from admin UI',
    )

    command_id = cmd['Command']['CommandId']
    log.info('SSM stop command sent: %s', command_id)

    return respond(200, {
        'status': 'stopping',
        'instance_id': INSTANCE_ID,
        'message': 'Ingestion stopped. Pipeline is draining. EC2 will shut down automatically when queues are empty (~20 min).',
        'command_id': command_id,
    })
