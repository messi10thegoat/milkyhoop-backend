"""
Email Service — Resend Integration
====================================
Sends verification and notification emails via Resend API.
"""
import os
import logging

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")


class EmailDeliveryUnavailable(RuntimeError):
    """Pengiriman email tak bisa dilakukan — konfigurasinya tidak ada.

    KENAPA INI DILEMPAR, BUKAN DIKEMBALIKAN SEBAGAI False/True
    ----------------------------------------------------------
    Sebelum ini, kunci yang kosong dijawab dengan `return True` — SUKSES —
    disertai `logger.warning`. Akibatnya seluruh sistem meyakini email
    terkirim: layar pendaftaran menampilkan "Cek email Anda", tak ada email
    yang datang, dan aplikasi melaporkan berhasil.

    [LOG] 32 baris "RESEND_API_KEY not set, skipping email send" terkumpul
    selama berminggu-minggu. Sinyalnya ADA, TERLIHAT, dan BERULANG — yang tak
    ada adalah pembacanya. Persis pola /ready 503 (lihat tiket observability):
    memperbaiki logging tanpa alerting hanya menambah baris yang tak dibaca
    siapa pun. Karena itu urutannya tetap: ALERTING MENDAHULUI LOGGING.

    Ini instance lain dari "kegagalan dilaporkan sebagai keberhasilan" —
    kelas yang sama dengan bug A (`success:true` + `role_code:"VIEWER"` untuk
    peran yang tak ditemukan).
    """


def email_delivery_configured() -> bool:
    """Prasyarat, bisa diperiksa SEBELUM menulis apa pun ke basis data."""
    return bool(RESEND_API_KEY)
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
            # Dulu di sini: dua baris warning berisi KODE VERIFIKASI dan MAGIC
            # LINK, lalu `return True`. Dua cacat sekaligus — sukses palsu, dan
            # rahasia tertulis ke log. Keduanya hilang bersama lemparan ini.
            raise EmailDeliveryUnavailable(
                "RESEND_API_KEY tidak terpasang — email verifikasi tidak dapat dikirim."
            )

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

    except EmailDeliveryUnavailable:
        raise
    except Exception as e:
        # DULU: return False — dan pemanggil TIDAK PERNAH memeriksanya, jadi
        # kunci yang ada tapi ditolak (salah, dicabut, kredit habis) tetap
        # menghasilkan "Cek email Anda". Kunci kosong hanyalah satu cara jalur
        # ini gagal diam-diam; ini caranya yang akan datang.
        logger.error(f"Gagal mengirim email verifikasi ke {email}: {e}")
        raise EmailDeliveryUnavailable(
            f"Layanan email menolak permintaan: {e}"
        ) from e


async def send_login_suggestion_email(email: str) -> bool:
    """
    Send to existing users when someone tries to register their email.
    Anti-enumeration: attacker cannot distinguish this from verification email.
    """
    try:
        import resend
        resend.api_key = RESEND_API_KEY

        if not RESEND_API_KEY:
            raise EmailDeliveryUnavailable(
                "RESEND_API_KEY tidak terpasang — email saran-masuk tidak dapat dikirim."
            )
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

    except EmailDeliveryUnavailable:
        raise
    except Exception as e:
        logger.error(f"Gagal mengirim email saran-masuk ke {email}: {e}")
        raise EmailDeliveryUnavailable(f"Layanan email menolak permintaan: {e}") from e


