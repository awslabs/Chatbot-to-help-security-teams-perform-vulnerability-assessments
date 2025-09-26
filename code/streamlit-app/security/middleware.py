"""Security middleware and decorators."""
import functools
import logging
from typing import Any, Callable, Dict, Optional
from .security_config import InputValidator, SecurityConfig, safe_log

logger = logging.getLogger(__name__)

def validate_input(func: Callable) -> Callable:
    """
    Decorator to validate and sanitize input parameters.

    Args:
        func: The function to wrap

    Returns:
        Wrapped function with input validation
    """
    @functools.wraps(func)
    def wrapper(user_input: str, *args: Any, **kwargs: Any) -> Any:
        sanitized_input = InputValidator.sanitize_input(user_input)
        if sanitized_input is None:
            raise ValueError("Invalid input")
        return func(sanitized_input, *args, **kwargs)
    return wrapper

def validate_sql(func: Callable) -> Callable:
    """
    Decorator to validate SQL queries.

    Args:
        func: The function to wrap

    Returns:
        Wrapped function with SQL validation
    """
    @functools.wraps(func)
    def wrapper(query: str, *args: Any, **kwargs: Any) -> Any:
        if not InputValidator.validate_sql_query(query):
            raise ValueError("Invalid SQL query")
        return func(query, *args, **kwargs)
    return wrapper

def secure_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """
    Add security headers to response headers.

    Args:
        headers: Original headers

    Returns:
        Headers with security additions
    """
    return {**headers, **SecurityConfig.SECURE_HEADERS}

def rate_limit(max_calls: int, time_window: int) -> Callable:
    """
    Decorator to implement rate limiting.

    Args:
        max_calls: Maximum number of calls allowed
        time_window: Time window in seconds

    Returns:
        Rate-limited function
    """
    def decorator(func: Callable) -> Callable:
        # Store call history for rate limiting
        call_history: Dict[str, list] = {}

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from datetime import datetime, timedelta

            # Get caller ID (implement based on your auth system)
            caller_id = kwargs.get('session_id', 'default')

            # Initialize call history for new callers
            if caller_id not in call_history:
                call_history[caller_id] = []

            # Clean up old calls
            current_time = datetime.now()
            call_history[caller_id] = [
                call_time for call_time in call_history[caller_id]
                if current_time - call_time < timedelta(seconds=time_window)
            ]

            # Check rate limit
            if len(call_history[caller_id]) >= max_calls:
                raise Exception("Rate limit exceeded")

            # Add current call
            call_history[caller_id].append(current_time)

            return func(*args, **kwargs)
        return wrapper
    return decorator

def error_handler(func: Callable) -> Callable:
    """
    Decorator to handle errors securely.

    Args:
        func: The function to wrap

    Returns:
        Error-handled function
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except ValueError as e:
            safe_log(f"Validation error: {str(e)}")
            return {"error": "Invalid input", "status": 400}
        except Exception as e:
            safe_log(f"Internal error: {str(e)}")
            # Don't expose internal error details to client
            return {"error": "An internal error occurred", "status": 500}
    return wrapper

def audit_log(func: Callable) -> Callable:
    """
    Decorator to add audit logging.

    Args:
        func: The function to wrap

    Returns:
        Audit-logged function
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Log function call
        safe_log(
            f"Audit: Calling {func.__name__} with args: {args}, kwargs: {kwargs}",
            sensitive=True
        )

        result = func(*args, **kwargs)

        # Log result (carefully)
        safe_log(
            f"Audit: {func.__name__} completed successfully",
            sensitive=False
        )

        return result
    return wrapper
