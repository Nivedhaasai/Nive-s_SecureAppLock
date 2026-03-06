"""
Nive'secureAppLock - PIN authentication.
Hashes and verifies 4-6 digit PINs using bcrypt.
"""

import bcrypt
from utils.logger import setup_logger

logger = setup_logger()


class PinError(Exception):
    """Raised for invalid PIN format."""


def validate_pin_format(pin: str) -> None:
    """Ensure PIN is 4-6 digits."""
    if not pin or not pin.isdigit() or not (4 <= len(pin) <= 6):
        raise PinError("PIN must be 4-6 digits.")


def set_pin(pin: str) -> str:
    """Hash a PIN and return the bcrypt hash as a UTF-8 string."""
    validate_pin_format(pin)
    hashed = bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt())
    logger.info("New PIN has been set.")
    return hashed.decode("utf-8")


def verify_pin(pin: str, stored_hash: str) -> bool:
    """Check a PIN against the stored bcrypt hash."""
    try:
        result = bcrypt.checkpw(pin.encode("utf-8"), stored_hash.encode("utf-8"))
        if result:
            logger.info("PIN authentication successful.")
        else:
            logger.warning("PIN authentication failed - incorrect PIN.")
        return result
    except Exception as e:
        logger.error("PIN verification error: %s", e)
        return False
