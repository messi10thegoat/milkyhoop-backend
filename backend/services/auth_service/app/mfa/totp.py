"""
TOTP (Time-based One-Time Password) Manager
ISO 27001:2022 - A.8.5 Secure Authentication

Implements RFC 6238 TOTP for Multi-Factor Authentication
Compatible with Google Authenticator, Authy, Microsoft Authenticator
"""

import pyotp
import qrcode
import qrcode.image.svg
import base64
import io
import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TOTPSetupResult:
    """Result of TOTP setup operation"""

    secret: str
    qr_code_uri: str
    qr_code_svg: str
    qr_code_base64: str
    manual_entry_key: str


class TOTPManager:
    """
    TOTP Manager for Multi-Factor Authentication

    Security considerations:
    - Uses SHA1 (RFC 6238 standard, widely compatible)
    - 30-second time window
    - 6-digit codes
    - Allows 1 window tolerance for clock drift
    """

    ISSUER = "MilkyHoop"
    DIGITS = 6
    INTERVAL = 30  # seconds
    ALGORITHM = "SHA1"  # RFC 6238 standard
    VALID_WINDOW = 1  # Allow 1 interval before/after for clock drift

    @classmethod
    def generate_secret(cls) -> str:
        """
        Generate a new TOTP secret

        Returns:
            Base32-encoded secret (32 characters)
        """
        return pyotp.random_base32()

    @classmethod
    def setup_totp(
        cls, user_email: str, secret: Optional[str] = None
    ) -> TOTPSetupResult:
        """
        Setup TOTP for a user

        Args:
            user_email: User's email for identification
            secret: Optional existing secret (generates new if None)

        Returns:
            TOTPSetupResult with all necessary setup information
        """
        if secret is None:
            secret = cls.generate_secret()

        # Create TOTP object
        totp = pyotp.TOTP(secret, digits=cls.DIGITS, interval=cls.INTERVAL)

        # Generate provisioning URI for QR code
        provisioning_uri = totp.provisioning_uri(
            name=user_email, issuer_name=cls.ISSUER
        )

        # Generate QR code as SVG
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(provisioning_uri)
        qr.make(fit=True)

        # Create SVG image
        img = qr.make_image(fill_color="black", back_color="white")

        # Convert to base64 PNG for embedding
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        qr_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        # Also create SVG version
        svg_buffer = io.BytesIO()
        svg_img = qrcode.make(provisioning_uri, image_factory=qrcode.image.svg.SvgImage)
        svg_img.save(svg_buffer)
        svg_buffer.seek(0)
        qr_svg = svg_buffer.getvalue().decode("utf-8")

        # Format secret for manual entry (groups of 4)
        manual_key = " ".join([secret[i : i + 4] for i in range(0, len(secret), 4)])

        logger.info("TOTP setup generated for user (email masked)")

        return TOTPSetupResult(
            secret=secret,
            qr_code_uri=provisioning_uri,
            qr_code_svg=qr_svg,
            qr_code_base64=f"data:image/png;base64,{qr_base64}",
            manual_entry_key=manual_key,
        )

    @classmethod
    def verify_code(cls, secret: str, code: str) -> bool:
        """
        Verify a TOTP code

        Args:
            secret: User's TOTP secret
            code: 6-digit code from authenticator app

        Returns:
            True if code is valid
        """
        if not secret or not code:
            return False

        # Remove any spaces/dashes from code
        code = code.replace(" ", "").replace("-", "")

        # Validate code format
        if not code.isdigit() or len(code) != cls.DIGITS:
            logger.warning("Invalid TOTP code format")
            return False

        try:
            totp = pyotp.TOTP(secret, digits=cls.DIGITS, interval=cls.INTERVAL)

            # Verify with window tolerance for clock drift
            is_valid = totp.verify(code, valid_window=cls.VALID_WINDOW)

            if is_valid:
                logger.info("TOTP verification successful")
            else:
                logger.warning("TOTP verification failed - invalid code")

            return is_valid

        except Exception as e:
            logger.error(f"TOTP verification error: {e}")
            return False

    @classmethod
    def get_current_code(cls, secret: str) -> str:
        """
        Get current TOTP code (for testing/debugging only)

        Args:
            secret: TOTP secret

        Returns:
            Current 6-digit code
        """
        totp = pyotp.TOTP(secret, digits=cls.DIGITS, interval=cls.INTERVAL)
        return totp.now()

    @classmethod
    def get_time_remaining(cls) -> int:
        """
        Get seconds remaining until next code

        Returns:
            Seconds until code changes
        """
        import time

        return cls.INTERVAL - (int(time.time()) % cls.INTERVAL)
