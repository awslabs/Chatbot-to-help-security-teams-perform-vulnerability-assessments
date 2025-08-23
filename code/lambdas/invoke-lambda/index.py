"""Lambda handler for invoking the agent."""
import boto3
import json
import logging
import os
from collections import OrderedDict
import re
from typing import Dict, Any, List, Tuple, Optional
from code.security.middleware import validate_input, error_handler, audit_log, rate_limit
from code.security.security_config import safe_log, InputValidator

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment configuration
AGENT_ID = os.environ["AGENT_ID"]
REGION_NAME = os.environ["REGION_NAME"]
MAX_CALLS_PER_MINUTE = int(os.environ.get("MAX_CALLS_PER_MINUTE", "60"))

# Initialize AWS clients
agent_client = boto3.client("bedrock-agent", region_name=REGION_NAME)
agent_runtime_client = boto3.client("bedrock-agent-runtime", region_name=REGION_NAME)
s3_resource = boto3.resource("s3", region_name=REGION_NAME)

def get_highest_agent_version_alias_id(response: Dict[str, Any]) -> Optional[str]:
    """
    Find newest agent alias id securely.

    Args:
        response: Response from list_agent_aliases()

    Returns:
        Agent alias ID or None if not found
    """
    try:
        highest_version = None
        highest_version_alias_id = None

        for alias_summary in response.get("agentAliasSummaries", []):
            if not alias_summary.get("routingConfiguration"):
                continue

            agent_version = alias_summary["routingConfiguration"][0].get("agentVersion")
            if not agent_version or not agent_version.isdigit():
                continue

            version_num = int(agent_version)
            if highest_version is None or version_num > highest_version:
                highest_version = version_num
                highest_version_alias_id = alias_summary.get("agentAliasId")

        return highest_version_alias_id

    except Exception as e:
        safe_log(f"Error getting agent version: {str(e)}")
        return None

@rate_limit(max_calls=MAX_CALLS_PER_MINUTE, time_window=60)
@error_handler
@audit_log
def invoke_agent(user_input: str, session_id: str) -> Dict[str, Any]:
    """
    Get response from Agent with security controls.

    Args:
        user_input: User's question
        session_id: Session identifier

    Returns:
        Agent response
    """
    # Validate input
    sanitized_input = InputValidator.sanitize_input(user_input)
    if not sanitized_input:
        raise ValueError("Invalid input")

    try:
        response = agent_client.list_agent_aliases(agentId=AGENT_ID)
        safe_log("Agent aliases retrieved", sensitive=True)

        agent_alias_id = get_highest_agent_version_alias_id(response)
        if not agent_alias_id:
            raise ValueError("No agent published alias found")

        streaming_response = agent_runtime_client.invoke_agent(
            agentId=AGENT_ID,
            agentAliasId=agent_alias_id,
            sessionId=session_id,
            enableTrace=True,
            inputText=sanitized_input
        )

        return streaming_response

    except Exception as e:
        safe_log(f"Agent invocation error: {str(e)}")
        raise

def get_agent_response(response: Dict[str, Any]) -> Tuple[str, str, List[str]]:
    """
    Process agent response securely.

    Args:
        response: Raw agent response

    Returns:
        Tuple of (response text, reference text, source files)
    """
    if "completion" not in response:
        raise ValueError(f"No completion found in response")

    chunk_text = ""
    reference_text = ""
    source_file_list = []
    trace_list = []

    try:
        for event in response["completion"]:
            # Handle traces
            if "trace" in event:
                trace_list.append(event["trace"])

            # Handle chunks
            if "chunk" in event:
                chunk_bytes = event["chunk"].get("bytes")
                if chunk_bytes:
                    chunk_text = chunk_bytes.decode("utf-8")

                # Handle citations
                if "attribution" in event["chunk"]:
                    citations = event["chunk"]["attribution"].get("citations", [])
                    for citation in citations:
                        # Get response parts
                        if "generatedResponsePart" in citation:
                            response_part = citation["generatedResponsePart"].get("textResponsePart", {})
                            if "text" in response_part:
                                safe_log(f"Response part: {response_part['text']}")

                        # Get references
                        if "retrievedReferences" in citation:
                            for reference in citation["retrievedReferences"]:
                                if "content" in reference and "text" in reference["content"]:
                                    reference_text = reference["content"]["text"]

                                if "location" in reference and "s3Location" in reference["location"]:
                                    source_file = reference["location"]["s3Location"].get("uri")
                                    if source_file:
                                        source_file_list.append(source_file)

        # Process traces for SQL queries
        for trace in trace_list:
            if "orchestrationTrace" in trace.get("trace", {}):
                observation = trace["trace"]["orchestrationTrace"].get("observation", {})
                if observation.get("type") == "ACTION_GROUP":
                    output = observation.get("actionGroupInvocationOutput", {}).get("text")
                    if output:
                        sql_query = extract_sql_query(output)
                        if sql_query:
                            source_file_list = [sql_query]

        return chunk_text, reference_text, source_file_list

    except Exception as e:
        safe_log(f"Error processing agent response: {str(e)}")
        raise

