"""Streamlit application with security controls."""

import os
from datetime import datetime
import logging
import json
import streamlit as st
from typing import Dict, Any, Optional
from utils import clear_input, show_empty_container, show_footer
from connections import Connections
from security.middleware import validate_input, error_handler, rate_limit
from security.security_config import SecurityConfig, safe_log, SessionManager

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize connections and session management
lambda_client = Connections.lambda_client
session_manager = SessionManager()


@error_handler
@rate_limit(max_calls=60, time_window=60)
@validate_input
def get_response(user_input: str, session_id: str) -> Dict[str, Any]:
    """
    Get response from Lambda with security controls.

    Args:
        user_input: User's question
        session_id: Session identifier

    Returns:
        Lambda response
    """
    try:
        safe_log(f"Processing request for session: {session_id}")

        payload = {"body": {"query": user_input, "session_id": session_id}}

        lambda_function_name = Connections.lambda_function_name
        safe_log(f"Invoking lambda: {lambda_function_name}")

        response = lambda_client.invoke(
            FunctionName=lambda_function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )

        response_output = json.loads(response["Payload"].read().decode("utf-8"))
        safe_log("Lambda response received")

        return response_output

    except Exception as e:
        safe_log(f"Error getting response: {str(e)}")
        raise


def header() -> None:
    """Configure secure app header."""
    # Set secure page config
    st.set_page_config(
        page_title="security-advisory-assistant",
        page_icon=":computer:",
        layout="centered",
        menu_items={
            "Get Help": None,
            "Report a bug": None,
            "About": "Security Advisory Assistant",
        },
    )

    # Add security headers
    for header, value in SecurityConfig.SECURE_HEADERS.items():
        st.markdown(
            f"<meta http-equiv='{header}' content='{value}'>", unsafe_allow_html=True
        )

    # Create layout
    col1, col2 = st.columns([1, 3])

    with col1:
        try:
            st.image(
                "./cna_guru_example.png",
                width=150,
                output_format="PNG",  # Enforce PNG format
            )
        except Exception as e:
            st.write("🤖")
            safe_log(f"Image load error: {str(e)}")

    with col2:
        st.markdown("Ask me questions. Share my wisdom.")

    st.write("#### describe your vulnerability")
    st.write("-----")


def initialization() -> None:
    """Initialize secure session state."""
    if "session_id" not in st.session_state:
        # Create new secure session
        st.session_state.session_id = session_manager.create_session()
        st.session_state.questions = []
        st.session_state.answers = []

    if "temp" not in st.session_state:
        st.session_state.temp = ""

    if "cache" not in st.session_state:
        st.session_state.cache = {}


def show_message() -> None:
    """Display messages with security controls."""
    # Get user input
    user_input = st.text_input(
        "# **Question:** 👇", "", key="input", max_chars=SecurityConfig.MAX_INPUT_LENGTH
    )

    # New conversation button
    new_conversation = st.button("New Conversation", key="clear", on_click=clear_input)

    if new_conversation:
        # End old session
        if "session_id" in st.session_state:
            session_manager.end_session(st.session_state.session_id)

        # Create new session
        st.session_state.session_id = session_manager.create_session()
        st.session_state.user_input = ""
        st.session_state.questions = []
        st.session_state.answers = []

    if user_input:
        session_id = st.session_state.session_id

        # Validate session
        if not session_manager.validate_session(session_id):
            st.error("Session expired. Please start a new conversation.")
            return

        with st.spinner("Asking the security-advisory-assistant..."):
            try:
                vertical_space = show_empty_container()
                vertical_space.empty()

                response_output = get_response(user_input, session_id)

                st.write("-------")

                # Handle potential error responses
                if "error" in response_output:
                    st.error(f"Error: {response_output.get('error', 'Unknown error')}")
                    return

                # Extract answer and source with better validation
                answer_text = response_output.get("answer", "")
                source_text = response_output.get("source", "")

                # Check if we got a valid response
                if not answer_text:
                    st.error(
                        "No response received from the assistant. Please try again."
                    )
                    return

                # Format source
                if source_text and source_text.startswith("SELECT"):
                    source_text = f"_{source_text}_"

                # Build complete response
                formatted_answer = "**Answer**: \n\n" + answer_text
                if source_text:
                    formatted_answer += "\n\n **Source**:" + "\n\n" + source_text

                st.session_state.questions.append(user_input)
                st.session_state.answers.append(formatted_answer)

            except Exception as e:
                safe_log(f"Error processing message: {str(e)}")
                st.error("An error occurred processing your request.")

    if st.session_state.get("answers"):
        for i in range(len(st.session_state["answers"]) - 1, -1, -1):
            with st.chat_message(
                name="human",
                avatar="👤",
            ):
                st.markdown(st.session_state["questions"][i])

            with st.chat_message(name="ai", avatar="🤖"):
                st.markdown(st.session_state["answers"][i])


def main() -> None:
    """Run Streamlit app with security controls."""
    try:
        header()
        initialization()
        show_message()
        show_footer()

    except Exception as e:
        safe_log(f"Application error: {str(e)}")
        # Show more detailed error information for debugging
        st.error(f"An error occurred: {str(e)}")
        st.error("Check the logs for more details.")
        # Also print to console for debugging
        print(f"DEBUG - Application error: {str(e)}")
        import traceback

        print(f"DEBUG - Traceback: {traceback.format_exc()}")


if __name__ == "__main__":
    main()
