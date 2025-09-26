"""Lambda handler for invoking the agent."""

import boto3
import json
import logging
import os
from collections import OrderedDict
import re
from typing import Dict, Any, List, Tuple, Optional
import functools
from datetime import datetime, timedelta

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


# Local security implementations
def safe_log(message: str, sensitive: bool = False) -> None:
    """Safely log messages, handling sensitive data appropriately."""
    try:
        if sensitive and not os.getenv("DEBUG"):
            logger.info("Sensitive data logged in debug mode only")
            return
        logger.info(message)
    except Exception as e:
        logger.error(f"Error logging message: {str(e)}")


class InputValidator:
    """Input validation utilities."""

    MAX_INPUT_LENGTH = 1000
    ALLOWED_CHARS_PATTERN = r'^[\w\s\-\.,\?!@#$%^&*()+=\[\]{}|\\:;"\'<>\/]+$'
    ALLOWED_SQL_KEYWORDS = {
        "SELECT",
        "FROM",
        "WHERE",
        "AND",
        "OR",
        "IN",
        "LIKE",
        "LIMIT",
        "ORDER",
        "BY",
        "ASC",
        "DESC",
        "GROUP",
        "HAVING",
        "JOIN",
    }

    @staticmethod
    def sanitize_input(user_input: str) -> Optional[str]:
        """Sanitize user input by validating length and characters."""
        try:
            if not isinstance(user_input, str):
                logger.warning(f"Invalid input type: {type(user_input)}")
                return None

            if len(user_input) > InputValidator.MAX_INPUT_LENGTH:
                logger.warning(f"Input exceeds maximum length: {len(user_input)}")
                return None

            if not re.match(InputValidator.ALLOWED_CHARS_PATTERN, user_input):
                logger.warning("Input contains invalid characters")
                return None

            return user_input.strip()
        except Exception as e:
            logger.error(f"Error sanitizing input: {str(e)}")
            return None

    @staticmethod
    def validate_sql_query(query: str) -> bool:
        """Validate SQL query against allowed keywords and patterns."""
        try:
            query_upper = query.upper()
            query_words = set(re.findall(r"\b\w+\b", query_upper))

            sql_words = {
                word
                for word in query_words
                if word not in {"AND", "OR", "IN", "THE", "AS", "ON"}
            }

            if not sql_words.issubset(InputValidator.ALLOWED_SQL_KEYWORDS):
                invalid_words = sql_words - InputValidator.ALLOWED_SQL_KEYWORDS
                logger.warning(f"Query contains invalid SQL keywords: {invalid_words}")
                return False

            if ";" in query or "--" in query or "/*" in query:
                logger.warning("Query contains invalid characters")
                return False

            return True
        except Exception as e:
            logger.error(f"Error validating SQL query: {str(e)}")
            return False


# Security decorators (simplified)
def error_handler(func):
    """Decorator to handle errors securely."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ValueError as e:
            safe_log(f"Validation error: {str(e)}")
            return {"error": "Invalid input", "status": 400}
        except Exception as e:
            safe_log(f"Internal error: {str(e)}")
            return {"error": "An internal error occurred", "status": 500}

    return wrapper


def audit_log(func):
    """Decorator to add audit logging."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        safe_log(f"Audit: Calling {func.__name__}", sensitive=True)
        result = func(*args, **kwargs)
        safe_log(f"Audit: {func.__name__} completed successfully", sensitive=False)
        return result

    return wrapper


