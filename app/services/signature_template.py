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
            f"<a href=\"{href}\" style=\"text-decoration:none\"><img alt=\"\" src=\"/static/signature/icons/{filename}\" width=\"16\" height=\"16\" style=\"display:inline-block; border:0; margin-left:6px\"></a>"
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
        <img src="/static/signature/profile.png" width="72" height="72" style="border-radius:999px; display:block; border:3px solid #000;" alt="" />
      </td>
      <td style="vertical-align:top;">
        <div style="font-size:22px; font-weight:800; line-height:1.1;">{p.name} | <span style="font-weight:800"> </span>{social_html}</div>
        <div style="font-size:12px; font-weight:700; margin-top:6px;">{p.title_line}</div>
        <div style="font-size:12px; margin-top:6px;">
          <span style="font-weight:700">{p.phone}</span> | <a href="mailto:{p.email}" style="color:#0b57d0; text-decoration:underline;">{p.email}</a>
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
</div>
""".strip()


def build_signature_text(p: SignatureProfile) -> str:
    office_lines = "\n".join([f"{label}: {addr}" for label, addr in p.offices])
    return (
        "Thank You.\n"
        "Yours Truly,\n\n"
        f"{p.name}\n"
        f"{p.title_line}\n"
        f"{p.phone} | {p.email}\n\n"
        f"{p.company}\n"
        "Office Locations:\n"
        f"{office_lines}\n\n"
        "Please note: All in-person meetings at the above locations must be booked in advance.\n"
    ).strip()
