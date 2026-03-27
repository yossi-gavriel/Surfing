"""
Lambda: surf-ai-stop-ingestion
Triggered by CloudWatch Events at end of surf day (6pm Israel time).
Sends SSM command to EC2 to stop the ingestion container and signal the watchdog.
The watchdog then waits for all SQS queues to drain before stopping the instance.
"""
import boto3
import os
import logging

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, context):
    ec2         = boto3.client('ec2', region_name=os.environ['AWS_REGION'])
    ssm         = boto3.client('ssm', region_name=os.environ['AWS_REGION'])
    instance_id = os.environ['INSTANCE_ID']

    resp  = ec2.describe_instances(InstanceIds=[instance_id])
    state = resp['Reservations'][0]['Instances'][0]['State']['Name']
    log.info('Instance %s state: %s', instance_id, state)

    if state != 'running':
        log.info('Instance not running — nothing to stop')
        return {'action': 'skipped', 'state': state}

    cmd = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName='AWS-RunShellScript',
        Parameters={
            'commands': [
                # Stop ingestion container (graceful; ignore if already stopped)
                'docker stop ingestion-service || true',
                # Signal watchdog that ingestion is done
                'touch /tmp/ingestion-stopped',
                'echo "Ingestion stopped at $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> /var/log/surf-ai-watchdog.log',
            ]
        },
        Comment='Surf AI — end-of-day ingestion stop',
    )

    command_id = cmd['Command']['CommandId']
    log.info('SSM command sent: %s', command_id)
    return {'action': 'ingestion_stopped', 'command_id': command_id}
