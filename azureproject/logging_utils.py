"""
Logging utilities for PII protection.

Provides functions for masking sensitive data (emails, phone numbers) in logs
while maintaining enough information for debugging.
"""
import re
import logging
from functools import lru_cache


def mask_email(email: str) -> str:
    """
    Mask an email address for safe logging.

    Examples:
        john.doe@example.com -> j***e@e***.com
        a@b.io -> a***@b***.io
        test@company.co.uk -> t***t@c***.co.uk
    """
    if not email or '@' not in email:
        return email or '[empty]'

    try:
        local, domain = email.split('@', 1)

        # Mask local part (keep first and last char if long enough)
        if len(local) <= 2:
            masked_local = local[0] + '***'
        else:
            masked_local = local[0] + '***' + local[-1]

        # Mask domain (keep first char and TLD)
        domain_parts = domain.rsplit('.', 1)
        if len(domain_parts) == 2:
            domain_name, tld = domain_parts
            if len(domain_name) <= 1:
                masked_domain = domain_name + '***.' + tld
            else:
                masked_domain = domain_name[0] + '***.' + tld
        else:
            masked_domain = domain[0] + '***'

        return f"{masked_local}@{masked_domain}"
    except Exception:
        return '[invalid-email]'


def mask_phone(phone: str) -> str:
    """
    Mask a phone number for safe logging.

    Keeps the country code and last 2 digits.

    Examples:
        +352 621 123 456 -> +352 *** ** 56
        +1234567890 -> +1*** ***90
    """
    if not phone:
        return '[empty]'

    # Remove all non-digit characters except leading +
    digits = re.sub(r'[^\d+]', '', phone)

    if len(digits) < 4:
        return '***'

    # Keep first 3-4 chars (country code) and last 2 digits
    if digits.startswith('+'):
        # Keep + and country code (assume 2-3 digits)
        return digits[:4] + ' *** **' + digits[-2:]
    else:
        return digits[:2] + '*** **' + digits[-2:]


class PIIMaskingFilter(logging.Filter):
    """
    Logging filter that masks PII (emails, phone numbers) in log messages.

    Usage:
        Add to logging config:
        'filters': {
            'pii_masking': {
                '()': 'azureproject.logging_utils.PIIMaskingFilter',
            }
        }
    """

    # Regex patterns for PII detection
    EMAIL_PATTERN = re.compile(
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    )
    PHONE_PATTERN = re.compile(
        r'(\+?\d{1,4}[-.\s]?)?(\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{3,4}'
    )

    def filter(self, record):
        """Filter log record to mask PII."""
        if isinstance(record.msg, str):
            # Mask emails
            record.msg = self.EMAIL_PATTERN.sub(
                lambda m: mask_email(m.group(0)),
                record.msg
            )
            # Note: Phone masking disabled by default as it may cause false positives
            # Uncomment if needed:
            # record.msg = self.PHONE_PATTERN.sub(
            #     lambda m: mask_phone(m.group(0)),
            #     record.msg
            # )

        # Also handle args
        if record.args:
            record.args = tuple(
                mask_email(arg) if isinstance(arg, str) and '@' in arg else arg
                for arg in record.args
            )

        return True


# Convenience function for explicit masking in code
def log_user_action(logger, level, action, user=None, user_id=None, email=None, **kwargs):
    """
    Log a user action with masked PII.

    Usage:
        log_user_action(logger, logging.INFO, "Profile updated",
                       user_id=request.user.id, email=request.user.email)
    """
    user_info = []

    if user_id:
        user_info.append(f"user_id={user_id}")

    if email:
        user_info.append(f"email={mask_email(email)}")
    elif user and hasattr(user, 'email'):
        user_info.append(f"email={mask_email(user.email)}")

    if user and hasattr(user, 'id') and not user_id:
        user_info.append(f"user_id={user.id}")

    user_str = ', '.join(user_info) if user_info else 'unknown'
    extra_str = ', '.join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ''

    message = f"{action} ({user_str})"
    if extra_str:
        message += f" - {extra_str}"

    logger.log(level, message)
