"""
HTML/CSS Headless Playwright Graphic Card Generator for hub.crush.lu.

Generates agency-grade, high-conversion 1080x1080px PNG image cards:
- CSS3 glassmorphism (backdrop-filter: blur)
- Radial ambient lighting & neon drop-shadows
- Google Fonts ('Playfair Display', 'Outfit', 'Plus Jakarta Sans')
- Native SVG iconography & zero glyph truncation
- Fast Playwright Headless Chromium rendering (<200ms)
"""
import io
import os
import logging
from playwright.sync_api import sync_playwright
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


def _render_html_to_png(html_content: str) -> bytes:
    """Render HTML string to 1080x1080px PNG using Playwright Headless Chromium."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1080, "height": 1080}, device_scale_factor=1)
        page = context.new_page()
        page.set_content(html_content, wait_until="networkidle")
        png_bytes = page.screenshot(type="png")
        browser.close()
        return png_bytes


def generate_event_flyer(
    title="Soirée rencontre Crush.lu & dégustation de vins",
    date_str="Jeudi 30 Juillet @ 19:30",
    location="Casino 2000, Mondorf-les-Bains",
    background_url: str = None
) -> str:
    """Generate a 1080x1080px agency-grade event flyer card."""
    bg_style = f"background-image: url('{background_url}'); background-size: cover; background-position: center;" if background_url else ""

    html = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
      <meta charset="UTF-8">
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&family=Playfair+Display:ital,wght@0,700;1,600&display=swap" rel="stylesheet">
      <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
          width: 1080px;
          height: 1080px;
          background: #0b0b14;
          font-family: 'Outfit', sans-serif;
          color: #ffffff;
          display: flex;
          align-items: center;
          justify-content: center;
          overflow: hidden;
          position: relative;
        }}
        .bg-layer {{
          position: absolute;
          inset: 0;
          {bg_style}
          filter: blur(8px) brightness(0.6);
          transform: scale(1.05);
        }}
        .radial-glow-1 {{
          position: absolute;
          top: -150px;
          left: -150px;
          width: 600px;
          height: 600px;
          background: radial-gradient(circle, rgba(225, 29, 72, 0.45) 0%, rgba(0, 0, 0, 0) 70%);
          border-radius: 50%;
        }}
        .radial-glow-2 {{
          position: absolute;
          bottom: -150px;
          right: -150px;
          width: 650px;
          height: 650px;
          background: radial-gradient(circle, rgba(147, 51, 234, 0.45) 0%, rgba(0, 0, 0, 0) 70%);
          border-radius: 50%;
        }}
        .card-container {{
          position: relative;
          z-index: 10;
          width: 960px;
          height: 960px;
          background: rgba(22, 22, 38, 0.75);
          backdrop-filter: blur(30px);
          -webkit-backdrop-filter: blur(30px);
          border: 1.5px solid rgba(255, 255, 255, 0.15);
          border-radius: 36px;
          padding: 70px 65px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          box-shadow: 0 40px 100px rgba(0, 0, 0, 0.8), inset 0 0 40px rgba(225, 29, 72, 0.15);
        }}
        .badge-pill {{
          display: inline-flex;
          align-items: center;
          gap: 10px;
          padding: 10px 22px;
          background: rgba(225, 29, 72, 0.2);
          border: 1px solid rgba(225, 29, 72, 0.6);
          border-radius: 50px;
          font-size: 20px;
          font-weight: 700;
          letter-spacing: 2px;
          color: #f43f5e;
          width: fit-content;
        }}
        .badge-dot {{
          width: 10px;
          height: 10px;
          background: #e11d48;
          border-radius: 50%;
          box-shadow: 0 0 10px #e11d48;
        }}
        .title-box {{
          margin-top: 25px;
          margin-bottom: 25px;
        }}
        .title {{
          font-family: 'Playfair Display', serif;
          font-size: 54px;
          font-weight: 700;
          line-height: 1.25;
          color: #ffffff;
          text-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
        }}
        .meta-list {{
          display: flex;
          flex-direction: column;
          gap: 20px;
        }}
        .meta-item {{
          display: flex;
          align-items: center;
          gap: 18px;
          padding: 22px 28px;
          background: rgba(255, 255, 255, 0.05);
          border: 1px solid rgba(255, 255, 255, 0.1);
          border-radius: 20px;
          font-size: 26px;
          font-weight: 600;
        }}
        .meta-icon {{
          width: 32px;
          height: 32px;
          fill: #38bdf8;
          flex-shrink: 0;
        }}
        .cta-row {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-top: 30px;
        }}
        .cta-button {{
          padding: 24px 48px;
          background: linear-gradient(135deg, #e11d48 0%, #9333ea 100%);
          border-radius: 24px;
          font-size: 26px;
          font-weight: 800;
          letter-spacing: 1px;
          color: #ffffff;
          box-shadow: 0 15px 40px rgba(225, 29, 72, 0.5);
          display: flex;
          align-items: center;
          gap: 14px;
        }}
        .brand-footer {{
          font-size: 24px;
          color: rgba(255, 255, 255, 0.5);
          font-weight: 600;
        }}
      </style>
    </head>
    <body>
      <div class="bg-layer"></div>
      <div class="radial-glow-1"></div>
      <div class="radial-glow-2"></div>

      <div class="card-container">
        <div>
          <div class="badge-pill">
            <div class="badge-dot"></div>
            CRUSH.LU EXCLUSIVE EVENT
          </div>
          <div class="title-box">
            <h1 class="title">{title}</h1>
          </div>
        </div>

        <div class="meta-list">
          <div class="meta-item">
            <svg class="meta-icon" viewBox="0 0 24 24"><path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.11 0-1.99.9-1.99 2L3 20c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V10h14v10zm0-12H5V6h14v2z"/></svg>
            <span>DATE : {date_str}</span>
          </div>
          <div class="meta-item">
            <svg class="meta-icon" style="fill: #f43f5e;" viewBox="0 0 24 24"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>
            <span>LIEU : {location}</span>
          </div>
        </div>

        <div class="cta-row">
          <div class="cta-button">
            <span>RÉSERVER MA PLACE</span>
            <span>&rarr;</span>
          </div>
          <div class="brand-footer">crush.lu</div>
        </div>
      </div>
    </body>
    </html>
    """
    png_bytes = _render_html_to_png(html)
    return _save_bytes_to_storage(png_bytes, "event_flyer")


