from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SignatureProfile:
    name: str
    title_line: str
    phone: str
    email: str
    company: str
    offices: list[tuple[str, str]]  # (label, address)

    # Social links (optional)
    facebook: str = ""
    youtube: str = ""
    linkedin: str = ""
    instagram: str = ""
    whatsapp: str = ""
    discord: str = ""


DISCLAIMER_HTML = (
    "The information and details contained in this electronic mail message and any electronic files attached to it "
    "may be confidential information and may also be the subject of estate agent professional privilege and/or public "
    "interest immunity. If you are not the intended recipient, you are required to delete it. Any use, disclosure, "
    "copying or downloading any attachments of this message is unauthorized. If you have received this electronic "
    "message in error, please inform "
    "<a href=\"mailto:Admin@donspremier.com.au\" style=\"color:#0b57d0; text-decoration:underline;\">"
    "Admin@donspremier.com.au</a>. "
    "The sender doesn't represent or warrant that any attached files are free from computer viruses or other defects. "
    "The user assumes all responsibility for any loss or damage resulting directly or indirectly from the use of the "
    "attached files of this electronic mail message."
)

DISCLAIMER_TEXT = (
    "The information and details contained in this electronic mail message and any electronic files attached to it may be "
    "confidential information and may also be the subject of estate agent professional privilege and/or public interest immunity. "
    "If you are not the intended recipient, you are required to delete it. Any use, disclosure, copying or downloading any "
    "attachments of this message is unauthorized. If you have received this electronic message in error, please inform "
    "Admin@donspremier.com.au. The sender doesn't represent or warrant that any attached files are free from computer viruses or "
    "other defects. The user assumes all responsibility for any loss or damage resulting directly or indirectly from the use of "
    "the attached files of this electronic mail message."
)


def build_signature_html(p: SignatureProfile) -> str:
    """Build an app-managed signature HTML.

    This template is designed to closely match the user's provided screenshot while
    staying robust across email clients (table layout, inline CSS, no external CSS).

    Images are referenced via local app paths (/static/signature/...). The send pipeline
    converts those into CID-related attachments, ensuring images render reliably in Gmail.
    """

    # Office blocks
    office_cols = []
    for label, addr in p.offices:
        office_cols.append(
            f"""
            <td style="padding:0 10px 0 0; vertical-align:top;">
              <div style="display:inline-block; border:1px solid #000; border-radius:999px; padding:2px 10px; font-weight:700; font-size:11px; letter-spacing:0.3px;">
                {label}
              </div>
              <div style="margin-top:6px; font-size:11px; line-height:1.35;">
                {addr}
              </div>
            </td>
            """.strip()
        )
    offices_html = "".join(office_cols)

    # Social icons (links optional)
    def icon(href: str, filename: str) -> str:
        if not href:
            href = "#"
        return (
            f"<a href=\"{href}\" style=\"text-decoration:none\">"
            f"<img alt=\"\" src=\"/static/signature/icons/{filename}\" width=\"16\" height=\"16\" "
            f"style=\"display:inline-block; border:0; margin-left:6px; vertical-align:middle;\">"
            f"</a>"
        )

    social_html = (
        icon(p.facebook, "facebook.png")
        + icon(p.youtube, "youtube.png")
        + icon(p.linkedin, "linkedin.png")
        + icon(p.instagram, "instagram.png")
        + icon(p.whatsapp, "whatsapp.png")
        + icon(p.discord, "discord.png")
    )

    # Signature HTML
    return f"""
<div style="font-family:Arial, Helvetica, sans-serif; color:#000;">
  <div style="font-size:13px; line-height:1.4;">
    <div>Thank You.</div>
    <div>Yours Truly,</div>
  </div>

  <table cellpadding="0" cellspacing="0" border="0" style="margin-top:10px; border-collapse:collapse;">
    <tr>
      <td style="vertical-align:top; padding-right:14px;">
        <img src="/static/signature/profile.png" width="72" height="72"
             style="border-radius:999px; display:block; border:3px solid #000;" alt="" />
      </td>
      <td style="vertical-align:top;">
        <div style="font-size:22px; font-weight:800; line-height:1.1;">
          {p.name}
          <span style="font-weight:800;">&nbsp;</span>
          {social_html}
        </div>

        <!-- title_line can include a <br> to match your screenshot (two-line title) -->
        <div style="font-size:12px; font-weight:700; margin-top:6px; line-height:1.35;">
          {p.title_line}
        </div>

        <div style="font-size:12px; margin-top:6px;">
          <span style="font-weight:700">{p.phone}</span> |
          <a href="mailto:{p.email}" style="color:#0b57d0; text-decoration:underline;">{p.email}</a>
        </div>
      </td>
    </tr>
  </table>

  <div style="margin-top:14px; font-weight:900; text-decoration:underline; font-size:16px;">{p.company}</div>

  <div style="margin-top:8px; font-weight:700; font-size:13px;">Office Locations:</div>
  <table cellpadding="0" cellspacing="0" border="0" style="margin-top:8px; border-collapse:collapse;">
    <tr>
      {offices_html}
    </tr>
  </table>

  <div style="margin-top:10px; font-size:11px;">
    Please note: All in-person meetings at the above locations must be booked in advance.
  </div>

  <div style="margin-top:12px;">
    <img src="/static/signature/banner.png" alt="" style="width:100%; max-width:520px; height:auto; display:block;" />
  </div>

  <div style="margin-top:10px; font-size:9px; color:#666; line-height:1.45;">
    {DISCLAIMER_HTML}
  </div>
</div>
""".strip()


def build_signature_text(p: SignatureProfile) -> str:
    office_lines = "\n".join([f"{label}: {addr}" for label, addr in p.offices])
    return (
        "Thank You.\n"
        "Yours Truly,\n\n"
        f"{p.name}\n"
        f"{p.title_line.replace('<br>', ' | ')}\n"
        f"{p.phone} | {p.email}\n\n"
        f"{p.company}\n"
        "Office Locations:\n"
        f"{office_lines}\n\n"
        "Please note: All in-person meetings at the above locations must be booked in advance.\n\n"
        f"{DISCLAIMER_TEXT}"
    ).strip()
