"""
Journey challenge answer validation utilities.

Provides input sanitization and validation for different challenge types
to ensure consistent answer handling across the Wonderland journey.
"""
import re
from typing import Optional


def normalize_answer(answer: str, challenge_type: str) -> str:
    """
    Normalize a challenge answer based on the challenge type.

    Args:
        answer: Raw user input
        challenge_type: Type of challenge (e.g., 'text_input', 'timeline_events', etc.)

    Returns:
        Normalized answer string

    Normalization rules:
    - Strip leading/trailing whitespace
    - Collapse multiple spaces to single space
    - Convert to lowercase for case-insensitive types
    - Remove special characters for certain types
    """
    if not answer:
        return ""

    # Strip leading/trailing whitespace
    normalized = answer.strip()

    # Collapse multiple whitespace characters to single space
    normalized = re.sub(r'\s+', ' ', normalized)

    # Type-specific normalization
    if challenge_type in ['text_input', 'riddle']:
        # Case-insensitive comparison for text inputs and riddles
        normalized = normalized.lower()
        # Remove common punctuation for more forgiving matching
        normalized = re.sub(r'[.,!?;:\'"()]', '', normalized)
    elif challenge_type == 'timeline_events':
        # Timeline events: normalize whitespace only (preserve case)
        pass
    elif challenge_type == 'photo_puzzle':
        # Photo puzzle: typically no text answer, but normalize if present
        normalized = normalized.lower()
    elif challenge_type == 'choice':
        # Multiple choice: case-insensitive, whitespace normalized
        normalized = normalized.lower()

    return normalized


def validate_answer_format(answer: str, challenge_type: str) -> tuple[bool, Optional[str]]:
    """
    Validate the format of a challenge answer.

    Args:
        answer: User input (already normalized)
        challenge_type: Type of challenge

    Returns:
        Tuple of (is_valid, error_message)
        - is_valid: True if answer format is valid
        - error_message: Error description if invalid, None if valid

    Validation rules:
    - Minimum length requirements
    - Maximum length limits
    - Format requirements (e.g., JSON for timeline_events)
    - Character restrictions
    """
    # Check for empty/whitespace-only answers
    if not answer or not answer.strip():
        return False, "Answer cannot be empty"

    # Minimum length check (prevent trivial answers)
    MIN_LENGTH = 1
    if len(answer.strip()) < MIN_LENGTH:
        return False, f"Answer must be at least {MIN_LENGTH} character(s)"

    # Type-specific validation
    if challenge_type == 'text_input':
        # Text input: 1-500 characters
        MAX_LENGTH = 500
        if len(answer) > MAX_LENGTH:
            return False, f"Answer too long (max {MAX_LENGTH} characters)"

    elif challenge_type == 'riddle':
        # Riddle: 1-200 characters
        MAX_LENGTH = 200
        if len(answer) > MAX_LENGTH:
            return False, f"Answer too long (max {MAX_LENGTH} characters)"

    elif challenge_type == 'timeline_events':
        # Timeline events: JSON array format
        import json
        try:
            events = json.loads(answer)
            if not isinstance(events, list):
                return False, "Timeline answer must be a list"
            if len(events) < 1:
                return False, "Timeline must contain at least one event"
            if len(events) > 20:
                return False, "Timeline cannot exceed 20 events"
        except json.JSONDecodeError as e:
            return False, f"Invalid timeline format: {str(e)}"

    elif challenge_type == 'choice':
        # Multiple choice: single character or short string
        MAX_LENGTH = 50
        if len(answer) > MAX_LENGTH:
            return False, f"Choice answer too long (max {MAX_LENGTH} characters)"

    elif challenge_type == 'photo_puzzle':
        # Photo puzzle: typically auto-completed, but validate if present
        MAX_LENGTH = 100
        if len(answer) > MAX_LENGTH:
            return False, f"Answer too long (max {MAX_LENGTH} characters)"

    # All checks passed
    return True, None


def compare_answers(
    user_answer: str,
    correct_answer: str,
    challenge_type: str,
    case_sensitive: bool = False
) -> bool:
    """
    Compare user answer with correct answer.

    Args:
        user_answer: User's submitted answer (raw)
        correct_answer: Expected correct answer
        challenge_type: Type of challenge
        case_sensitive: Whether to use case-sensitive comparison (overrides type default)

    Returns:
        True if answers match according to challenge rules
    """
    # Normalize both answers
    normalized_user = normalize_answer(user_answer, challenge_type)
    normalized_correct = normalize_answer(correct_answer, challenge_type)

    # Case sensitivity override
    if case_sensitive:
        normalized_user = user_answer.strip()
        normalized_correct = correct_answer.strip()

    # Direct comparison for most types
    if challenge_type in ['text_input', 'riddle', 'choice']:
        return normalized_user == normalized_correct

    # Special handling for timeline events (order matters)
    elif challenge_type == 'timeline_events':
        import json
        try:
            user_events = json.loads(user_answer)
            correct_events = json.loads(correct_answer)
            # Compare ordered lists
            return user_events == correct_events
        except (json.JSONDecodeError, TypeError):
            return False

    # Photo puzzle: typically auto-validated by frontend
    elif challenge_type == 'photo_puzzle':
        return True  # Always pass if reached this point

    # Default: exact match
    return normalized_user == normalized_correct


def sanitize_answer_for_storage(answer: str, challenge_type: str) -> str:
    """
    Sanitize answer before storing in database.

    Args:
        answer: User input
        challenge_type: Type of challenge

    Returns:
        Sanitized answer safe for database storage

    Sanitization:
    - Trim whitespace
    - Escape special characters
    - Limit length
    - Remove potential XSS vectors
    """
    import html

    # Basic trimming
    sanitized = answer.strip()

    # HTML escape to prevent XSS
    sanitized = html.escape(sanitized)

    # Length limits per type
    max_lengths = {
        'text_input': 500,
        'riddle': 200,
        'timeline_events': 5000,  # JSON can be longer
        'choice': 50,
        'photo_puzzle': 100,
    }
    max_length = max_lengths.get(challenge_type, 500)
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]

    return sanitized
