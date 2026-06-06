"""
Google Maps → Telegram Bot
Scrape les magasins SANS site web en Algérie
Via SerpApi (pas de carte bancaire requise)
"""

import asyncio
import logging
import time
import json
import re
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

import requests
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

SERPAPI_KEY      = os.getenv("SERPAPI_KEY", "COLLE_TA_CLE_ICI")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "COLLE_TON_TOKEN_ICI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "COLLE_TON_CHAT_ID_ICI")

WILAYAS = [
    "Alger",
    "Oran",
    "Constantine",
    "Annaba",
    "Blida",
]

SEND_DELAY = 1.5

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─── TYPES ────────────────────────────────────────────────────────────────────

KEYWORDS_TYPE = [
    (["restaurant", "pizza", "burger", "grill", "kebab", "brasserie", "traiteur"], ("🍽️", "Restaurant")),
    (["café", "cafe", "coffee", "thé", "salon de thé"],                            ("☕", "Café")),
    (["boulangerie", "pâtisserie", "gateau", "patisserie"],                         ("🥖", "Boulangerie")),
    (["pharmacie", "parapharmacie"],                                                ("💊", "Pharmacie")),
    (["médecin", "docteur", "clinique", "cabinet médical"],                         ("🩺", "Médecin")),
    (["dentiste", "dentaire"],                                                      ("🦷", "Dentiste")),
    (["coiffeur", "coiffure", "barbier", "barber"],                                 ("💇", "Coiffeur")),
    (["beauty", "esthétique", "esthetique", "spa", "ongle"],                        ("💅", "Beauté")),
    (["vêtement", "vetement", "mode", "boutique", "friperie", "textile"],           ("👗", "Boutique vêtements")),
    (["chaussure", "shoe"],                                                         ("👟", "Chaussures")),
    (["bijou", "bijouterie", "jewelry", "or ", "argent"],                           ("💍", "Bijouterie")),
    (["épicerie", "epicerie", "alimentation", "grocery"],                           ("🛒", "Épicerie")),
    (["supermarché", "supermarche", "marché", "marche"],                            ("🏬", "Supermarché")),
    (["électronique", "electronique", "informatique", "téléphone", "telephone", "gsm", "mobile", "iphone", "samsung"], ("📱", "Électronique")),
    (["électroménager", "electromenager", "climatiseur", "refrigerateur"],          ("🏠", "Électroménager")),
    (["meuble", "décoration", "decoration", "home"],                                ("🪑", "Meubles / Déco")),
    (["librairie", "livre", "papeterie", "fourniture"],                             ("📚", "Librairie")),
    (["mécanicien", "mecanicien", "auto", "garage", "carrosserie", "pneu"],        ("🔩", "Mécanicien auto")),
    (["station", "essence", "carburant"],                                           ("⛽", "Station-service")),
    (["quincaillerie", "matériaux", "materiaux", "bricolage", "peinture"],          ("🔧", "Quincaillerie")),
    (["immobilier", "agence immobilière", "location"],                              ("🏠", "Immobilier")),
    (["banque", "bank", "assurance", "credit"],                                     ("🏦", "Banque / Finance")),
    (["école", "ecole", "formation", "cours", "académie", "academie"],              ("🏫", "École / Formation")),
    (["sport", "gym", "fitness", "salle de sport"],                                 ("💪", "Sport")),
    (["hôtel", "hotel", "résidence", "residence", "auberge"],                       ("🏨", "Hôtel")),
    (["voyage", "tourisme", "agence de voyage"],                                    ("✈️", "Agence de voyage")),
    (["imprimerie", "impression", "copie", "reprographie"],                         ("🖨️", "Imprimerie")),
    (["fleur", "fleuriste", "plante"],                                              ("💐", "Fleuriste")),
    (["laverie", "pressing", "nettoyage"],                                          ("👕", "Laverie")),
]

def detect_type(name: str, category: str = "") -> tuple:
    text = (name + " " + category).lower()
    for keywords, result in KEYWORDS_TYPE:
        if any(kw in text for kw in keywords):
            return result
    return ("🏪", "Commerce")


# ─── DATACLASS ────────────────────────────────────────────────────────────────

@dataclass
class Place:
    name:       str
    address:    str
    phone:      str
    rating:     Optional[float]
    reviews:    int
    type_emoji: str
    type_label: str
    wilaya:     str
    maps_url:   str


# ─── SERPAPI ──────────────────────────────────────────────────────────────────