def rate_limit(max_calls: int, time_window: int):
    """Decorator to implement rate limiting."""
    call_history = {}

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            caller_id = kwargs.get("session_id", "default")

            if caller_id not in call_history:
                call_history[caller_id] = []

            current_time = datetime.now()
            call_history[caller_id] = [
                call_time
                for call_time in call_history[caller_id]
                if current_time - call_time < timedelta(seconds=time_window)
            ]

            if len(call_history[caller_id]) >= max_calls:
                raise Exception("Rate limit exceeded")

            call_history[caller_id].append(current_time)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def validate_input(func):
    """Decorator to validate and sanitize input parameters."""

    @functools.wraps(func)
    def wrapper(user_input: str, *args, **kwargs):
        sanitized_input = InputValidator.sanitize_input(user_input)
        if sanitized_input is None:
            raise ValueError("Invalid input")
        return func(sanitized_input, *args, **kwargs)

    return wrapper


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
            inputText=sanitized_input,
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
    print(f"[DEBUG] Processing response with keys: {list(response.keys())}")

    if "completion" not in response:
        raise ValueError(f"No completion found in response")

    chunk_text = ""
    reference_text = ""
    source_file_list = []
    trace_list = []
    event_count = 0
    chunk_count = 0

    try:
        print("[DEBUG] Starting to process completion events...")
        for event in response["completion"]:
            event_count += 1
            print(f"[DEBUG] Event {event_count}: {list(event.keys())}")

            # Handle traces
            if "trace" in event:
                trace_list.append(event["trace"])
                print(f"[DEBUG] Added trace to list")

            # Handle chunks
            if "chunk" in event:
                chunk_count += 1
                chunk_bytes = event["chunk"].get("bytes")
                print(
                    f"[DEBUG] Chunk {chunk_count}: bytes present = {chunk_bytes is not None}"
                )

                if chunk_bytes:
                    decoded_chunk = chunk_bytes.decode("utf-8")
                    print(f"[DEBUG] Decoded chunk length: {len(decoded_chunk)}")
                    print(f"[DEBUG] Chunk preview: {decoded_chunk[:50]}...")
                    chunk_text += decoded_chunk
                    print(f"[DEBUG] Total accumulated text length: {len(chunk_text)}")

                # Handle citations
                if "attribution" in event["chunk"]:
                    citations = event["chunk"]["attribution"].get("citations", [])
                    print(f"[DEBUG] Found {len(citations)} citations")
                    for citation in citations:
                        # Get response parts
                        if "generatedResponsePart" in citation:
                            response_part = citation["generatedResponsePart"].get(
                                "textResponsePart", {}
                            )
                            if "text" in response_part:
                                safe_log(f"Response part: {response_part['text']}")

                        # Get references
                        if "retrievedReferences" in citation:
                            for reference in citation["retrievedReferences"]:
                                if (
                                    "content" in reference
                                    and "text" in reference["content"]
                                ):
                                    reference_text = reference["content"]["text"]
                                    print(
                                        f"[DEBUG] Found reference text (length: {len(reference_text)})"
                                    )

                                if (
                                    "location" in reference
                                    and "s3Location" in reference["location"]
                                ):
                                    source_file = reference["location"][
                                        "s3Location"
                                    ].get("uri")
                                    if source_file:
                                        source_file_list.append(source_file)
                                        print(
                                            f"[DEBUG] Added source file: {source_file}"
                                        )

        print(f"[DEBUG] Processed {event_count} events, {chunk_count} chunks")
        print(f"[DEBUG] Final chunk_text length: {len(chunk_text)}")
        print(f"[DEBUG] Final chunk_text preview: {chunk_text[:100]}...")

        # Process traces for SQL queries
        print(f"[DEBUG] Processing {len(trace_list)} traces...")
        for trace in trace_list:
            if "orchestrationTrace" in trace.get("trace", {}):
                observation = trace["trace"]["orchestrationTrace"].get(
                    "observation", {}
                )
                if observation.get("type") == "ACTION_GROUP":
                    output = observation.get("actionGroupInvocationOutput", {}).get(
                        "text"
                    )
                    if output:
                        print(f"[DEBUG] Found action group output, extracting SQL...")
                        sql_query = extract_sql_query(output)
                        if sql_query:
                            source_file_list = [sql_query]
                            print(f"[DEBUG] Extracted SQL query: {sql_query[:100]}...")

        print(
            f"[DEBUG] Returning: chunk_text({len(chunk_text)}), reference_text({len(reference_text)}), sources({len(source_file_list)})"
        )
        return chunk_text, reference_text, source_file_list

    except Exception as e:
        error_msg = f"Error processing agent response: {str(e)}"
        print(f"[ERROR] {error_msg}")
        safe_log(error_msg)
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
            if not re.match(r"^https?://", link):
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
        print(f"[DEBUG] Lambda event received: {json.dumps(event, default=str)}")
        safe_log("Processing Lambda event", sensitive=True)

        body = event.get("body", {})
        print(f"[DEBUG] Request body type: {type(body)}, content: {body}")

        if not isinstance(body, dict):
            raise ValueError("Invalid request body")

        query = body.get("query")
        session_id = body.get("session_id")

        print(f"[DEBUG] Query: {query}")
        print(f"[DEBUG] Session ID: {session_id}")

        if not query or not session_id:
            raise ValueError("Missing required parameters")

        print("[DEBUG] Invoking agent...")
        streaming_response = invoke_agent(query, session_id)
        print(f"[DEBUG] Streaming response keys: {list(streaming_response.keys())}")

        print("[DEBUG] Processing agent response...")
        response, reference_text, source_file_list = get_agent_response(
            streaming_response
        )

        print(f"[DEBUG] Agent response length: {len(response)}")
        print(f"[DEBUG] Response preview: {response[:100]}...")
        print(f"[DEBUG] Reference text length: {len(reference_text)}")
        print(
            f"[DEBUG] Source files count: {len(source_file_list) if isinstance(source_file_list, list) else 'Not a list'}"
        )

        if isinstance(source_file_list, list):
            reference_str = source_link(source_file_list)
        else:
            reference_str = str(source_file_list)

        final_result = {"answer": response, "source": reference_str}
        print(
            f"[DEBUG] Final result answer length: {len(final_result.get('answer', ''))}"
        )
        print(
            f"[DEBUG] Final result source length: {len(final_result.get('source', ''))}"
        )

        return final_result

    except Exception as e:
        error_msg = f"Lambda handler error: {str(e)}"
        print(f"[ERROR] {error_msg}")
        safe_log(error_msg)
        return {"error": "An internal error occurred", "status": 500, "details": str(e)}
