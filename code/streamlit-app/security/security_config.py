"""Security configuration and utilities"""

import os
import re
import secrets
import streamlit as st
from datetime import datetime, timedelta
import logging
from typing import Optional, Dict, Any

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SecurityConfig:
    """Security configuration settings."""

    MAX_INPUT_LENGTH = int(os.getenv("MAX_INPUT_LENGTH", "1000"))
    ALLOWED_CHARS_PATTERN = r'^[\w\s\-\.,\?!@#$%^&*()+=\[\]{}|\\:;"\'<>\/]+$'
    SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT", "3600"))  # 1 hour
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    SECURE_HEADERS = {
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline';",
    }
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


class InputValidator:
    """Input validation utilities."""

    @staticmethod
    def sanitize_input(user_input: str) -> Optional[str]:
        """
        Sanitize user input by validating length and characters.

        Args:
            user_input: The input string to sanitize

        Returns:
            Sanitized string or None if invalid
        """
        try:
            if not isinstance(user_input, str):
                logger.warning(f"Invalid input type: {type(user_input)}")
                return None

            if len(user_input) > SecurityConfig.MAX_INPUT_LENGTH:
                logger.warning(f"Input exceeds maximum length: {len(user_input)}")
                return None

            if not re.match(SecurityConfig.ALLOWED_CHARS_PATTERN, user_input):
                logger.warning("Input contains invalid characters")
                return None

            return user_input.strip()

        except Exception as e:
            logger.error(f"Error sanitizing input: {str(e)}")
            return None

    @staticmethod
    def validate_sql_query(query: str) -> bool:
        """
        Validate SQL query against allowed keywords and patterns.

        Args:
            query: The SQL query to validate

        Returns:
            bool: True if valid, False otherwise
        """
        try:
            # Convert query to uppercase for keyword matching
            query_upper = query.upper()

            # Extract all words from query
            query_words = set(re.findall(r"\b\w+\b", query_upper))

            # Check if all words are in allowed keywords
            sql_words = {
                word
                for word in query_words
                if word not in {"AND", "OR", "IN", "THE", "AS", "ON"}
            }

            if not sql_words.issubset(SecurityConfig.ALLOWED_SQL_KEYWORDS):
                invalid_words = sql_words - SecurityConfig.ALLOWED_SQL_KEYWORDS
                logger.warning(f"Query contains invalid SQL keywords: {invalid_words}")
                return False

            # Prevent multiple statements
            if ";" in query:
                logger.warning("Query contains multiple statements")
                return False

            # Prevent comments
            if "--" in query or "/*" in query:
                logger.warning("Query contains comments")
                return False

            return True

        except Exception as e:
            logger.error(f"Error validating SQL query: {str(e)}")
            return False


class SessionManager:
    """Secure session management using Streamlit's persistent session state."""

    def __init__(self):
        # Initialize session storage in Streamlit's session state
        if "session_storage" not in st.session_state:
            st.session_state.session_storage = {}

    def create_session(self) -> str:
        """
        Create a new secure session.

        Returns:
            str: New session ID
        """
        session_id = secrets.token_urlsafe(32)

        # Store session in Streamlit's persistent session state
        st.session_state.session_storage[session_id] = {
            "created_at": datetime.now(),
            "last_accessed": datetime.now(),
        }

        logger.info(f"Created new session: {session_id}")
        return session_id

    def validate_session(self, session_id: str) -> bool:
        """
        Validate a session ID and update last accessed time.
        Auto-recreates session if it doesn't exist to handle app restarts gracefully.

        Args:
            session_id: The session ID to validate

        Returns:
            bool: True if valid, False otherwise
        """
        try:
            # Initialize session storage if not exists
            if "session_storage" not in st.session_state:
                st.session_state.session_storage = {}

            # If session doesn't exist, create it (handles app restart gracefully)
            if session_id not in st.session_state.session_storage:
                logger.info(f"Session not found, recreating: {session_id}")
                st.session_state.session_storage[session_id] = {
                    "created_at": datetime.now(),
                    "last_accessed": datetime.now(),
                }
                return True

            session = st.session_state.session_storage[session_id]

            # Check session timeout
            if datetime.now() - session["last_accessed"] > timedelta(
                seconds=SecurityConfig.SESSION_TIMEOUT
            ):
                logger.info(f"Session timed out: {session_id}")
                del st.session_state.session_storage[session_id]
                return False

            # Update last accessed time
            session["last_accessed"] = datetime.now()
            return True

        except Exception as e:
            logger.error(f"Error validating session: {str(e)}")
            # On error, create new session to recover gracefully
            st.session_state.session_storage[session_id] = {
                "created_at": datetime.now(),
                "last_accessed": datetime.now(),
            }
            return True

    def end_session(self, session_id: str) -> None:
        """
        End a session by removing it.

        Args:
            session_id: The session ID to end
        """
        try:
            if (
                "session_storage" in st.session_state
                and session_id in st.session_state.session_storage
            ):
                del st.session_state.session_storage[session_id]
                logger.info(f"Session ended: {session_id}")
        except Exception as e:
            logger.error(f"Error ending session: {str(e)}")


def safe_log(message: str, sensitive: bool = False) -> None:
    """
    Safely log messages, handling sensitive data appropriately.

    Args:
        message: The message to log
        sensitive: Whether the message contains sensitive data
    """
    try:
        if sensitive and not os.getenv("DEBUG"):
            # Log placeholder for sensitive data
            logger.info("Sensitive data logged in debug mode only")
            return

        logger.info(message)

    except Exception as e:
        logger.error(f"Error logging message: {str(e)}")
