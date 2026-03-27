"""
Lambda: surf-ai-start-ec2
Triggered by CloudWatch Events at start of day (6am Israel time).
Starts the Surf AI EC2 instance if it is stopped.
"""
import boto3
import os
import logging

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, context):
    ec2         = boto3.client('ec2', region_name=os.environ['AWS_REGION'])
    instance_id = os.environ['INSTANCE_ID']

    resp  = ec2.describe_instances(InstanceIds=[instance_id])
    state = resp['Reservations'][0]['Instances'][0]['State']['Name']
    log.info('Instance %s current state: %s', instance_id, state)

    if state in ('stopped', 'stopping'):
        ec2.start_instances(InstanceIds=[instance_id])
        log.info('Start command sent for instance %s', instance_id)
        return {'action': 'started', 'instance_id': instance_id}

    log.info('Instance already %s — no action taken', state)
    return {'action': 'skipped', 'state': state}