async def send_invitation_email(email: str, invite_link: str, role_name: str = None,
                                tenant_name: str = None) -> str:
    """Kirim email undangan tim via Resend. KEMBALIKAN message id Resend.

    Melempar EmailDeliveryUnavailable bila kunci tak ada / Resend menolak — pemanggil
    WAJIB memeriksa dan melaporkan JUJUR (invite_link tetap dikembalikan, jangan
    laporkan 'terkirim' ke ruang hampa). Sama seperti send_verification_email.
    """
    try:
        import resend
        resend.api_key = RESEND_API_KEY

        if not RESEND_API_KEY:
            raise EmailDeliveryUnavailable(
                "RESEND_API_KEY tidak terpasang — email undangan tidak dapat dikirim."
            )

        _peran = f" sebagai <strong>{role_name}</strong>" if role_name else ""
        _tim = tenant_name or "tim di MilkyHoop"
        html_content = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #1A1A1A;">
  <div style="text-align: center; margin-bottom: 32px;">
    <h1 style="font-size: 24px; font-weight: 700; margin: 0;">MilkyHoop</h1>
    <p style="font-size: 14px; color: #6B6B6B; margin: 4px 0 0;">Financial Automation Tools</p>
  </div>
  <h2 style="font-size: 20px; font-weight: 600; margin-bottom: 8px;">Undangan Bergabung</h2>
  <p style="font-size: 15px; color: #4A4A4A; line-height: 1.5;">
    Anda diundang bergabung ke <strong>{_tim}</strong>{_peran}. Klik tombol di bawah untuk menerima undangan.
  </p>
  <div style="text-align: center; margin: 24px 0;">
    <a href="{invite_link}" style="display: inline-block; background: #1A1A1A; color: #FFFFFF; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-size: 15px; font-weight: 600;">
      Terima Undangan
    </a>
  </div>
  <p style="font-size: 13px; color: #9A9A9A; line-height: 1.5;">
    Atau salin tautan ini: {invite_link}<br>Undangan berlaku selama 7 hari.
  </p>
  <hr style="border: none; border-top: 1px solid #E8E6E1; margin: 32px 0;">
  <p style="font-size: 12px; color: #9A9A9A; text-align: center;">&copy; 2026 MilkyHoop. Financial Automation Tools.</p>
</body>
</html>
"""
        r = resend.Emails.send({
            "from": FROM_EMAIL,
            "to": email,
            "subject": "Undangan Bergabung — MilkyHoop",
            "html": html_content,
        })
        mid = r.get("id") if isinstance(r, dict) else getattr(r, "id", None)
        logger.info(f"Invitation email sent to {email} (id={mid})")
        return mid

    except EmailDeliveryUnavailable:
        raise
    except Exception as e:
        logger.error(f"Gagal mengirim email undangan ke {email}: {e}")
        raise EmailDeliveryUnavailable(f"Layanan email menolak permintaan: {e}")


async def send_invite_code_email(email: str, code: str) -> bool:
    """Kode 6 digit untuk MEMBUAT AKUN dari undangan (26 Sep 2026, V311).

    Dikirim ke email UNDANGAN, bukan ke pemanggil: token undangan membuktikan
    undangan, kode ini membuktikan kepemilikan email. Kunci Resend kosong ->
    EmailDeliveryUnavailable (bukan sukses palsu; kode TIDAK ditulis ke log).
    """
    import resend
    resend.api_key = RESEND_API_KEY
    if not RESEND_API_KEY:
        raise EmailDeliveryUnavailable(
            "RESEND_API_KEY tidak terpasang — kode undangan tidak dapat dikirim."
        )
    html_content = f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #1A1A1A;">
  <h1 style="font-size: 24px; font-weight: 700; text-align: center;">MilkyHoop</h1>
  <h2 style="font-size: 20px; font-weight: 600;">Kode untuk menerima undangan tim</h2>
  <p style="font-size: 15px; color: #4A4A4A;">Masukkan kode ini untuk membuat akun dan bergabung:</p>
  <div style="background: #F7F6F3; border-radius: 12px; padding: 24px; text-align: center; margin: 24px 0;">
    <span style="font-size: 36px; font-weight: 700; letter-spacing: 8px;">{code}</span>
  </div>
  <p style="font-size: 13px; color: #9A9A9A;">Kode berlaku 15 menit. Jika Anda tidak meminta kode ini, abaikan email ini — tidak ada akun yang dibuat tanpa kode.</p>
</body></html>
"""
    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": email,
        "subject": "Kode undangan MilkyHoop: " + code,
        "html": html_content,
    })
    logger.info("Invite code email sent")
    return True
