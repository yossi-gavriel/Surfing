"""
Lambda: surf-ai-system-status
GET /system-status — polled by Angular frontend to show live EC2 state.

Returns the EC2 instance state and a human-readable label.
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
    'Access-Control-Allow-Methods': 'GET,OPTIONS',
}

STATE_LABELS = {
    'running':       'Running',
    'stopped':       'Stopped',
    'stopping':      'Stopping',
    'pending':       'Starting',
    'shutting-down': 'Shutting down',
    'terminated':    'Terminated',
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

    resp = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    instance = resp['Reservations'][0]['Instances'][0]
    state = instance['State']['Name']
    log.info('Instance %s state: %s', INSTANCE_ID, state)

    return respond(200, {
        'state': state,
        'instance_id': INSTANCE_ID,
        'label': STATE_LABELS.get(state, state.capitalize()),
        'message': _state_message(state),
    })


def _state_message(state: str) -> str:
    messages = {
        'running':  'System is running. All services are active.',
        'stopped':  'System is stopped. Click "Start System" to begin.',
        'stopping': 'System is stopping. Pipeline is draining queues.',
        'pending':  'System is starting up (~60 seconds).',
    }
    return messages.get(state, f'Instance state: {state}')
