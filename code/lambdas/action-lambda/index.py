"""Lambda handler for processing actions."""
from build_query_engine import query_engine
import json
import logging
from typing import Dict, Any, List
from code.security.middleware import validate_input, error_handler, audit_log
from code.security.security_config import safe_log

logger = logging.getLogger()
logger.setLevel(logging.INFO)

@error_handler
@audit_log
@validate_input
def get_response(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Get response RAG or Query with security controls.

    Args:
        event: Lambda event
        context: Lambda context

    Returns:
        Response dictionary
    """
    safe_log("Processing event", sensitive=True)
    safe_log(json.dumps(event))

    responses: List[Dict[str, Any]] = []
    response_code = 200

    try:
        prediction = event
        api_path = prediction["apiPath"]
        parameters = prediction["parameters"]
        user_input = parameters[0]["value"]

        safe_log(f"Processing question: {user_input}")

        if api_path == "/uc2":
            response = query_engine.query(user_input)

            # Log SQL query securely
            safe_log("SQL query:", sensitive=True)
            safe_log(response.metadata["sql_query"].replace("\n", " "), sensitive=True)

            safe_log(f"Generated response: {response.response}")

            output = {
                "source": response.metadata["sql_query"],
                "answer": response.response,
            }

        elif api_path == "/uc1":
            output = {
                "source": "Doc retrieval",
                "answer": "Getting info from knowledgebase.",
            }

        else:
            output = {
                "source": "Not Found",
                "answer": "I don't know enough to answer this question, please try to clarify your question.",
            }

    except Exception as e:
        safe_log(f"Error processing request: {str(e)}")
        output = {
            "source": "Error",
            "answer": "An error occurred processing your request.",
        }
        response_code = 500

    body = f"""
            Source: {output["source"]}
            Returned information: {output["answer"]}
            """

    response_body = {"application/json": {"body": body}}

    action_response = {
        "actionGroup": prediction["actionGroup"],
        "apiPath": prediction["apiPath"],
        "httpMethod": prediction["httpMethod"],
        "httpStatusCode": response_code,
        "responseBody": response_body,
    }

    responses.append(action_response)

    return {"messageVersion": "1.0", "response": action_response}

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler with error handling.

    Args:
        event: Lambda event
        context: Lambda context

    Returns:
        Lambda response
    """
    try:
        return get_response(event, context)
    except Exception as e:
        safe_log(f"Lambda handler error: {str(e)}")
        return {
            "messageVersion": "1.0",
            "response": {
                "httpStatusCode": 500,
                "responseBody": {
                    "application/json": {
                        "body": "An internal error occurred"
                    }
                }
            }
        }
