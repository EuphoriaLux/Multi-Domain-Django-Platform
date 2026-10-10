"""
Logging utilities for PII protection.

Provides functions for masking sensitive data (emails, phone numbers) in logs
while maintaining enough information for debugging.
"""
import re
import logging
from collections.abc import Mapping


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

    Only the email addresses themselves are replaced, in ``record.msg`` and in
    every argument, so "Speed Dating @ Urban Bar" or a multi-line report that
    happens to hold one address keeps the rest of its text. A non-string
    argument (the recipient list of an email, a ``User`` whose ``__str__`` is
    its address, an exception) is replaced by its masked ``str()`` only when
    that text holds an address; otherwise it is passed through untouched.

    An exception attached with ``exc_info`` is exported with its own message
    and stack trace, read from the exception object rather than from
    ``record.msg``. When any exception in the chain mentions an address, the
    record gets a masked stand-in chain with the same class names and
    tracebacks; the caller's exception is never touched.

    In production this runs on the OpenTelemetry handler that exports to
    Application Insights (attached in ``azureproject.telemetry_config``), not
    only on the ERROR-level console handler from ``LOGGING``.

    Usage:
        Add to logging config:
        'filters': {
            'pii_masking': {
                '()': 'azureproject.logging_utils.PIIMaskingFilter',
            }
        }
    """

    # Regex patterns for PII detection. \w is Unicode here, so internationalised
    # domains (jane@müller.de, which Django's EmailValidator accepts) match;
    # the TLD is letters only ([^\W\d_]).
    EMAIL_PATTERN = re.compile(
        r'\b[\w.%+-]+@[\w.-]+\.[^\W\d_]{2,}\b'
    )
    PHONE_PATTERN = re.compile(
        r'(\+?\d{1,4}[-.\s]?)?(\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{3,4}'
    )

    # Original exception class -> stand-in class with the same name and module.
    _stand_in_classes = {}

    def _mask_text(self, text):
        if '@' not in text:  # most records; skips the regex scan
            return text
        return self.EMAIL_PATTERN.sub(lambda m: mask_email(m.group(0)), text)

    @staticmethod
    def _exception_text(exc):
        try:
            return str(exc)
        except Exception:
            return ''

    @staticmethod
    def _exception_chain(exc):
        chain, pending = [], [exc]
        while pending:
            current = pending.pop()
            if current is None or any(current is seen for seen in chain):
                continue
            chain.append(current)
            pending.extend((current.__cause__, current.__context__))
        return chain

    def _mentions_address(self, exc):
        texts = [self._exception_text(exc)]
        texts.extend(str(note) for note in getattr(exc, '__notes__', None) or ())
        return any(self.EMAIL_PATTERN.search(text) for text in texts)

    def _stand_in(self, exc, memo):
        """A masked copy of ``exc`` and its chain, for the record only."""
        if exc is None:
            return None
        if id(exc) in memo:
            return memo[id(exc)]
        cls = type(exc)
        stand_in_cls = self._stand_in_classes.get(cls)
        if stand_in_cls is None:
            stand_in_cls = type(
                cls.__name__,
                (Exception,),
                {'__module__': cls.__module__, '__qualname__': cls.__qualname__},
            )
            self._stand_in_classes[cls] = stand_in_cls
        stand_in = stand_in_cls(self._mask_text(self._exception_text(exc)))
        memo[id(exc)] = stand_in
        stand_in.__traceback__ = exc.__traceback__
        stand_in.__cause__ = self._stand_in(exc.__cause__, memo)
        stand_in.__context__ = self._stand_in(exc.__context__, memo)
        # Assigning __cause__ sets this to True, so copy it last.
        stand_in.__suppress_context__ = exc.__suppress_context__
        notes = getattr(exc, '__notes__', None)
        if notes:
            stand_in.__notes__ = [self._mask_text(str(note)) for note in notes]
        return stand_in

    def _mask_exc_info(self, exc_info):
        if not isinstance(exc_info, tuple) or len(exc_info) != 3:
            return exc_info
        exc_type, exc, tb = exc_info
        if exc is None or not any(
            self._mentions_address(e) for e in self._exception_chain(exc)
        ):
            return exc_info
        # Keep the original type first: the OTel handler reports its __name__.
        return (exc_type, self._stand_in(exc, {}), tb)

    def _mask_value(self, value):
        """Return ``value`` with every email address in it masked.

        Never mutates ``value``: a list argument can be the live recipient
        list of the email being sent.
        """
        if isinstance(value, str):
            return self._mask_text(value)
        if value is None or isinstance(value, (int, float)):
            return value
        # Exact types: a namedtuple cannot be rebuilt from one iterable, so it
        # takes the str() path below like any other object.
        if type(value) in (list, tuple):
            return type(value)(self._mask_value(item) for item in value)
        if isinstance(value, Mapping):
            return {key: self._mask_value(item) for key, item in value.items()}
        try:
            text = str(value)
        except Exception:
            # Formatting the record will fail the same way, so it is never
            # exported; leave it for logging's own error handling.
            return value
        if self.EMAIL_PATTERN.search(text):
            return self._mask_text(text)
        return value

    def filter(self, record):
        """Filter log record to mask PII."""
        try:
            record.msg = self._mask_value(record.msg)
            # Note: Phone masking disabled by default as it may cause false positives
            # Uncomment if needed:
            # record.msg = self.PHONE_PATTERN.sub(
            #     lambda m: mask_phone(m.group(0)),
            #     record.msg
            # )

            # `logger.info("%(email)s", {...})` leaves a mapping in args, which
            # has to stay a mapping for the message to format.
            if record.args:
                record.args = self._mask_value(record.args)

            if record.exc_info:
                record.exc_info = self._mask_exc_info(record.exc_info)
            # Set when another handler already formatted the traceback.
            if isinstance(record.exc_text, str):
                record.exc_text = self._mask_text(record.exc_text)
        except Exception:
            # A filter that raises propagates into the caller's logging call.
            # Withhold the content rather than risk exporting it unmasked.
            record.msg = "[log message withheld: PII masking failed]"
            record.args = ()
            record.exc_info = None
            record.exc_text = None

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
