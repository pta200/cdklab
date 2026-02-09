import os
import json
import datetime
import logging
import uuid
import boto3

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

def handler(event, context):
    try:
        kinesis = boto3.client('kinesis')
        if event.get('body'):
            event_data = json.loads(event["body"])
            event_data["create_ts"]= datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            data = json.dumps(event_data)
            payload = str(data) + "\n"
            partition_key = event_data.get("session_id") if event_data.get("session_id") else str(uuid.uuid4())
            kinesis.put_record(
                    StreamName=os.getenv('KDS_NAME', ''),
                    Data=payload,
                    PartitionKey=partition_key)
            logger.debug('data ingested in Kinesis...')
            return {
                'statusCode': 200,
                'body': json.dumps('event data processed')
            }
        
        else:
            return {'statusCode': 422, 'body': "no event data supplied"}
    
    except Exception as error:
        logger.exception(error)
        return {'statusCode': 500, 'body': f"ingest incurred the following error: {str(error)}"}

