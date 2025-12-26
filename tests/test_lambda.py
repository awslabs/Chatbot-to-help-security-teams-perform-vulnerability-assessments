#!/usr/bin/env python3
import boto3
import json


def test_lambda():
    # Initialize Lambda client
    lambda_client = boto3.client("lambda", region_name="us-east-1")

    # Prepare payload
    payload = {
        "body": {
            "query": "What is a SQL injection vulnerability?",
            "session_id": "test-session-123",
        }
    }

    try:
        print("Invoking Lambda function...")
        print(f"Payload: {json.dumps(payload)}")

        # Invoke the Lambda function
        response = lambda_client.invoke(
            FunctionName="sec-advis-asst-invokeAgentLambda-043904799321-us-east-1",
            Payload=json.dumps(payload),
        )

        print(f"StatusCode: {response['StatusCode']}")

        # Read the response
        response_payload = response["Payload"].read()
        print(f"Response: {response_payload.decode()}")

        # Parse the JSON response
        result = json.loads(response_payload.decode())
        print(f"Parsed result: {json.dumps(result, indent=2)}")

    except Exception as e:
        print(f"Error invoking Lambda: {e}")


if __name__ == "__main__":
    test_lambda()