def search_places(wilaya: str, start: int = 0) -> list:
    """Cherche des commerces via SerpApi Google Maps."""
    params = {
        "engine":   "google_maps",
        "q":        f"magasins commerces boutiques {wilaya} Algérie",
        "hl":       "fr",
        "gl":       "dz",
        "start":    start,
        "api_key":  SERPAPI_KEY,
    }
    r = requests.get("https://serpapi.com/search", params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("local_results", [])


def scrape_wilaya(wilaya: str) -> list[Place]:
    log.info(f"▶ Scraping: {wilaya}")
    places_found = []
    seen = set()

    for start in range(0, 3):  # 3 pages SerpApi
        try:
            results = search_places(wilaya, start)
        except Exception as e:
            log.error(f"  Erreur SerpApi: {e}")
            break

        if not results:
            break

        for r in results:
            name = r.get("title", "").strip()
            if not name or name in seen:
                continue
            seen.add(name)

            # Filtre : sans site web
            if r.get("website"):
                continue

            phone   = r.get("phone", "Non disponible").strip()
            address = r.get("address", "N/A").strip()
            rating  = r.get("rating")
            reviews = r.get("reviews", 0)
            maps_url = r.get("link") or r.get("place_id") or "https://maps.google.com"
            category = r.get("type", "")

            emoji, label = detect_type(name, category)

            place = Place(
                name       = name,
                address    = address,
                phone      = phone,
                rating     = rating,
                reviews    = reviews,
                type_emoji = emoji,
                type_label = label,
                wilaya     = wilaya,
                maps_url   = maps_url,
            )
            places_found.append(place)
            log.info(f"  ✓ {name} [{label}] — {phone}")

        time.sleep(1)

    log.info(f"  → {len(places_found)} commerces sans site trouvés à {wilaya}")
    return places_found


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def escape_md(text: str) -> str:
    if not text:
        return ""
    chars = r"_*[]()~`>#+-=|{}.!\\"
    return re.sub(f"([{re.escape(chars)}])", r"\\\1", str(text))


def format_message(place: Place) -> str:
    if place.rating:
        stars = "⭐" * int(place.rating)
        note  = f"{stars} {place.rating}/5 ({place.reviews} avis)"
    else:
        note = "Pas de note"

    return (
        f"{place.type_emoji} *{escape_md(place.name)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📂 *Type :* {escape_md(place.type_label)}\n"
        f"📍 *Adresse :* {escape_md(place.address)}\n"
        f"📞 *Contact :* `{escape_md(place.phone)}`\n"
        f"⭐ *Note :* {escape_md(note)}\n"
        f"🌐 *Site web :* ❌ Aucun\n"
        f"🗺️ [Voir sur Google Maps]({place.maps_url})\n"
        f"📅 _{escape_md(datetime.now().strftime('%d/%m/%Y %H:%M'))}_"
    )


async def send_msg(bot: Bot, text: str):
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id    = TELEGRAM_CHAT_ID,
                text       = text,
                parse_mode = ParseMode.MARKDOWN_V2,
                disable_web_page_preview = True,
            )
            return
        except TelegramError as e:
            if "Too Many Requests" in str(e):
                await asyncio.sleep(30 + attempt * 15)
            else:
                log.error(f"Telegram error: {e}")
                return


def save_json(all_places: list[Place]):
    filename = f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump([
            {
                "nom":     p.name,
                "type":    p.type_label,
                "adresse": p.address,
                "contact": p.phone,
                "note":    p.rating,
                "avis":    p.reviews,
                "wilaya":  p.wilaya,
                "maps":    p.maps_url,
            }
            for p in all_places
        ], f, ensure_ascii=False, indent=2)
    log.info(f"💾 Sauvegardé: {filename}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    log.info("=" * 55)
    log.info("  GOOGLE MAPS → TELEGRAM  |  Algérie Sans-Site")
    log.info("=" * 55)

    bot = Bot(token=TELEGRAM_TOKEN)

    await send_msg(bot,
        "🤖 *Bot démarré\\!*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 Scraping Google Maps via SerpApi\\.\\.\\.\n"
        "📋 Filtre: magasins *sans site web* uniquement\n"
        f"🏙️ Wilayas: {escape_md(', '.join(WILAYAS))}"
    )

    all_places = []
    stats = {}

    for wilaya in WILAYAS:
        places = scrape_wilaya(wilaya)
        stats[wilaya] = len(places)
        all_places.extend(places)

        if places:
            await send_msg(bot,
                f"🏙️ *{escape_md(wilaya)}* — "
                f"{len(places)} commerces sans site trouvés"
            )

        for place in places:
            await send_msg(bot, format_message(place))
            await asyncio.sleep(SEND_DELAY)

    save_json(all_places)

    # Résumé
    lines = ["📊 *Rapport final — Algérie*\n━━━━━━━━━━━━━━━━━━━━"]
    for w, c in stats.items():
        lines.append(f"  🏙️ {escape_md(w)}: *{c}* commerces")
    lines.append(f"\n✅ *Total :* {len(all_places)} commerces sans site")
    lines.append(f"📅 _{escape_md(datetime.now().strftime('%d/%m/%Y %H:%M'))}_")
    await send_msg(bot, "\n".join(lines))

    log.info("✅ Terminé.")


if __name__ == "__main__":
    asyncio.run(main())