def source_link(input_source_list: List[str]) -> str:
    """
    Process source links securely.

    Args:
        input_source_list: List of source S3 URIs

    Returns:
        Formatted source references
    """
    source_dict_list = []

    try:
        for input_source in input_source_list:
            # Validate S3 URI format
            if not input_source.startswith("s3://"):
                safe_log(f"Invalid S3 URI: {input_source}")
                continue

            parts = input_source.split("//")[1].split("/", 1)
            if len(parts) != 2:
                safe_log(f"Invalid S3 path format: {input_source}")
                continue

            bucket = parts[0]
            obj_key = parts[1]

            try:
                file_obj = s3_resource.Object(bucket, obj_key)
                body = file_obj.get()["Body"].read()
                content = json.loads(body)

                source_link_url = content.get("Url")
                source_title = content.get("Topic")

                if source_link_url and source_title:
                    source_dict_list.append((source_title, source_link_url))

            except Exception as e:
                safe_log(f"Error reading S3 object: {str(e)}")
                continue

        # Remove duplicates while preserving order
        unique_sources = list(OrderedDict.fromkeys(source_dict_list))

        # Format references
        refs_str = ""
        for i, (title, link) in enumerate(unique_sources, start=1):
            # Validate URL format
            if not re.match(r'^https?://', link):
                safe_log(f"Invalid URL format: {link}")
                continue
            refs_str += f"{i}. [{title}]({link})\n\n"

        return refs_str

    except Exception as e:
        safe_log(f"Error processing source links: {str(e)}")
        return ""

def extract_sql_query(input_string: str) -> Optional[str]:
    """
    Safely extract SQL query from input.

    Args:
        input_string: Input containing SQL query

    Returns:
        Extracted query or None
    """
    try:
        pattern = r"(SELECT.*?)(?=\n\s*(?:Returned information|$))"
        match = re.search(pattern, input_string, re.DOTALL | re.IGNORECASE)

        if match:
            query = match.group(1).strip()
            # Validate extracted SQL
            if InputValidator.validate_sql_query(query):
                return query
            return None

    except Exception as e:
        safe_log(f"Error extracting SQL query: {str(e)}")
        return None

@error_handler
@audit_log
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler with security controls.

    Args:
        event: Lambda event
        context: Lambda context

    Returns:
        Lambda response
    """
    try:
        safe_log("Processing Lambda event", sensitive=True)

        body = event.get("body", {})
        if not isinstance(body, dict):
            raise ValueError("Invalid request body")

        query = body.get("query")
        session_id = body.get("session_id")

        if not query or not session_id:
            raise ValueError("Missing required parameters")

        streaming_response = invoke_agent(query, session_id)
        response, reference_text, source_file_list = get_agent_response(streaming_response)

        if isinstance(source_file_list, list):
            reference_str = source_link(source_file_list)
        else:
            reference_str = str(source_file_list)

        return {
            "answer": response,
            "source": reference_str
        }

    except Exception as e:
        safe_log(f"Lambda handler error: {str(e)}")
        return {
            "error": "An internal error occurred",
            "status": 500
        }
