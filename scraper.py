"""
Google Maps → Telegram Bot
Scrape les magasins SANS site web en Algérie
Détecte automatiquement : type, adresse, contact
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

GOOGLE_API_KEY   = os.getenv("GOOGLE_API_KEY", "VOTRE_CLE_GOOGLE_API")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "VOTRE_TOKEN_TELEGRAM")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "VOTRE_CHAT_ID")

# Wilayas à scraper (modifiable)
WILAYAS = [
    "Alger, Algérie",
    "Oran, Algérie",
    "Constantine, Algérie",
    "Annaba, Algérie",
    "Blida, Algérie",
]

# Délai entre chaque envoi Telegram (secondes) — évite le rate-limit
SEND_DELAY = 1.5

# Nombre max de résultats par wilaya
MAX_RESULTS_PER_WILAYA = 60

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

# ─── TYPES DE COMMERCES ───────────────────────────────────────────────────────

# Mapping type Google Maps → emoji + label français
TYPE_MAP = {
    "restaurant":            ("🍽️", "Restaurant"),
    "food":                  ("🍽️", "Restauration"),
    "cafe":                  ("☕", "Café"),
    "bakery":                ("🥖", "Boulangerie"),
    "meal_takeaway":         ("🥡", "Restauration rapide"),
    "meal_delivery":         ("🛵", "Livraison repas"),
    "bar":                   ("🍺", "Bar"),
    "night_club":            ("🎵", "Boîte de nuit"),
    "pharmacy":              ("💊", "Pharmacie"),
    "doctor":                ("🩺", "Médecin"),
    "dentist":               ("🦷", "Dentiste"),
    "hospital":              ("🏥", "Hôpital"),
    "health":                ("🏥", "Santé"),
    "beauty_salon":          ("💅", "Salon de beauté"),
    "hair_care":             ("💇", "Coiffeur"),
    "spa":                   ("🧖", "Spa"),
    "clothing_store":        ("👗", "Boutique vêtements"),
    "shoe_store":            ("👟", "Chaussures"),
    "jewelry_store":         ("💍", "Bijouterie"),
    "store":                 ("🏪", "Magasin"),
    "supermarket":           ("🛒", "Supermarché"),
    "convenience_store":     ("🏬", "Épicerie"),
    "grocery_or_supermarket":("🛒", "Épicerie/Supermarché"),
    "department_store":      ("🏬", "Grand magasin"),
    "shopping_mall":         ("🛍️", "Centre commercial"),
    "electronics_store":     ("📱", "Électronique"),
    "hardware_store":        ("🔧", "Quincaillerie"),
    "home_goods_store":      ("🛋️", "Maison / Déco"),
    "furniture_store":       ("🪑", "Meubles"),
    "book_store":            ("📚", "Librairie"),
    "pet_store":             ("🐾", "Animalerie"),
    "florist":               ("💐", "Fleuriste"),
    "car_repair":            ("🔩", "Mécanicien auto"),
    "car_dealer":            ("🚗", "Concessionnaire"),
    "car_wash":              ("🚿", "Lavage auto"),
    "gas_station":           ("⛽", "Station-service"),
    "lodging":               ("🏨", "Hébergement"),
    "real_estate_agency":    ("🏠", "Immobilier"),
    "travel_agency":         ("✈️", "Agence de voyage"),
    "bank":                  ("🏦", "Banque"),
    "atm":                   ("💳", "DAB / ATM"),
    "insurance_agency":      ("📋", "Assurance"),
    "accounting":            ("📊", "Comptabilité"),
    "lawyer":                ("⚖️", "Avocat"),
    "school":                ("🏫", "École"),
    "university":            ("🎓", "Université"),
    "gym":                   ("💪", "Salle de sport"),
    "laundry":               ("👕", "Laverie"),
    "electrician":           ("⚡", "Électricien"),
    "plumber":               ("🔧", "Plombier"),
    "painter":               ("🎨", "Peintre"),
    "moving_company":        ("📦", "Déménagement"),
    "storage":               ("📦", "Stockage"),
    "post_office":           ("📮", "Poste"),
    "printing":              ("🖨️", "Imprimerie"),
    "point_of_interest":     ("📍", "Point d'intérêt"),
    "establishment":         ("🏢", "Établissement"),
}

def get_type_label(google_types: list) -> tuple[str, str]:
    """Retourne (emoji, label) du type principal du commerce."""
    for t in google_types:
        if t in TYPE_MAP:
            return TYPE_MAP[t]
    return ("🏪", "Commerce")


# ─── DATACLASS ────────────────────────────────────────────────────────────────

@dataclass
class Place:
    place_id:    str
    name:        str
    address:     str
    phone:       str
    rating:      Optional[float]
    reviews:     int
    type_emoji:  str
    type_label:  str
    wilaya:      str
    maps_url:    str
    raw_types:   list = field(default_factory=list)


# ─── GOOGLE MAPS API ──────────────────────────────────────────────────────────

BASE_URL = "https://maps.googleapis.com/maps/api/place"

def search_places(wilaya: str, page_token: str = None) -> dict:
    """Cherche tous les établissements dans une wilaya."""
    params = {
        "query":    f"magasin commerce boutique {wilaya}",
        "language": "fr",
        "region":   "dz",
        "key":      GOOGLE_API_KEY,
    }
    if page_token:
        params = {"pagetoken": page_token, "key": GOOGLE_API_KEY}
        time.sleep(2)  # Requis par l'API avant d'utiliser un page_token

    r = requests.get(f"{BASE_URL}/textsearch/json", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def get_place_details(place_id: str) -> dict:
    """Récupère les détails complets d'un lieu (téléphone, site web, types...)."""
    params = {
        "place_id": place_id,
        "fields":   "name,formatted_address,formatted_phone_number,website,rating,user_ratings_total,types,url",
        "language": "fr",
        "key":      GOOGLE_API_KEY,
    }
    r = requests.get(f"{BASE_URL}/details/json", params=params, timeout=10)
    r.raise_for_status()
    return r.json().get("result", {})


