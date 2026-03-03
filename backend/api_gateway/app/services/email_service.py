"""
Email Service — Resend Integration
====================================
Sends verification and notification emails via Resend API.
"""
import os
import logging

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://milkyhoop.com")
FROM_EMAIL = "MilkyHoop <noreply@milkyhoop.com>"


async def send_verification_email(email: str, code: str, magic_link: str) -> bool:
    """
    Send verification email with 6-digit code + magic link.
    Returns True on success, False on failure.
    """
    try:
        import resend
        resend.api_key = RESEND_API_KEY

        if not RESEND_API_KEY:
            logger.warning("RESEND_API_KEY not set, skipping email send")
            logger.warning(f"[DEV] Verification code for {email}: {code}")
            logger.warning(f"[DEV] Magic link: {magic_link}")
            return True

        html_content = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #1A1A1A;">
  <div style="text-align: center; margin-bottom: 32px;">
    <h1 style="font-size: 24px; font-weight: 700; margin: 0;">MilkyHoop</h1>
    <p style="font-size: 14px; color: #6B6B6B; margin: 4px 0 0;">Financial Automation Tools</p>
  </div>

  <h2 style="font-size: 20px; font-weight: 600; margin-bottom: 8px;">Verifikasi Email Anda</h2>
  <p style="font-size: 15px; color: #4A4A4A; line-height: 1.5;">
    Masukkan kode berikut untuk memverifikasi email Anda:
  </p>

  <div style="background: #F7F6F3; border-radius: 12px; padding: 24px; text-align: center; margin: 24px 0;">
    <span style="font-size: 36px; font-weight: 700; letter-spacing: 8px; color: #1A1A1A;">
      {code}
    </span>
  </div>

  <p style="font-size: 15px; color: #4A4A4A; line-height: 1.5;">
    Atau klik tombol di bawah:
  </p>

  <div style="text-align: center; margin: 24px 0;">
    <a href="{magic_link}" style="display: inline-block; background: #1A1A1A; color: #FFFFFF; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-size: 15px; font-weight: 600;">
      Verifikasi Email
    </a>
  </div>

  <p style="font-size: 13px; color: #9A9A9A; line-height: 1.5;">
    Kode ini berlaku selama 15 menit. Jika Anda tidak mendaftar di MilkyHoop, abaikan email ini.
  </p>

  <hr style="border: none; border-top: 1px solid #E8E6E1; margin: 32px 0;">
  <p style="font-size: 12px; color: #9A9A9A; text-align: center;">
    &copy; 2026 MilkyHoop. Financial Automation Tools.
  </p>
</body>
</html>
"""
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": email,
            "subject": "Verifikasi Email MilkyHoop — Kode: " + code,
            "html": html_content,
        })
        logger.info(f"Verification email sent to {email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send verification email to {email}: {e}")
        return False


async def send_login_suggestion_email(email: str) -> bool:
    """
    Send to existing users when someone tries to register their email.
    Anti-enumeration: attacker cannot distinguish this from verification email.
    """
    try:
        import resend
        resend.api_key = RESEND_API_KEY

        if not RESEND_API_KEY:
            logger.warning("RESEND_API_KEY not set, skipping email send")
            logger.warning(f"[DEV] Login suggestion email for existing user: {email}")
            return True

        login_link = f"{FRONTEND_URL}"

        html_content = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #1A1A1A;">
  <div style="text-align: center; margin-bottom: 32px;">
    <h1 style="font-size: 24px; font-weight: 700; margin: 0;">MilkyHoop</h1>
    <p style="font-size: 14px; color: #6B6B6B; margin: 4px 0 0;">Financial Automation Tools</p>
  </div>

  <h2 style="font-size: 20px; font-weight: 600; margin-bottom: 8px;">Percobaan Pendaftaran</h2>
  <p style="font-size: 15px; color: #4A4A4A; line-height: 1.5;">
    Seseorang mencoba mendaftar menggunakan email Anda di MilkyHoop.
    Jika ini Anda, silakan login menggunakan password yang sudah ada:
  </p>

  <div style="text-align: center; margin: 24px 0;">
    <a href="{login_link}" style="display: inline-block; background: #1A1A1A; color: #FFFFFF; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-size: 15px; font-weight: 600;">
      Login ke MilkyHoop
    </a>
  </div>

  <p style="font-size: 13px; color: #9A9A9A; line-height: 1.5;">
    Jika Anda tidak melakukan ini, abaikan email ini. Akun Anda tetap aman.
  </p>

  <hr style="border: none; border-top: 1px solid #E8E6E1; margin: 32px 0;">
  <p style="font-size: 12px; color: #9A9A9A; text-align: center;">
    &copy; 2026 MilkyHoop. Financial Automation Tools.
  </p>
</body>
</html>
"""
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": email,
            "subject": "Percobaan Pendaftaran di MilkyHoop",
            "html": html_content,
        })
        logger.info(f"Login suggestion email sent to {email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send login suggestion email to {email}: {e}")
        return False