def generate_kpi_card(title="CHIFFRES DE LA SEMAINE", stats=None) -> str:
    """Generate a 1080x1080px agency-grade KPI statistic graphic card."""
    if not stats:
        stats = [
            {"value": "+140", "label": "Nouveaux Inscrits cette semaine"},
            {"value": "52", "label": "Matchs Confirmés à Luxembourg"},
            {"value": "50 / 50", "label": "Taux de Parité Hommes / Femmes"},
        ]

    stats_html = ""
    for st in stats[:3]:
        stats_html += f"""
        <div class="stat-card">
          <div class="stat-value">{st.get("value", "")}</div>
          <div class="stat-label">{st.get("label", "")}</div>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
      <meta charset="UTF-8">
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&display=swap" rel="stylesheet">
      <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
          width: 1080px;
          height: 1080px;
          background: #0b0b14;
          font-family: 'Outfit', sans-serif;
          color: #ffffff;
          display: flex;
          align-items: center;
          justify-content: center;
          overflow: hidden;
          position: relative;
        }}
        .radial-glow-1 {{
          position: absolute;
          top: -150px;
          right: -150px;
          width: 650px;
          height: 650px;
          background: radial-gradient(circle, rgba(147, 51, 234, 0.45) 0%, rgba(0, 0, 0, 0) 70%);
          border-radius: 50%;
        }}
        .radial-glow-2 {{
          position: absolute;
          bottom: -150px;
          left: -150px;
          width: 600px;
          height: 600px;
          background: radial-gradient(circle, rgba(225, 29, 72, 0.45) 0%, rgba(0, 0, 0, 0) 70%);
          border-radius: 50%;
        }}
        .card-container {{
          position: relative;
          z-index: 10;
          width: 960px;
          height: 960px;
          background: rgba(22, 22, 38, 0.8);
          backdrop-filter: blur(30px);
          -webkit-backdrop-filter: blur(30px);
          border: 1.5px solid rgba(147, 51, 234, 0.4);
          border-radius: 36px;
          padding: 65px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          box-shadow: 0 40px 100px rgba(0, 0, 0, 0.8);
        }}
        .header-tag {{
          font-size: 22px;
          font-weight: 800;
          letter-spacing: 2px;
          color: #c084fc;
          text-transform: uppercase;
          margin-bottom: 12px;
        }}
        .title {{
          font-size: 46px;
          font-weight: 800;
          color: #ffffff;
        }}
        .stats-grid {{
          display: flex;
          flex-direction: column;
          gap: 22px;
          margin-top: 30px;
          margin-bottom: 30px;
        }}
        .stat-card {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 30px 40px;
          background: rgba(255, 255, 255, 0.04);
          border: 1px solid rgba(225, 29, 72, 0.3);
          border-radius: 24px;
        }}
        .stat-value {{
          font-size: 64px;
          font-weight: 800;
          background: linear-gradient(135deg, #e11d48, #f43f5e);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }}
        .stat-label {{
          font-size: 28px;
          font-weight: 600;
          color: #e2e8f0;
          text-align: right;
          max-width: 450px;
        }}
        .footer {{
          font-size: 24px;
          color: rgba(255, 255, 255, 0.5);
          font-weight: 600;
          text-align: center;
        }}
      </style>
    </head>
    <body>
      <div class="radial-glow-1"></div>
      <div class="radial-glow-2"></div>

      <div class="card-container">
        <div>
          <div class="header-tag">◆ CRUSH.LU COMMUNITY METRICS</div>
          <h1 class="title">{title}</h1>
        </div>

        <div class="stats-grid">
          {stats_html}
        </div>

        <div class="footer">
          Rejoignez la communauté sur crush.lu &bull; Rencontres & Événements à Luxembourg
        </div>
      </div>
    </body>
    </html>
    """
    png_bytes = _render_html_to_png(html)
    return _save_bytes_to_storage(png_bytes, "kpi_card")