def scrape_wilaya(wilaya: str, max_results: int = MAX_RESULTS_PER_WILAYA) -> list[Place]:
    """Scrape tous les commerces sans site web d'une wilaya."""
    log.info(f"▶ Scraping: {wilaya}")
    places_found = []
    seen_ids = set()
    page_token = None
    page = 1

    while len(places_found) < max_results:
        log.info(f"  Page {page} — {wilaya}")
        try:
            data = search_places(wilaya, page_token)
        except Exception as e:
            log.error(f"  Erreur recherche: {e}")
            break

        results = data.get("results", [])
        if not results:
            break

        for r in results:
            if len(places_found) >= max_results:
                break

            pid = r.get("place_id")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)

            # Récupère les détails pour vérifier le site web
            try:
                details = get_place_details(pid)
            except Exception as e:
                log.warning(f"  Détails introuvables pour {pid}: {e}")
                continue

            # ── Filtre : sans site web ──────────────────────────────────────
            if details.get("website"):
                continue

            phone = details.get("formatted_phone_number", "").strip()
            address = details.get("formatted_address", r.get("formatted_address", "N/A")).strip()
            rating = details.get("rating") or r.get("rating")
            reviews = details.get("user_ratings_total") or r.get("user_ratings_total", 0)
            raw_types = details.get("types") or r.get("types", [])
            maps_url = details.get("url") or f"https://www.google.com/maps/place/?q=place_id:{pid}"

            emoji, label = get_type_label(raw_types)

            place = Place(
                place_id   = pid,
                name       = details.get("name") or r.get("name", "Inconnu"),
                address    = address,
                phone      = phone or "Non disponible",
                rating     = rating,
                reviews    = reviews,
                type_emoji = emoji,
                type_label = label,
                wilaya     = wilaya.replace(", Algérie", ""),
                maps_url   = maps_url,
                raw_types  = raw_types,
            )
            places_found.append(place)
            log.info(f"  ✓ {place.name} [{label}] — {phone or 'pas de tél'}")

        page_token = data.get("next_page_token")
        if not page_token:
            break
        page += 1

    log.info(f"  → {len(places_found)} commerces sans site trouvés à {wilaya}")
    return places_found


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def format_message(place: Place) -> str:
    """Formate un message Telegram pour un commerce."""
    stars = ""
    if place.rating:
        full = int(place.rating)
        stars = "⭐" * full + f" {place.rating}/5 ({place.reviews} avis)"
    else:
        stars = "Pas de note"

    msg = (
        f"{place.type_emoji} *{escape_md(place.name)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📂 *Type :* {escape_md(place.type_label)}\n"
        f"📍 *Adresse :* {escape_md(place.address)}\n"
        f"📞 *Contact :* `{escape_md(place.phone)}`\n"
        f"⭐ *Note :* {escape_md(stars)}\n"
        f"🌐 *Site web :* ❌ Aucun\n"
        f"🗺️ [Voir sur Google Maps]({place.maps_url})\n"
        f"📅 _{escape_md(datetime.now().strftime('%d/%m/%Y %H:%M'))}_"
    )
    return msg


def escape_md(text: str) -> str:
    """Échappe les caractères spéciaux MarkdownV2."""
    if not text:
        return ""
    chars = r"_*[]()~`>#+-=|{}.!\\"
    return re.sub(f"([{re.escape(chars)}])", r"\\\1", str(text))


async def send_to_telegram(bot: Bot, message: str):
    """Envoie un message Telegram avec retry automatique."""
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id    = TELEGRAM_CHAT_ID,
                text       = message,
                parse_mode = ParseMode.MARKDOWN_V2,
                disable_web_page_preview = False,
            )
            return
        except TelegramError as e:
            if "Too Many Requests" in str(e):
                wait = 30 + attempt * 15
                log.warning(f"  Rate limit Telegram — attente {wait}s")
                await asyncio.sleep(wait)
            else:
                log.error(f"  Erreur Telegram: {e}")
                break


async def send_summary(bot: Bot, stats: dict):
    """Envoie un résumé global à la fin."""
    lines = [f"📊 *Rapport de scraping — Algérie*\n━━━━━━━━━━━━━━━━━━━━"]
    total = 0
    for wilaya, count in stats["by_wilaya"].items():
        lines.append(f"  🏙️ {escape_md(wilaya)}: *{count}* commerces")
        total += count
    lines.append(f"\n✅ *Total :* {total} commerces sans site web")
    lines.append(f"📅 _{escape_md(datetime.now().strftime('%d/%m/%Y %H:%M'))}_")

    await bot.send_message(
        chat_id    = TELEGRAM_CHAT_ID,
        text       = "\n".join(lines),
        parse_mode = ParseMode.MARKDOWN_V2,
    )


# ─── SAUVEGARDE JSON ──────────────────────────────────────────────────────────

def save_results(all_places: list[Place]):
    """Sauvegarde tous les résultats en JSON."""
    filename = f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    data = [
        {
            "name":       p.name,
            "type":       p.type_label,
            "type_emoji": p.type_emoji,
            "address":    p.address,
            "phone":      p.phone,
            "rating":     p.rating,
            "reviews":    p.reviews,
            "wilaya":     p.wilaya,
            "maps_url":   p.maps_url,
        }
        for p in all_places
    ]
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"💾 Résultats sauvegardés: {filename}")
    return filename


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    log.info("=" * 60)
    log.info("  GOOGLE MAPS → TELEGRAM BOT  |  Algérie Sans-Site")
    log.info("=" * 60)

    bot = Bot(token=TELEGRAM_TOKEN)

    # Message de démarrage
    await bot.send_message(
        chat_id    = TELEGRAM_CHAT_ID,
        text       = (
            "🤖 *Bot démarré\\!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔍 Scraping Google Maps en cours\\.\\.\\.\n"
            "📋 Filtrage: magasins *sans site web* uniquement\n"
            f"🏙️ Wilayas: {escape_md(', '.join(w.replace(', Algérie','') for w in WILAYAS))}"
        ),
        parse_mode = ParseMode.MARKDOWN_V2,
    )

    all_places = []
    stats = {"by_wilaya": {}}

    for wilaya in WILAYAS:
        wilaya_name = wilaya.replace(", Algérie", "")
        places = scrape_wilaya(wilaya)
        stats["by_wilaya"][wilaya_name] = len(places)
        all_places.extend(places)

        # En-tête wilaya sur Telegram
        if places:
            await bot.send_message(
                chat_id    = TELEGRAM_CHAT_ID,
                text       = (
                    f"🏙️ *{escape_md(wilaya_name)}* — "
                    f"{len(places)} commerces sans site trouvés"
                ),
                parse_mode = ParseMode.MARKDOWN_V2,
            )

        # Envoie chaque commerce
        for place in places:
            msg = format_message(place)
            await send_to_telegram(bot, msg)
            await asyncio.sleep(SEND_DELAY)

    # Sauvegarde JSON
    if all_places:
        save_results(all_places)

    # Résumé final
    await send_summary(bot, stats)
    log.info("✅ Bot terminé.")


if __name__ == "__main__":
    asyncio.run(main())