def generate_profile_card(first_name="Sophie", age=31, region="Luxembourg-Ville", passions=None, bio_quote=None) -> str:
    """Generate an anonymized member profile card (1080x1080px)."""
    if not passions:
        passions = ["Œnologie", "Randonnée", "Gastronomie"]
    if not bio_quote:
        bio_quote = "Recherche une belle histoire basée sur la complicité et le rire."

    passions_html = "".join([f'<div class="pill">{p}</div>' for p in passions[:4]])

    html = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
      <meta charset="UTF-8">
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&family=Playfair+Display:ital@1&display=swap" rel="stylesheet">
      <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
          width: 1080px;
          height: 1080px;
          background: #0b0b14;
          font-family: 'Outfit', sans-serif;
          color: #ffffff;
          display: flex;
          align-items: center;
          justify-content: center;
          overflow: hidden;
          position: relative;
        }}
        .radial-glow-1 {{
          position: absolute;
          top: -150px;
          left: -150px;
          width: 650px;
          height: 650px;
          background: radial-gradient(circle, rgba(225, 29, 72, 0.45) 0%, rgba(0, 0, 0, 0) 70%);
          border-radius: 50%;
        }}
        .card-container {{
          position: relative;
          z-index: 10;
          width: 960px;
          height: 960px;
          background: rgba(22, 22, 38, 0.8);
          backdrop-filter: blur(30px);
          -webkit-backdrop-filter: blur(30px);
          border: 1.5px solid rgba(225, 29, 72, 0.4);
          border-radius: 36px;
          padding: 65px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          box-shadow: 0 40px 100px rgba(0, 0, 0, 0.8);
        }}
        .header-tag {{
          font-size: 22px;
          font-weight: 800;
          letter-spacing: 2px;
          color: #f43f5e;
          text-transform: uppercase;
        }}
        .profile-row {{
          display: flex;
          align-items: center;
          gap: 35px;
          margin-top: 25px;
        }}
        .avatar {{
          width: 150px;
          height: 150px;
          border-radius: 50%;
          background: linear-gradient(135deg, #9333ea, #e11d48);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 72px;
          font-weight: 800;
          color: #ffffff;
          box-shadow: 0 15px 35px rgba(225, 29, 72, 0.4);
        }}
        .name {{
          font-size: 52px;
          font-weight: 800;
          color: #ffffff;
        }}
        .region {{
          font-size: 28px;
          font-weight: 600;
          color: #38bdf8;
          margin-top: 6px;
        }}
        .section-label {{
          font-size: 22px;
          font-weight: 700;
          letter-spacing: 1px;
          color: rgba(255, 255, 255, 0.6);
          margin-bottom: 14px;
        }}
        .pills-row {{
          display: flex;
          flex-wrap: wrap;
          gap: 14px;
        }}
        .pill {{
          padding: 14px 28px;
          background: rgba(147, 51, 234, 0.2);
          border: 1px solid rgba(147, 51, 234, 0.5);
          border-radius: 50px;
          font-size: 26px;
          font-weight: 600;
          color: #ffffff;
        }}
        .quote-box {{
          font-family: 'Playfair Display', serif;
          font-style: italic;
          font-size: 36px;
          line-height: 1.4;
          color: #f1f5f9;
          padding: 30px;
          background: rgba(255, 255, 255, 0.03);
          border-left: 4px solid #e11d48;
          border-radius: 0 20px 20px 0;
        }}
        .footer {{
          font-size: 22px;
          color: rgba(255, 255, 255, 0.5);
          font-weight: 600;
          display: flex;
          align-items: center;
          gap: 10px;
        }}
      </style>
    </head>
    <body>
      <div class="radial-glow-1"></div>

      <div class="card-container">
        <div>
          <div class="header-tag">🔒 MEMBRE CERTIFIÉ CRUSH.LU</div>
          <div class="profile-row">
            <div class="avatar">{first_name[0].upper()}</div>
            <div>
              <div class="name">{first_name}, {age} ans</div>
              <div class="region">📍 {region}</div>
            </div>
          </div>
        </div>

        <div>
          <div class="section-label">CENTRES D'INTÉRÊT</div>
          <div class="pills-row">
            {passions_html}
          </div>
        </div>

        <div>
          <div class="section-label">CE QU'IL / ELLE RECHERCHE</div>
          <div class="quote-box">« {bio_quote} »</div>
        </div>

        <div class="footer">
          <span>🔒 Profil anonymisé & certifié par Crush.lu &bull; Protection de la vie privée</span>
        </div>
      </div>
    </body>
    </html>
    """
    png_bytes = _render_html_to_png(html)
    return _save_bytes_to_storage(png_bytes, "profile_card")


def _save_bytes_to_storage(png_bytes: bytes, prefix="graphic") -> str:
    """Save PNG bytes to media storage and return public absolute URL."""
    file_name = f"social/{prefix}_{os.urandom(4).hex()}.png"
    saved_path = default_storage.save(file_name, ContentFile(png_bytes))
    media_url = default_storage.url(saved_path)

    if media_url.startswith("/"):
        backend_host = getattr(settings, "BACKEND_BASE_URL", "http://localhost:8000")
        media_url = f"{backend_host.rstrip('/')}{media_url}"

    return media_url
