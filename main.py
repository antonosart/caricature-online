# ══════════════════════════════════════════════════════════════
#   CARICATURE.ONLINE — Backend v2.7.0 TEMPLATE IMG2IMG PIPELINE
#   Antonos Template → img2img face replace (no face-swap needed)
#   Stack: Flask · Firestore · GCS · fal.ai LoRA · Stripe · Resend
# ══════════════════════════════════════════════════════════════

import os, uuid, threading, time, requests, re, json, base64
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import stripe
from google.cloud import storage as gcs_lib, firestore
import resend
import anthropic

app = Flask(__name__)
CORS(app, origins=[
    "https://caricature.online",
    "https://www.caricature.online",
    "http://localhost:3000"
])

# ══════════════════════════════════════════════════════════════
#   CONFIG
# ══════════════════════════════════════════════════════════════
CFG = {
    "STRIPE_SECRET":       os.environ.get("STRIPE_SECRET_KEY",       "").strip(),
    "STRIPE_PUB":          os.environ.get("STRIPE_PUBLISHABLE_KEY",   "").strip(),
    "STRIPE_WEBHOOK_SEC":  os.environ.get("STRIPE_WEBHOOK_SECRET",    "").strip(),
    "FAL_KEY":             os.environ.get("FAL_API_KEY",              "").strip(),
    "FAL_LORA_URL":        os.environ.get("FAL_LORA_URL",             "").strip(),
    "ANTHROPIC_KEY":       os.environ.get("ANTHROPIC_API_KEY",        "").strip(),
    "RESEND_KEY":          os.environ.get("RESEND_API_KEY",           "").strip(),
    "RESEND_FROM":         os.environ.get("RESEND_FROM_EMAIL",        "art@caricature.online").strip(),
    "ADMIN_EMAIL":         os.environ.get("ADMIN_EMAIL",              "antonosart@gmail.com").strip(),
    "ADMIN_SECRET":        os.environ.get("ADMIN_SECRET",             "change-me").strip(),
    "GCS_BUCKET":          os.environ.get("GCS_BUCKET",               "caricature-files").strip(),
    "BASE_URL":            os.environ.get("BASE_URL",                 "https://caricature.online").strip(),
    "AI_OUTPUT_MODE":      os.environ.get("AI_OUTPUT_MODE",           "4k").strip().lower(),
    "FAL_UPSCALE_MODEL":   os.environ.get("FAL_UPSCALE_MODEL",        "fal-ai/esrgan").strip(),
    # v2.4.2 caricature style-gate engine: transforms the customer photo directly, with stronger caricature styling.
    # Defaults use fal.ai FLUX LoRA image-to-image; override safely from Cloud Run env vars if needed.
    "FAL_IDENTITY_I2I_MODEL": os.environ.get("FAL_IDENTITY_I2I_MODEL", "fal-ai/flux-lora/image-to-image").strip(),
    "FAL_SECONDARY_I2I_MODEL": os.environ.get("FAL_SECONDARY_I2I_MODEL", "fal-ai/flux-general/image-to-image").strip(),
    "IDENTITY_FIRST_ENABLED": os.environ.get("IDENTITY_FIRST_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"},
    "IDENTITY_STRENGTH_1": float(os.environ.get("IDENTITY_STRENGTH_1", "0.50")),
    "IDENTITY_STRENGTH_2": float(os.environ.get("IDENTITY_STRENGTH_2", "0.62")),
    "IDENTITY_STRENGTH_3": float(os.environ.get("IDENTITY_STRENGTH_3", "0.72")),
    "LORA_SCALE_1": float(os.environ.get("LORA_SCALE_1", "1.30")),
    "LORA_SCALE_2": float(os.environ.get("LORA_SCALE_2", "1.45")),
    "LORA_SCALE_3": float(os.environ.get("LORA_SCALE_3", "1.65")),
    "MAX_GENERATION_RETRIES": int(os.environ.get("MAX_GENERATION_RETRIES", "3")),
    "MIN_STYLE_SCORE": int(os.environ.get("MIN_STYLE_SCORE", "6")),
    "MIN_IDENTITY_SCORE": int(os.environ.get("MIN_IDENTITY_SCORE", "6")),
    "MIN_QUALITY_SCORE": int(os.environ.get("MIN_QUALITY_SCORE", "6")),
    # v2.6.0: Template-anchored pipeline
    # STYLE_UNIFY_STRENGTH: very low img2img strength after face-swap.
    # 0.15-0.25 = barely changes composition/structure, but LoRA blends face into caricature style.
    "STYLE_UNIFY_STRENGTH": float(os.environ.get("STYLE_UNIFY_STRENGTH", "0.22")),
    "STYLE_UNIFY_LORA_SCALE": float(os.environ.get("STYLE_UNIFY_LORA_SCALE", "0.90")),
    "STYLE_UNIFY_STEPS": int(os.environ.get("STYLE_UNIFY_STEPS", "28")),
    "STYLE_UNIFY_GUIDANCE": float(os.environ.get("STYLE_UNIFY_GUIDANCE", "8.0")),
    "TEMPLATE_IMAGES_FOLDER": os.environ.get("TEMPLATE_IMAGES_FOLDER", "template_bases"),
}

stripe.api_key     = CFG["STRIPE_SECRET"]
gcs_client         = gcs_lib.Client()
db                 = firestore.Client()
claude_client      = anthropic.Anthropic(api_key=CFG["ANTHROPIC_KEY"])
resend.api_key     = CFG["RESEND_KEY"]
os.environ["FAL_KEY"] = CFG["FAL_KEY"]  # fal_client reads env var, not api_key attr

# ══════════════════════════════════════════════════════════════
#   TEMPLATES CATALOGUE
# ══════════════════════════════════════════════════════════════
TEMPLATES = {
    # ── WEDDING ───────────────────────────────────────────────
    "wed_anniversary":   {"name":"40th Wedding Anniversary",  "occasion":"wedding", "persons":2, "emoji":"💒", "desc":"Couple celebrating anniversary"},
    "wed_boxing":        {"name":"Boxing Wedding",            "occasion":"wedding", "persons":2, "emoji":"🥊", "desc":"Fun boxing couple caricature"},
    "wed_citroen":       {"name":"Bride in a Citroën 2CV",   "occasion":"wedding", "persons":2, "emoji":"🚗", "desc":"Classic car wedding"},
    "wed_pulling":       {"name":"Bride Pulling Groom",       "occasion":"wedding", "persons":2, "emoji":"👰", "desc":"Funny bride & groom"},
    "wed_bicycles":      {"name":"Couple On Bicycles",        "occasion":"wedding", "persons":2, "emoji":"🚲", "desc":"Cycling couple"},
    "wed_happily":       {"name":"Happily Ever After",        "occasion":"wedding", "persons":2, "emoji":"❤️", "desc":"MR & MRS romantic"},
    "wed_heart":         {"name":"Heart Wedding Dresses",     "occasion":"wedding", "persons":2, "emoji":"💜", "desc":"Heart background wedding"},
    "wed_three":         {"name":"Just The Three Of Us",      "occasion":"wedding", "persons":3, "emoji":"👨‍👩‍👦", "desc":"Wedding with baby"},
    "wed_lace":          {"name":"Lace Wedding Invitation",   "occasion":"wedding", "persons":2, "emoji":"🌸", "desc":"Elegant lace style"},
    "wed_love":          {"name":"Love Forever",              "occasion":"wedding", "persons":2, "emoji":"💕", "desc":"Heart love forever"},
    "wed_married":       {"name":"Married Couple",            "occasion":"wedding", "persons":2, "emoji":"👫", "desc":"Classic married couple"},
    "wed_navy":          {"name":"Navy Wedding",              "occasion":"wedding", "persons":2, "emoji":"⚓", "desc":"Nautical navy theme"},
    "wed_runaway":       {"name":"Runaway Groom",             "occasion":"wedding", "persons":2, "emoji":"🏃", "desc":"Funny runaway groom"},
    "wed_santorini":     {"name":"Santorini Wedding",         "occasion":"wedding", "persons":2, "emoji":"🏛️", "desc":"Greek island wedding"},
    "wed_singing":       {"name":"Singing In The Street",     "occasion":"wedding", "persons":2, "emoji":"🎵", "desc":"Musical couple"},
    "wed_christening":   {"name":"Wedding & Christening",     "occasion":"wedding", "persons":3, "emoji":"👶", "desc":"Wedding with newborn"},
    "wed_boat":          {"name":"Wedding On A Boat",         "occasion":"wedding", "persons":2, "emoji":"⛵", "desc":"Nautical boat wedding"},
    "wed_cooking":       {"name":"Cooking with Love",         "occasion":"wedding", "persons":2, "emoji":"👨‍🍳", "desc":"Couple cooking together"},

    # ── BUSINESS / CORPORATE ──────────────────────────────────
    "biz_analytics":     {"name":"Business Analytics",        "occasion":"business", "persons":1, "emoji":"📊", "desc":"Business analyst caricature"},
    "biz_doctor_music":  {"name":"Doctor's Music",            "occasion":"business", "persons":1, "emoji":"🎵", "desc":"Musical doctor"},
    "biz_niptuck":       {"name":"Dr. Nip/Tuck",             "occasion":"business", "persons":1, "emoji":"🏥", "desc":"Plastic surgeon"},
    "biz_guitar":        {"name":"Electric Guitar Player",    "occasion":"business", "persons":1, "emoji":"🎸", "desc":"Rock musician"},
    "biz_farmer":        {"name":"Farmer",                    "occasion":"business", "persons":1, "emoji":"🌾", "desc":"Farmer caricature"},
    "biz_secretary":     {"name":"General Secretary",         "occasion":"business", "persons":1, "emoji":"💼", "desc":"Office secretary"},
    "biz_gynecologist":  {"name":"Gynecologist",              "occasion":"business", "persons":1, "emoji":"👩‍⚕️", "desc":"Medical professional"},
    "biz_xray":          {"name":"Homer Brain X-Ray",         "occasion":"business", "persons":1, "emoji":"🧠", "desc":"Funny doctor with X-ray"},
    "biz_lab":           {"name":"Medical Laboratory",        "occasion":"business", "persons":1, "emoji":"🔬", "desc":"Lab scientist"},
    "biz_osteopath":     {"name":"Osteopathic Physician",     "occasion":"business", "persons":2, "emoji":"🤝", "desc":"Doctor with patient"},
    "biz_smartphone":    {"name":"Smartphone Lover",          "occasion":"business", "persons":1, "emoji":"📱", "desc":"Tech lover"},
    "biz_successful":    {"name":"Successful Businesswoman",  "occasion":"business", "persons":1, "emoji":"👩‍💼", "desc":"Business woman"},
    "biz_teacher":       {"name":"Teacher",                   "occasion":"business", "persons":1, "emoji":"📚", "desc":"Teacher at blackboard"},
    "biz_tourist":       {"name":"Tourist Guide",             "occasion":"business", "persons":1, "emoji":"🗺️", "desc":"Tourism professional"},
    "biz_unproductive":  {"name":"Unproductive Day",          "occasion":"business", "persons":1, "emoji":"😴", "desc":"Funny lazy day"},
    "biz_worldwide":     {"name":"Worldwide Businessman",     "occasion":"business", "persons":1, "emoji":"🌍", "desc":"Global business"},
    "biz_confectionery": {"name":"Young Confectionery",       "occasion":"business", "persons":1, "emoji":"🍰", "desc":"Pastry chef"},
    "biz_funny_cook":    {"name":"Funny Cook Man",            "occasion":"business", "persons":1, "emoji":"👨‍🍳", "desc":"Funny chef"},
    "biz_grand_chef":    {"name":"Grand Chef",                "occasion":"business", "persons":1, "emoji":"⭐", "desc":"Master chef"},
    "biz_photographer":  {"name":"Photographer on Action",    "occasion":"business", "persons":1, "emoji":"📷", "desc":"Action photographer"},
    "biz_dancer":        {"name":"So You Think You Can Dance","occasion":"business", "persons":1, "emoji":"💃", "desc":"Dancer caricature"},

    # ── BIRTHDAY / CELEBRATION ────────────────────────────────
    "bday_anniversary":  {"name":"Birthday Anniversary",      "occasion":"birthday", "persons":1, "emoji":"🎂", "desc":"Birthday celebration"},
    "bday_celebrity":    {"name":"Celebrity Girl",            "occasion":"birthday", "persons":1, "emoji":"⭐", "desc":"Celebrity birthday"},
    "bday_grandpa":      {"name":"Happy Birthday Grandpa",    "occasion":"birthday", "persons":1, "emoji":"🎉", "desc":"Grandpa birthday"},
    "bday_kitty":        {"name":"Kitty Kitty",               "occasion":"birthday", "persons":1, "emoji":"🐱", "desc":"Cat theme birthday"},
    "bday_lovely":       {"name":"Lovely Girl Invitation",    "occasion":"birthday", "persons":1, "emoji":"👧", "desc":"Girl birthday invitation"},
    "bday_reunion":      {"name":"Reunion Party",             "occasion":"birthday", "persons":1, "emoji":"🎊", "desc":"Reunion celebration"},
    "bday_santa":        {"name":"Santa Claus",               "occasion":"birthday", "persons":1, "emoji":"🎅", "desc":"Christmas Santa"},
    "bday_superhero":    {"name":"Superheroes Carnival",      "occasion":"birthday", "persons":2, "emoji":"🦸", "desc":"Superhero party"},
    "bday_dancer2":      {"name":"So You Think You Can Dance","occasion":"birthday", "persons":2, "emoji":"💃", "desc":"Dance celebration"},

    # ── KIDS ──────────────────────────────────────────────────
    "kids_baby_girl":    {"name":"A Baby Girl is on Her Way", "occasion":"kids", "persons":2, "emoji":"👶", "desc":"Baby announcement"},
    "kids_ahoy":         {"name":"Ahoy Matey Party",          "occasion":"kids", "persons":1, "emoji":"⚓", "desc":"Pirate kids party"},
    "kids_cherokee":     {"name":"Cherokee Indian Kid",       "occasion":"kids", "persons":1, "emoji":"🪶", "desc":"Native American theme"},
    "kids_three":        {"name":"Just The Three Of Us",      "occasion":"kids", "persons":3, "emoji":"👨‍👩‍👦", "desc":"Family with baby"},
    "kids_dogs":         {"name":"Kid & Dogs Birthday",       "occasion":"kids", "persons":1, "emoji":"🐕", "desc":"Kid with dogs"},
    "kids_santa":        {"name":"Santa Clause Baby",         "occasion":"kids", "persons":1, "emoji":"🎅", "desc":"Baby Santa"},
    "kids_summer":       {"name":"Summer Christening",        "occasion":"kids", "persons":1, "emoji":"🌊", "desc":"Summer baptism"},
    "kids_underwater":   {"name":"Underwater Girl",           "occasion":"kids", "persons":1, "emoji":"🐠", "desc":"Underwater theme"},
    "kids_welcome":      {"name":"Welcome Baby",              "occasion":"kids", "persons":1, "emoji":"🍼", "desc":"New baby welcome"},

    # ── SUPERHERO ─────────────────────────────────────────────
    "hero_batmobile":    {"name":"Batmobile",                 "occasion":"superhero", "persons":1, "emoji":"🦇", "desc":"Batman caricature"},
    "hero_catwoman":     {"name":"Catwoman Unmasked",         "occasion":"superhero", "persons":1, "emoji":"🐱", "desc":"Catwoman caricature"},
    "hero_mata_hari":    {"name":"Mata Hari",                 "occasion":"superhero", "persons":1, "emoji":"🗡️", "desc":"Spy warrior"},
    "hero_secret":       {"name":"Secret Agent",              "occasion":"superhero", "persons":1, "emoji":"🕵️", "desc":"Secret agent"},
    "hero_spiderman":    {"name":"Spiderman",                 "occasion":"superhero", "persons":1, "emoji":"🕷️", "desc":"Spiderman caricature"},
    "hero_super_mama":   {"name":"Super Mama",                "occasion":"superhero", "persons":1, "emoji":"🦸‍♀️", "desc":"Superhero mom"},
    "hero_carnival":     {"name":"Superheroes Carnival",      "occasion":"superhero", "persons":2, "emoji":"🎭", "desc":"Carnival superhero"},
    "hero_superyou":     {"name":"SuperYou",                  "occasion":"superhero", "persons":1, "emoji":"💪", "desc":"Generic superhero"},
    "hero_spartan":      {"name":"The Spartan",               "occasion":"superhero", "persons":1, "emoji":"⚔️", "desc":"Spartan warrior"},

    # ── FAMILY / FRIENDS ──────────────────────────────────────
    "fam_art_nouveau":   {"name":"Art Nouveau",               "occasion":"family", "persons":1, "emoji":"🎨", "desc":"Art nouveau style"},
    "fam_aunt":          {"name":"Aunt Jemima",               "occasion":"family", "persons":1, "emoji":"👩", "desc":"Family aunt portrait"},
    "fam_bday_anni":     {"name":"Birthday Anniversary",      "occasion":"family", "persons":1, "emoji":"🎂", "desc":"Family birthday"},
    "fam_doctor_claw":   {"name":"Doctor Claw",               "occasion":"family", "persons":1, "emoji":"🦾", "desc":"Funny doctor"},
    "fam_scooter":       {"name":"Girl on a Scooter",         "occasion":"family", "persons":1, "emoji":"🛵", "desc":"Scooter girl"},
    "fam_grandpa":       {"name":"Happy Birthday Grandpa",    "occasion":"family", "persons":1, "emoji":"👴", "desc":"Grandpa portrait"},
    "fam_kitty":         {"name":"Kitty Kitty",               "occasion":"family", "persons":1, "emoji":"🐱", "desc":"Cat lover"},
    "fam_lovely":        {"name":"Lovely Girl Invitation",    "occasion":"family", "persons":1, "emoji":"👧", "desc":"Girl portrait"},
    "fam_lying":         {"name":"Lying In Bed",              "occasion":"family", "persons":1, "emoji":"😴", "desc":"Funny lying in bed"},
    "fam_santa":         {"name":"Santa Claus",               "occasion":"family", "persons":1, "emoji":"🎅", "desc":"Santa portrait"},
    "fam_smartphone":    {"name":"Smartphone Lover",          "occasion":"family", "persons":1, "emoji":"📱", "desc":"Phone addict"},
    "fam_cooking":       {"name":"Cooking with Love",         "occasion":"family", "persons":2, "emoji":"👨‍🍳", "desc":"Couple cooking"},

    # ── SPORTS / HOBBIES ──────────────────────────────────────
    "sport_bike":        {"name":"Bike Me",                   "occasion":"sports", "persons":1, "emoji":"🚴", "desc":"Cycling caricature"},
    "sport_celebrity":   {"name":"Celebrity Girl",            "occasion":"sports", "persons":1, "emoji":"⭐", "desc":"Celebrity portrait"},
    "sport_doctor":      {"name":"Doctor's Music",            "occasion":"sports", "persons":1, "emoji":"🎵", "desc":"Music lover"},
    "sport_farmer":      {"name":"Farmer",                    "occasion":"sports", "persons":1, "emoji":"🌾", "desc":"Farming hobby"},
    "sport_scooter":     {"name":"Girl on a Scooter",         "occasion":"sports", "persons":1, "emoji":"🛵", "desc":"Scooter hobby"},
    "sport_nba":         {"name":"NBA Player",                "occasion":"sports", "persons":1, "emoji":"🏀", "desc":"Basketball player"},
    "sport_travel":      {"name":"Travel Around The World",   "occasion":"sports", "persons":1, "emoji":"✈️", "desc":"World traveler"},
    "sport_underwater":  {"name":"Underwater Girl",           "occasion":"sports", "persons":1, "emoji":"🤿", "desc":"Diving hobby"},
    "sport_yamaha":      {"name":"Yamaha Motorcycle",         "occasion":"sports", "persons":1, "emoji":"🏍️", "desc":"Motorcycle rider"},
    "sport_cooking":     {"name":"Cooking with Love",         "occasion":"sports", "persons":2, "emoji":"👨‍🍳", "desc":"Cooking hobby"},
    "sport_funny_cook":  {"name":"Funny Cook Man",            "occasion":"sports", "persons":1, "emoji":"🍳", "desc":"Amateur chef"},
    "sport_grand_chef":  {"name":"Grand Chef",                "occasion":"sports", "persons":1, "emoji":"⭐", "desc":"Master chef"},
    "sport_photo":       {"name":"Photographer on Action",    "occasion":"sports", "persons":1, "emoji":"📷", "desc":"Photography hobby"},
    "sport_dancer":      {"name":"So You Think You Can Dance","occasion":"sports", "persons":2, "emoji":"💃", "desc":"Dancing hobby"},
}

# Custom free-description order support. Used when the customer describes a
# scene instead of selecting a predefined template. Keeps frontend/backend
# payloads compatible without breaking the existing template catalogue.
TEMPLATES["custom_free"] = {
    "name": "Custom Scene",
    "occasion": "other",
    "persons": 1,
    "emoji": "✨",
    "desc": "customer-described custom caricature scene"
}

# ── PRICING ───────────────────────────────────────────────────
PERSON_PRICE = {1: 0, "1": 0, 2: 500, "2": 500, 3: 1000, "3": 1000, 4: 1500, "4": 1500, 5: 2000, "5": 2000}
PLANS = {
    "basic":    {"name": "Basic",    "base_cents":  899, "extra_cents": 0, "label": "Basic"},
    "standard": {"name": "Standard", "base_cents": 1499, "extra_cents": 0, "label": "Standard"},
    "premium":  {"name": "Premium",  "base_cents": 1999, "extra_cents": 0, "label": "Premium"},
}

OCCASIONS = {
    "wedding":   {"name": "Wedding",    "emoji": "💒", "desc": "For the big day"},
    "birthday":  {"name": "Birthday",   "emoji": "🎂", "desc": "Celebrate in style"},
    "business":  {"name": "Business",   "emoji": "💼", "desc": "Professional & fun"},
    "kids":      {"name": "Kids",       "emoji": "👶", "desc": "Little ones"},
    "superhero": {"name": "Superhero",  "emoji": "🦸", "desc": "Become a hero"},
    "family":    {"name": "Family",     "emoji": "👨‍👩‍👧", "desc": "Friends & family"},
    "sports":    {"name": "Sports",     "emoji": "⚽", "desc": "Hobbies & sports"},
}

def calc_price(persons_key: str, plan_id: str) -> int:
    plan = PLANS.get(plan_id, PLANS["standard"])
    person_extra = PERSON_PRICE.get(persons_key, 0)
    return plan["base_cents"] + plan["extra_cents"] + person_extra


# ══════════════════════════════════════════════════════════════
#   HELPERS
# ══════════════════════════════════════════════════════════════

def ok(data={}, code=200):
    return jsonify({"success": True, **data}), code

def err(msg, code=400):
    return jsonify({"success": False, "error": msg}), code

def require_admin(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Admin-Secret") != CFG["ADMIN_SECRET"]:
            return err("Unauthorized", 401)
        return f(*args, **kwargs)
    return wrapper

def upload_to_gcs(file_bytes: bytes, filename: str, content_type: str, folder="uploads") -> str:
    bucket = gcs_client.bucket(CFG["GCS_BUCKET"])
    blob_name = f"{folder}/{filename}"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(file_bytes, content_type=content_type)
    return f"https://storage.googleapis.com/{CFG['GCS_BUCKET']}/{blob_name}"

def log_order(order_id: str, data: dict):
    db.collection("orders").document(order_id).set(data, merge=True)

def get_order(order_id: str) -> dict:
    doc = db.collection("orders").document(order_id).get()
    return doc.to_dict() if doc.exists else None

def update_order_status(order_id: str, status: str, extra: dict = {}):
    db.collection("orders").document(order_id).update({
        "status": status,
        "updated_at": datetime.utcnow().isoformat(),
        **extra
    })

def notify_admin(msg: str):
    try:
        resend.Emails.send({
            "from": CFG["RESEND_FROM"],
            "to": [CFG["ADMIN_EMAIL"]],
            "subject": f"[Caricature Admin] {msg[:60]}",
            "text": msg
        })
    except:
        pass


# ══════════════════════════════════════════════════════════════
#   IMAGE NORMALISATION — Claude-safe vision payloads
# ══════════════════════════════════════════════════════════════

def prepare_image_for_vision_bytes(image_bytes: bytes, media_type: str = "image/jpeg", max_bytes: int = 4_500_000) -> tuple[bytes, str, dict]:
    """Compress/resize images before sending to Claude Vision.

    Anthropic image inputs have a 5MB base64/source limit. Customer phone photos
    are often 8–12MB, so every vision call must use a Claude-safe JPEG copy.
    The original upload remains untouched in GCS for generation/reference.
    """
    meta = {
        "original_bytes": len(image_bytes or b""),
        "processed_bytes": len(image_bytes or b""),
        "processed": False,
        "method": "original",
        "max_side": None,
    }
    if not image_bytes:
        return image_bytes, media_type or "image/jpeg", meta

    # Already small enough: keep as-is, unless it is an uncommon type.
    if len(image_bytes) <= max_bytes and (media_type or "").lower() in {"image/jpeg", "image/png", "image/webp"}:
        return image_bytes, media_type or "image/jpeg", meta

    try:
        from PIL import Image
        from io import BytesIO

        im = Image.open(BytesIO(image_bytes))
        im = im.convert("RGB")

        # Claude does not need full camera resolution for face analysis.
        # 1568px preserves facial features while keeping payload compact.
        max_side = 1568
        w, h = im.size
        if max(w, h) > max_side:
            ratio = max_side / float(max(w, h))
            im = im.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.Resampling.LANCZOS)

        last = None
        for quality in (88, 84, 80, 76, 72, 68, 64):
            out = BytesIO()
            im.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
            data = out.getvalue()
            last = data
            if len(data) <= max_bytes:
                meta.update({
                    "processed_bytes": len(data),
                    "processed": True,
                    "method": f"pillow_jpeg_q{quality}",
                    "max_side": max_side,
                })
                return data, "image/jpeg", meta

        # Last-resort: smaller side.
        if last and len(last) > max_bytes:
            w, h = im.size
            ratio = 1024 / float(max(w, h))
            if ratio < 1:
                im = im.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.Resampling.LANCZOS)
            out = BytesIO()
            im.save(out, format="JPEG", quality=72, optimize=True, progressive=True)
            data = out.getvalue()
            meta.update({
                "processed_bytes": len(data),
                "processed": True,
                "method": "pillow_jpeg_1024_q72",
                "max_side": 1024,
            })
            return data, "image/jpeg", meta

        return last or image_bytes, "image/jpeg", meta
    except Exception as e:
        print(f"[VisionImage] Compression failed: {e}")
        return image_bytes, media_type or "image/jpeg", meta



# ══════════════════════════════════════════════════════════════
#   CONTENT SAFETY — text/order moderation
# ══════════════════════════════════════════════════════════════

FORBIDDEN_KEYWORDS = {
    "sexual": ["nude", "naked", "porn", "sex", "erotic", "fetish", "onlyfans", "stripper"],
    "minors": ["child nude", "kid nude", "baby nude", "teen nude", "sexual child", "sexual kid"],
    "hate": ["nazi", "kkk", "white power", "ethnic cleansing"],
    "violence": ["gore", "bloodbath", "decapitated", "torture", "mutilated"],
    "fraud": ["fake passport", "fake id", "bank fraud", "scam", "phishing"],
}

FORBIDDEN_PATTERNS = [
    re.compile(r"\b(?:nude|naked|sexual|erotic)\b.*\b(?:child|kid|baby|minor|teen)\b", re.I),
    re.compile(r"\b(?:child|kid|baby|minor|teen)\b.*\b(?:nude|naked|sexual|erotic)\b", re.I),
]

def moderate_text(text: str) -> dict:
    """Fast local moderation for customer free-text. Designed to block obvious abuse before Stripe."""
    t = (text or "").lower()
    hits = []
    for cat, words in FORBIDDEN_KEYWORDS.items():
        for w in words:
            if w in t:
                hits.append({"category": cat, "term": w})
    for pat in FORBIDDEN_PATTERNS:
        if pat.search(text or ""):
            hits.append({"category": "minors", "term": "minor-sexual-pattern"})
    return {"allowed": len(hits) == 0, "hits": hits}

def moderate_order_request(body: dict) -> dict:
    joined = " ".join([
        str(body.get("description", "")),
        str(body.get("notes", "")),
        json.dumps(body.get("answers", {}), ensure_ascii=False),
        str(body.get("template_id", "")),
    ])
    result = moderate_text(joined)
    if not result["allowed"]:
        return {"allowed": False, "reason": "content_policy", "hits": result["hits"]}
    return {"allowed": True, "reason": "ok", "hits": []}


def build_person_photos_from_payload(body: dict, upload_ids: list) -> list:
    """Accepts both current order.html flat upload_ids and new structured person_photos."""
    structured = body.get("person_photos")
    if isinstance(structured, list) and structured:
        out = []
        for idx, person in enumerate(structured, start=1):
            ids = person.get("upload_ids", []) if isinstance(person, dict) else []
            ids = [str(x) for x in ids if x]
            if ids:
                out.append({
                    "person_index": person.get("person_index", idx) if isinstance(person, dict) else idx,
                    "upload_ids": ids[:3],
                    "primary_upload_id": person.get("primary_upload_id", ids[0]) if isinstance(person, dict) else ids[0],
                })
        if out:
            return out
    # fallback: group current flat upload list as 2 photos per person
    persons = int(str(body.get("persons", "1") or "1")) if str(body.get("persons", "1")).isdigit() else 1
    out = []
    cursor = 0
    for i in range(1, persons + 1):
        ids = upload_ids[cursor:cursor + 2]
        cursor += 2
        if ids:
            out.append({"person_index": i, "upload_ids": ids, "primary_upload_id": ids[0]})
    if not out and upload_ids:
        out.append({"person_index": 1, "upload_ids": upload_ids[:2], "primary_upload_id": upload_ids[0]})
    return out


def resolve_uploads(upload_ids: list, person_photos: list) -> tuple[list, list]:
    """Resolve Firestore upload docs while preserving person grouping."""
    docs_by_id = {}
    flat_urls = []
    flat_unique = []
    for uid in upload_ids:
        if uid and uid not in flat_unique:
            flat_unique.append(uid)
    for person in person_photos:
        for uid in person.get("upload_ids", []):
            if uid and uid not in flat_unique:
                flat_unique.append(uid)
    for uid in flat_unique[:15]:
        doc = db.collection("uploads").document(uid).get()
        if doc.exists:
            d = doc.to_dict()
            docs_by_id[uid] = d
            if d.get("photo_url"):
                flat_urls.append(d["photo_url"])
    resolved_people = []
    for person in person_photos:
        urls = []
        qualities = []
        for uid in person.get("upload_ids", []):
            d = docs_by_id.get(uid)
            if d and d.get("photo_url"):
                urls.append(d["photo_url"])
                qualities.append(d.get("quality", {}))
        if urls:
            resolved_people.append({
                "person_index": person.get("person_index", len(resolved_people) + 1),
                "primary_upload_id": person.get("primary_upload_id") or person.get("upload_ids", [None])[0],
                "photo_urls": urls,
                "qualities": qualities,
            })
    return flat_urls, resolved_people

# ══════════════════════════════════════════════════════════════
#   AI PIPELINE — LoRA Generation
# ══════════════════════════════════════════════════════════════

def analyze_photo_with_claude(photo_urls: list, persons: str, template_name: str, answers: dict, person_photos: list | None = None) -> str:
    """Use Claude Vision to create an identity-aware generation prompt from all available references."""
    try:
        content = []
        refs = []
        for i, url in enumerate((photo_urls or [])[:6], start=1):
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            media_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
            img_data, media_type, vision_meta = prepare_image_for_vision_bytes(resp.content, media_type)
            print(f"[Claude] Vision ref {i}: {vision_meta}")
            img_b64 = base64.b64encode(img_data).decode()
            content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}})
            refs.append(f"Reference photo {i}")

        occasion_desc = answers.get("occasion", "")
        notes = answers.get("notes", "") or answers.get("description", "")
        people_count = str(persons or "1")

        content.append({"type": "text", "text": f"""
You are preparing a production prompt for an AI caricature order.

GOAL:
Create a recognisable, family-friendly, high-quality caricature in the Antonos/caricature.photo visual direction.
Preserve identity from the uploaded reference photos: face shape, hairstyle, hairline, eyes, nose, mouth, skin tone, glasses, beard, distinctive features, and approximate age.

ORDER:
Template/theme: {template_name}
People count: {people_count}
Occasion: {occasion_desc}
Customer notes: {notes}

RULES:
- Do not invent a different person.
- Keep the person recognisable even with caricature exaggeration.
- Use tasteful exaggeration: larger head, expressive face, clean bold outlines, warm editorial colour.
- Avoid photorealism. Avoid grotesque distortion. Avoid unsafe content.
- For multiple people, describe each person separately as Person 1, Person 2, etc.

Return only one line starting with PROMPT: and make it detailed but concise.
"""})
        msg = claude_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=700,
            messages=[{"role": "user", "content": content}]
        )
        text = msg.content[0].text.strip()
        if "PROMPT:" in text:
            text = text.split("PROMPT:")[-1].strip()
        return (
            "ANTONOS caricature style, identity-preserving caricature, "
            + text
            + ", clean bold ink outlines, expressive but recognisable face, vivid warm colours, premium gift illustration, ultra detailed, 4K composition"
        )
    except Exception as e:
        print(f"[Claude] Error: {e}")
        template = TEMPLATES.get(answers.get("template_id", ""), {})
        return (
            f"ANTONOS caricature style, identity-preserving portrait caricature, "
            f"{template.get('desc','portrait')}, {template_name} theme, exaggerated but recognisable features, "
            f"bold lines, colorful, professional premium caricature art, 4K composition"
        )



def extract_face_description(photo_urls: list) -> str:
    """Claude Vision extracts a compact, precise face description for template img2img.

    v2.7.0: Face-swap cannot work on drawn/cartoon faces (Antonos templates).
    Instead we pass a detailed face description to img2img so FLUX can draw
    the customer's face directly in the caricature, replacing the template face.
    """
    try:
        content = []
        for url in (photo_urls or [])[:2]:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            media_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
            img_data, media_type, _ = prepare_image_for_vision_bytes(resp.content, media_type)
            content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(img_data).decode()}})

        content.append({"type": "text", "text": (
            "Describe ONLY this person's physical appearance for an AI art face-replacement prompt.\n"
            "Be specific and concise. One sentence, comma-separated, all lowercase.\n"
            "Include: gender, approximate age, hair color+length+style, eye color+shape, "
            "skin tone, glasses (yes/no and description), beard/facial hair (yes/no), distinctive features.\n"
            "Explicitly state absences: 'no glasses', 'no beard', 'smooth skin' etc.\n"
            "Example: young woman mid-20s, shoulder-length brown hair, blue-green almond eyes, "
            "fair skin with rosy cheeks, no glasses, no beard, soft round face, full lips\n"
            "Return ONLY the description. No preamble, no quotes."
        )})
        msg = claude_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=120,
            messages=[{"role": "user", "content": content}]
        )
        desc = msg.content[0].text.strip().strip('"').strip("'")
        print(f"[FaceDesc] Extracted: {desc}")
        return desc
    except Exception as e:
        print(f"[FaceDesc] Error: {e}")
        return "young person, no glasses, no beard, no facial hair"


def create_face_mask_for_template(template_url: str, template_id: str) -> str | None:
    """v2.8.0: Generate a soft face mask for a template using Claude Vision.

    Claude Vision locates the character's face/head as % of image dimensions.
    PIL draws a soft-edged ellipse over that area (white=repaint, black=keep).
    The mask is stored in GCS for reuse: template_masks/{template_id}_mask.png

    Why inpainting with mask is the ONLY correct approach:
    - img2img at low strength: preserves template but face doesn't change
    - img2img at high strength: face changes but template is destroyed
    - Inpainting: ONLY the masked face area is repainted, everything else untouched
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter
        from io import BytesIO

        # Check GCS cache first
        bucket = gcs_client.bucket(CFG["GCS_BUCKET"])
        mask_blob_name = f"template_masks/{template_id}_mask.png"
        mask_blob = bucket.blob(mask_blob_name)
        if mask_blob.exists():
            mask_url = f"https://storage.googleapis.com/{CFG['GCS_BUCKET']}/{mask_blob_name}"
            print(f"[Mask] Using cached mask: {mask_url}")
            return mask_url

        # Download template
        resp = requests.get(template_url, timeout=20)
        resp.raise_for_status()
        im = Image.open(BytesIO(resp.content)).convert("RGB")
        w, h = im.size

        # Claude Vision: locate the face bounding box
        img_data, media_type, _ = prepare_image_for_vision_bytes(resp.content)
        img_b64 = base64.b64encode(img_data).decode()

        msg = claude_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=120,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}},
                {"type": "text", "text": (
                    "In this caricature illustration, identify the bounding box of the character's face and head area.\n"
                    "Include forehead, hair, ears, chin. Add 15% margin on all sides.\n"
                    "Return ONLY JSON, no text: {\"x_min\": 0-100, \"y_min\": 0-100, \"x_max\": 0-100, \"y_max\": 0-100}\n"
                    "Values are percentages of image width (x) and height (y)."
                )}
            ]}]
        )
        raw = msg.content[0].text.strip()
        if "{" in raw:
            raw = raw[raw.index("{"):raw.rindex("}")+1]
        bbox = json.loads(raw)

        x1 = max(0, int(bbox["x_min"] / 100 * w))
        y1 = max(0, int(bbox["y_min"] / 100 * h))
        x2 = min(w, int(bbox["x_max"] / 100 * w))
        y2 = min(h, int(bbox["y_max"] / 100 * h))
        print(f"[Mask] Face bbox: ({x1},{y1})-({x2},{y2}) on {w}x{h} image")

        # Draw soft elliptical mask
        mask = Image.new("RGB", (w, h), "black")
        draw = ImageDraw.Draw(mask)
        draw.ellipse([x1, y1, x2, y2], fill="white")
        mask = mask.filter(ImageFilter.GaussianBlur(radius=max(10, (x2-x1)//8)))

        # Upload to GCS
        out = BytesIO()
        mask.save(out, format="PNG")
        mask_blob.upload_from_string(out.getvalue(), content_type="image/png")
        mask_url = f"https://storage.googleapis.com/{CFG['GCS_BUCKET']}/{mask_blob_name}"
        print(f"[Mask] Created and saved mask: {mask_url}")
        return mask_url

    except Exception as e:
        print(f"[Mask] Error creating face mask: {e}")
        return None


def generate_from_template_inpainting(template_url: str, mask_url: str, face_description: str, template_name: str, attempt: int = 1) -> tuple[str | None, dict]:
    """v2.8.0 core: inpaint ONLY the face area in the Antonos template.

    The mask (white=repaint, black=keep) covers only the face/head area.
    FLUX inpainting with LoRA repaints just the face in Antonos caricature style
    matching the customer's face_description. Everything outside the mask
    (body, costume, background, hands) stays pixel-perfect from the template.

    This solves the fundamental problem with img2img:
    - Low strength: face doesn't change  
    - High strength: everything changes
    Inpainting: ONLY the masked area changes, everything else is untouched.
    """
    meta = {"attempted": False, "success": False, "error": None,
            "method": "template_inpainting", "template_url": template_url, "mask_url": mask_url}

    if not CFG.get("FAL_LORA_URL"):
        meta["error"] = "no_lora_url"
        return None, meta

    guidance = 9.0 + (attempt - 1) * 0.5
    steps = 40 if attempt == 1 else 44 if attempt == 2 else 48
    lora_scale = float(CFG.get(f"LORA_SCALE_{min(attempt,3)}", 0.90))

    # Prompt: describe the face to draw in the masked area
    prompt = (
        f"ANTONOS hand-drawn caricature illustration style, "
        f"caricature portrait face of: {face_description}, "
        f"drawn in Antonos editorial caricature art, bold confident ink outlines around face, "
        f"cel-shaded cartoon skin, exaggerated expressive eyes, "
        f"warm vivid colours, caricature proportions, NOT photorealistic, "
        f"premium gift illustration face, same {template_name} costume visible around face"
    )

    # Try FLUX LoRA inpainting (mask_url parameter)
    models_to_try = [
        ("fal-ai/flux-lora/image-to-image", {
            "prompt": prompt,
            "image_url": template_url,
            "mask_url": mask_url,
            "strength": 0.99,
            "image_size": "portrait_4_3",
            "num_images": 1,
            "num_inference_steps": steps,
            "guidance_scale": guidance,
            "enable_safety_checker": True,
            "output_format": "jpeg",
            "loras": [{"path": CFG["FAL_LORA_URL"], "scale": lora_scale}],
        }),
        # Fallback: standard FLUX dev inpainting (no LoRA)
        ("fal-ai/flux/dev/image-to-image", {
            "prompt": prompt,
            "image_url": template_url,
            "mask_url": mask_url,
            "strength": 0.99,
            "image_size": "portrait_4_3",
            "num_images": 1,
            "num_inference_steps": steps,
            "guidance_scale": guidance,
            "enable_safety_checker": True,
            "output_format": "jpeg",
        }),
    ]

    for model, args in models_to_try:
        try:
            meta.update({"attempted": True, "model": model, "guidance": guidance, "lora_scale": lora_scale})
            print(f"[AI v2.8] Inpainting via {model} attempt={attempt}")
            result = _fal_run(model, args)
            url = _extract_fal_image_url(result)
            if url:
                meta.update({"success": True, "result_url": url})
                print(f"[AI v2.8] Inpainting success: {url}")
                return url, meta
            meta["error"] = f"no_url:{str(result)[:200]}"
        except Exception as e:
            meta["error"] = str(e)[:600]
            print(f"[AI v2.8] Inpainting failed via {model}: {e}")

    return None, meta


def generate_from_template_img2img(template_url: str, face_description: str, template_name: str, attempt: int = 1) -> tuple[str | None, dict]:
    """v2.7.0 core: replace the face in an Antonos template via img2img.

    WHY this works better than face-swap:
    - fal-ai/face-swap only detects photorealistic faces. Drawn cartoon faces
      (Antonos templates) are not detected as valid swap targets.
    - img2img at medium strength (0.38-0.54) keeps most of the template intact
      (body, costume, background, ink style) while prompt guides FLUX to draw
      a new face matching the customer description.
    - Antonos LoRA ensures the newly drawn face is in caricature style.

    Strength calibration:
      attempt 1: 0.38 -> conservative, good for face-only replacement
      attempt 2: 0.46 -> stronger if identity_score < 6 on attempt 1
      attempt 3: 0.54 -> aggressive, customer face takes priority
    """
    meta = {"attempted": False, "success": False, "error": None,
            "method": "template_img2img", "template_url": template_url}

    if not CFG.get("FAL_LORA_URL"):
        meta["error"] = "no_lora_url_configured"
        return None, meta

    strength = min(0.58, 0.38 + (attempt - 1) * 0.08)
    guidance = 9.0 + (attempt - 1) * 0.5
    steps = 40 if attempt == 1 else 44 if attempt == 2 else 48
    lora_scale = float(CFG.get(f"LORA_SCALE_{min(attempt,3)}", 0.85))

    prompt = (
        f"ANTONOS hand-drawn caricature illustration style, "
        f"keep the same {template_name} costume body pose background and art style as the reference illustration, "
        f"replace ONLY the face with a caricature drawing of: {face_description}, "
        f"draw the new face in Antonos caricature style: expressive eyes, "
        f"bold confident ink outlines around the face, cel-shaded cartoon skin, "
        f"exaggerated slightly larger head, warm vivid editorial colours, "
        f"do NOT change the body the costume the background or the overall composition, "
        f"premium gift caricature illustration, NOT photorealistic face"
    )

    try:
        meta.update({"attempted": True, "strength": strength, "lora_scale": lora_scale, "guidance": guidance})
        print(f"[AI v2.7] Template img2img: strength={strength} guidance={guidance} lora={lora_scale} attempt={attempt}")
        result = _fal_run("fal-ai/flux-lora/image-to-image", {
            "prompt": prompt,
            "image_url": template_url,
            "strength": strength,
            "image_size": "portrait_4_3",
            "num_images": 1,
            "num_inference_steps": steps,
            "guidance_scale": guidance,
            "enable_safety_checker": True,
            "output_format": "jpeg",
            "loras": [{"path": CFG["FAL_LORA_URL"], "scale": lora_scale}],
        })
        url = _extract_fal_image_url(result)
        if url:
            meta.update({"success": True, "result_url": url})
            print(f"[AI v2.7] Template img2img success: {url}")
            return url, meta
        meta["error"] = f"no_url:{str(result)[:300]}"
    except Exception as e:
        meta["error"] = str(e)[:800]
        print(f"[AI v2.7] Template img2img failed: {e}")

    return None, meta


def _fal_run(model: str, arguments: dict):
    import fal_client
    os.environ["FAL_KEY"] = CFG["FAL_KEY"]
    fal_client.api_key = CFG["FAL_KEY"]
    return fal_client.run(model, arguments=arguments)


def generate_base_art(prompt: str, photo_urls: list, attempt: int = 1) -> str | None:
    """Generate the caricature scene. LoRA is used for Antonos style; identity is reinforced later."""
    guidance = 7.5 + (attempt - 1) * 0.5
    steps = 32 if attempt == 1 else 36

    if CFG["FAL_LORA_URL"]:
        try:
            print(f"[AI] LoRA base generation attempt={attempt}")
            result = _fal_run("fal-ai/flux-lora", {
                "prompt": prompt,
                "loras": [{"path": CFG["FAL_LORA_URL"], "scale": 1.0}],
                "image_size": "portrait_4_3",
                "num_images": 1,
                "num_inference_steps": steps,
                "guidance_scale": guidance,
            })
            if result and result.get("images"):
                return result["images"][0]["url"]
        except Exception as e:
            print(f"[AI] LoRA base failed: {e}")

    try:
        print(f"[AI] FLUX fallback attempt={attempt}")
        result = _fal_run("fal-ai/flux/schnell", {
            "prompt": prompt,
            "image_size": "portrait_4_3",
            "num_images": 1,
            "num_inference_steps": 8,
        })
        if result and result.get("images"):
            return result["images"][0]["url"]
    except Exception as e:
        print(f"[AI] FLUX fallback failed: {e}")
    return None


def _extract_fal_image_url(result: dict) -> str | None:
    """Extract image URL from common fal response shapes."""
    if not isinstance(result, dict):
        return None
    if result.get("image") and isinstance(result["image"], dict):
        return result["image"].get("url")
    if result.get("images") and isinstance(result["images"], list) and result["images"]:
        first = result["images"][0]
        if isinstance(first, dict):
            return first.get("url")
    if result.get("url"):
        return result.get("url")
    return None



def _identity_strength_for_attempt(attempt: int) -> float:
    """Return img2img strength for attempt.

    Lower strength preserves face/pose more; higher strength allows stronger caricature/theme.
    v2.4.2 uses a calibrated range: enough denoise for illustration, not enough to replace identity.
    """
    if attempt <= 1:
        return max(0.25, min(0.95, float(CFG.get("IDENTITY_STRENGTH_1", 0.46))))
    if attempt == 2:
        return max(0.25, min(0.95, float(CFG.get("IDENTITY_STRENGTH_2", 0.52))))
    return max(0.25, min(0.95, float(CFG.get("IDENTITY_STRENGTH_3", 0.60))))


def build_identity_first_prompt(prompt: str, attempt: int = 1) -> str:
    """Strengthen prompt for reference-image transformation instead of random T2I.

    The prompt must tell the image-to-image model to transform the uploaded person,
    not invent a new actor matching the text description.
    """
    identity_rules = (
        "Transform the person in the reference photo into the requested caricature scene. "
        "Keep the SAME person: same facial identity, same approximate age, same face shape, same eyes, same nose, "
        "same mouth/smile, same hairline and natural hair colour, same skin tone and distinctive features. "
        "Use caricature exaggeration but do not replace the person with a generic adult, celebrity, model, or different character. "
        "Preserve recognisable likeness first; style second; theme third. "
    )
    exaggeration = (
        "ANTONOS hand-drawn caricature illustration, NOT photorealistic, NOT a retouched selfie, NOT a beauty filter. "
        "Convert the reference into a clear premium cartoon/caricature drawing with visible confident ink outlines around face, hair, eyes, nose, mouth and body. "
        "Use simplified painterly shading, warm editorial colours, clean cel-shaded skin, poster-like finish, and stylised gift-art rendering. "
        "Slightly larger head, expressive eyes, tasteful caricature proportions, clean cartoon linework, full theme costume/background visible. "
        "Remove camera/photo texture, remove selfie lighting realism, remove photographic pores, remove smartphone-photo look. "
        "Avoid generic beauty portrait, avoid replacing the face. "
    )
    if attempt == 1:
        exaggeration += "Balanced identity lock with visible drawing style; keep likeness but make it clearly illustrated. "
    elif attempt == 2:
        exaggeration += "Strong caricature style: clear ink outlines, stylised skin, non-photorealistic cartoon finish while preserving identity. "
        identity_rules += "Increase facial likeness and reduce invented beauty/age changes. "
    else:
        exaggeration += "Maximum Antonos caricature styling: obvious hand-drawn caricature, larger head, illustrated costume/background, no photo-real selfie look. "
        identity_rules += "Very careful identity preservation; do not alter age, eye colour, nose, mouth or face shape. "
    return f"{identity_rules}{exaggeration}{prompt}"


def _i2i_arguments(prompt: str, reference_url: str, attempt: int = 1, model: str | None = None) -> dict:
    """Build fal image-to-image arguments compatible with FLUX LoRA img2img endpoints."""
    strength = _identity_strength_for_attempt(attempt)
    steps = 34 if attempt == 1 else 38 if attempt == 2 else 42
    guidance = 4.2 if attempt == 1 else 5.0 if attempt == 2 else 5.8
    args = {
        "prompt": build_identity_first_prompt(prompt, attempt),
        "image_url": reference_url,
        "strength": strength,
        "image_size": "portrait_4_3",
        "num_images": 1,
        "num_inference_steps": steps,
        "guidance_scale": guidance,
        "enable_safety_checker": True,
        "output_format": "jpeg",
    }
    if CFG.get("FAL_LORA_URL"):
        scale_key = f"LORA_SCALE_{min(max(int(attempt), 1), 3)}"
        args["loras"] = [{"path": CFG["FAL_LORA_URL"], "scale": float(CFG.get(scale_key, 1.25))}]
    return args


def generate_identity_first_art(prompt: str, photo_urls: list, attempt: int = 1) -> tuple[str | None, dict]:
    """Generate by transforming the customer reference photo directly.

    This is the v2.4.1 core upgrade. It uses FLUX LoRA image-to-image so the
    original face/pose/age acts as the starting canvas. If the endpoint is not
    available or returns no image, it fails safely and lets the pipeline try the
    legacy base+face-reference fallback.
    """
    meta = {
        "engine": "identity_first_img2img",
        "attempted": False,
        "success": False,
        "model": None,
        "reference_url": photo_urls[0] if photo_urls else None,
        "strength": _identity_strength_for_attempt(attempt),
        "error": None,
    }
    if not CFG.get("IDENTITY_FIRST_ENABLED", True):
        meta["error"] = "identity_first_disabled"
        return None, meta
    if not photo_urls:
        meta["error"] = "missing_reference_photo"
        return None, meta

    reference_url = photo_urls[0]
    # Primary endpoint is official FLUX LoRA image-to-image. Secondary is optional fallback.
    models = []
    for m in [CFG.get("FAL_IDENTITY_I2I_MODEL"), CFG.get("FAL_SECONDARY_I2I_MODEL")]:
        if m and m not in models:
            models.append(m)

    for model in models:
        try:
            args = _i2i_arguments(prompt, reference_url, attempt=attempt, model=model)
            print(f"[AI v2.4] Identity-first img2img via {model} attempt={attempt} strength={args.get('strength')}")
            meta.update({"attempted": True, "model": model, "arguments_preview": {k: args[k] for k in args if k not in {'prompt'}}})
            result = _fal_run(model, args)
            url = _extract_fal_image_url(result)
            if url:
                meta.update({"success": True, "result_url": url, "raw_result_keys": list(result.keys()) if isinstance(result, dict) else []})
                return url, meta
            meta["error"] = f"no_image_url_in_response:{str(result)[:500]}"
            print(f"[AI v2.4] Identity-first no image URL via {model}: {str(result)[:500]}")
        except Exception as e:
            meta["error"] = str(e)[:1000]
            print(f"[AI v2.4] Identity-first failed via {model}: {e}")

    return None, meta


def generate_candidate_art(prompt: str, photo_urls: list, attempt: int = 1, strict: bool = True, template_id: str = "", face_description: str = "", template_name: str = "") -> tuple[str | None, dict]:
    """v2.8.0 Template Inpainting pipeline.

    PRIMARY PATH (Antonos base image exists):
      generate_from_template_img2img():
        - Template image as img2img input (medium strength 0.38-0.54)
        - Prompt contains precise customer face description → FLUX draws new face
        - LoRA keeps Antonos caricature style on the new face
        - Low strength preserves costume/background/body from template
      WHY NOT face-swap: fal-ai/face-swap cannot detect drawn cartoon faces.

    FALLBACK PATH (no template base image):
      T2I with LoRA → face-swap (best effort)
    """
    meta = {"pipeline": "v2.8.0_inpainting", "attempt": attempt, "stages": [],
            "template_id": template_id, "face_description": face_description}

    # ══ PRIMARY: Template img2img ═══════════════════════════════════════════
    template_base_url = get_template_base_image(template_id) if template_id else None
    meta["template_base_url"] = template_base_url

    if template_base_url and face_description:
        t_name = template_name or template_id

        # ── Stage 1: Create/retrieve face mask ──────────────────────────
        mask_url = create_face_mask_for_template(template_base_url, template_id)
        meta["mask_url"] = mask_url

        if mask_url:
            # ── Stage 2: Inpaint ONLY the face area ─────────────────────
            # This is the only approach that can change the face without
            # destroying the template body/costume/background.
            candidate_url, inp_meta = generate_from_template_inpainting(
                template_base_url, mask_url, face_description, t_name, attempt=attempt
            )
            meta["stages"].append({"stage": "template_inpainting", **inp_meta})

            if candidate_url:
                print(f"[AI v2.8] PRIMARY inpainting success attempt={attempt}")
                return candidate_url, meta

            print(f"[AI v2.8] Inpainting failed, trying img2img fallback")
            meta["warning"] = "inpainting_failed"

        # ── Fallback: img2img on template (medium strength) ─────────────
        candidate_url, t_meta = generate_from_template_img2img(
            template_base_url, face_description, t_name, attempt=attempt
        )
        meta["stages"].append({"stage": "template_img2img_fallback", **t_meta})

        if candidate_url:
            print(f"[AI v2.8] img2img fallback success attempt={attempt}")
            return candidate_url, meta

        meta["warning"] = "template_primary_and_fallback_failed"

    # ══ FALLBACK: T2I + Face-Swap ════════════════════════════════════════════
    print(f"[AI v2.7] FALLBACK: T2I+FaceSwap for template_id={template_id}")
    scale_key = f"LORA_SCALE_{min(max(int(attempt), 1), 3)}"
    lora_scale = float(CFG.get(scale_key, 1.45))
    guidance = 7.5 + (attempt - 1) * 0.5
    steps = 40 if attempt == 1 else 45 if attempt == 2 else 50

    base_url = None
    if CFG["FAL_LORA_URL"]:
        try:
            result = _fal_run("fal-ai/flux-lora", {
                "prompt": prompt,
                "loras": [{"path": CFG["FAL_LORA_URL"], "scale": lora_scale}],
                "image_size": "portrait_4_3", "num_images": 1,
                "num_inference_steps": steps, "guidance_scale": guidance,
                "enable_safety_checker": True, "output_format": "jpeg",
            })
            if result and result.get("images"):
                img = result["images"][0]
                base_url = img.get("url") if isinstance(img, dict) else img
        except Exception as e:
            print(f"[AI v2.7] T2I LoRA failed: {e}")

    if not base_url:
        try:
            result = _fal_run("fal-ai/flux/schnell", {
                "prompt": prompt, "image_size": "portrait_4_3",
                "num_images": 1, "num_inference_steps": 8,
            })
            if result and result.get("images"):
                img = result["images"][0]
                base_url = img.get("url") if isinstance(img, dict) else img
        except Exception as e:
            print(f"[AI v2.7] schnell failed: {e}")

    meta["stages"].append({"stage": "t2i_fallback", "success": bool(base_url), "url": base_url})
    if not base_url:
        meta["error"] = "all_stages_failed"
        return None, meta

    candidate_url, swap_meta = apply_identity_reference(base_url, photo_urls, strict=strict)
    meta["stages"].append({"stage": "face_swap_on_t2i", **swap_meta})

    if candidate_url:
        return candidate_url, meta
    if not strict and base_url:
        meta["warning"] = "faceswap_failed_returning_t2i_base"
        return base_url, meta

    meta["error"] = "all_stages_failed"
    return None, meta
    """v2.6.0 Template-Anchored Pipeline.

    PRIMARY PATH (when Antonos base image exists for this template):
      Stage 1 — Face-Swap: put customer face onto real Antonos template illustration
      Stage 2 — Style Unify: low-strength img2img (0.20) blends photographic face into caricature style
      → Result: correct template layout + correct Antonos style + customer identity

    FALLBACK PATH (when no template base image found):
      Stage 1 — T2I with LoRA: generate Antonos-style scene from prompt
      Stage 2 — Face-Swap: composite customer face into scene
      → Result: Antonos style (via LoRA) + customer identity

    Why template-first is superior:
    - The real Antonos image IS the correct output style — we never need to recreate it
    - T2I cannot reliably reproduce the exact Antonos caricature proportions/composition
    - Face-swap alone creates realistic face on drawn body (style mismatch)
    - Style unification (very low strength img2img) solves the face/body style mismatch
    """
    meta = {"pipeline": "v2.8.0_inpainting", "attempt": attempt, "stages": [], "template_id": template_id}

    # ══ PRIMARY: Template-Anchored (real Antonos image as base) ═══════════════
    template_base_url = get_template_base_image(template_id) if template_id else None
    meta["template_base_url"] = template_base_url

    if template_base_url:
        print(f"[AI v2.6] PRIMARY path: template base found for {template_id}")

        # Stage 1: Face-swap customer face onto Antonos template
        swapped_url, swap_meta = apply_identity_reference(template_base_url, photo_urls, strict=False)
        meta["stages"].append({"stage": "face_swap_onto_template", **swap_meta})

        if swapped_url:
            # Stage 2: Style unification — blend face into caricature style
            unified_url, unify_meta = stylize_face_blend(swapped_url, prompt)
            meta["stages"].append({"stage": "style_unification", **unify_meta})

            if unified_url:
                print(f"[AI v2.6] Template+FaceSwap+Unify success")
                return unified_url, meta

            # Style unify failed — return face-swapped result (still acceptable)
            print(f"[AI v2.6] Style unify failed, using face-swapped template directly")
            meta["warning"] = "style_unification_failed_using_faceswap_only"
            return swapped_url, meta

        # Face-swap failed on template — fall through to fallback
        print(f"[AI v2.6] Face-swap on template failed, falling back to T2I+FaceSwap")
        meta["warning"] = "template_faceswap_failed_using_t2i_fallback"

    # ══ FALLBACK: T2I + Face-Swap ═════════════════════════════════════════════
    print(f"[AI v2.6] FALLBACK path: T2I + face-swap (no template base for {template_id})")

    scale_key = f"LORA_SCALE_{min(max(int(attempt), 1), 3)}"
    lora_scale = float(CFG.get(scale_key, 1.45))
    guidance = 7.5 + (attempt - 1) * 0.5
    steps = 40 if attempt == 1 else 45 if attempt == 2 else 50

    base_url = None
    if CFG["FAL_LORA_URL"]:
        try:
            result = _fal_run("fal-ai/flux-lora", {
                "prompt": prompt,
                "loras": [{"path": CFG["FAL_LORA_URL"], "scale": lora_scale}],
                "image_size": "portrait_4_3",
                "num_images": 1,
                "num_inference_steps": steps,
                "guidance_scale": guidance,
                "enable_safety_checker": True,
                "output_format": "jpeg",
            })
            if result and result.get("images"):
                img = result["images"][0]
                base_url = img.get("url") if isinstance(img, dict) else img
        except Exception as e:
            print(f"[AI v2.6] T2I LoRA failed: {e}")

    if not base_url:
        try:
            result = _fal_run("fal-ai/flux/schnell", {
                "prompt": prompt, "image_size": "portrait_4_3",
                "num_images": 1, "num_inference_steps": 8,
            })
            if result and result.get("images"):
                img = result["images"][0]
                base_url = img.get("url") if isinstance(img, dict) else img
        except Exception as e:
            print(f"[AI v2.6] T2I schnell failed: {e}")

    meta["stages"].append({"stage": "t2i_fallback", "success": bool(base_url), "url": base_url})
    if not base_url:
        meta["error"] = "all_generation_stages_failed"
        return None, meta

    candidate_url, swap_meta2 = apply_identity_reference(base_url, photo_urls, strict=strict)
    meta["stages"].append({"stage": "face_swap_on_t2i", **swap_meta2})

    if candidate_url:
        # Apply style unification here too
        unified_url2, unify_meta2 = stylize_face_blend(candidate_url, prompt)
        meta["stages"].append({"stage": "style_unification_t2i", **unify_meta2})
        if unified_url2:
            return unified_url2, meta
        return candidate_url, meta

    if not strict and base_url:
        meta["warning"] = "faceswap_failed_returning_t2i_base"
        return base_url, meta

    meta["error"] = "all_generation_stages_failed"
    return None, meta


def apply_identity_reference(base_url: str, photo_urls: list, strict: bool = False) -> tuple[str | None, dict]:
    """Apply face reference after scene generation.

    fal-ai/face-swap currently expects swap_image_url for the identity/source face.
    Older code used face_image_url, which caused the face transfer to never run.
    """
    meta = {"attempted": False, "success": False, "method": "none", "error": None}
    if not base_url or not photo_urls:
        meta["error"] = "missing_base_or_reference"
        return (None if strict else base_url), meta

    reference_url = photo_urls[0]
    attempts = [
        ("fal-ai/face-swap:swap_image_url", {"base_image_url": base_url, "swap_image_url": reference_url}),
        # Compatibility fallback for accounts/endpoints that still use source/target naming.
        ("fal-ai/face-swap:source_target", {"target_image_url": base_url, "source_image_url": reference_url}),
        # Last legacy fallback; normally not used now.
        ("fal-ai/face-swap:legacy_face_image_url", {"base_image_url": base_url, "face_image_url": reference_url}),
    ]

    for method, args in attempts:
        try:
            print(f"[AI] Applying identity face reference via {method}...")
            meta.update({"attempted": True, "method": method, "error": None})
            result = _fal_run("fal-ai/face-swap", args)
            url = _extract_fal_image_url(result)
            if url:
                meta.update({"success": True, "result_url": url})
                return url, meta
            meta["error"] = f"no_image_url_in_response:{str(result)[:300]}"
        except Exception as e:
            meta["error"] = str(e)[:800]
            print(f"[AI] Face reference step failed via {method}: {e}")

    return (None if strict else base_url), meta


def get_template_base_image(template_id: str) -> str | None:
    """Retrieve the Antonos artist template base image URL from GCS.

    These images are the REAL Antonos illustrations for each template.
    They are uploaded manually via /api/admin/upload-template-base.
    Naming convention in GCS: template_bases/{template_id}.jpg

    Returns the public GCS URL or None if not found.
    """
    try:
        bucket = gcs_client.bucket(CFG["GCS_BUCKET"])
        folder = CFG.get("TEMPLATE_IMAGES_FOLDER", "template_bases")
        blob_name = f"{folder}/{template_id}.jpg"
        blob = bucket.blob(blob_name)
        if blob.exists():
            url = f"https://storage.googleapis.com/{CFG['GCS_BUCKET']}/{blob_name}"
            print(f"[TemplateBase] Found template base image: {url}")
            return url
        # Also try PNG
        blob_png = bucket.blob(f"{folder}/{template_id}.png")
        if blob_png.exists():
            url = f"https://storage.googleapis.com/{CFG['GCS_BUCKET']}/{folder}/{template_id}.png"
            print(f"[TemplateBase] Found template base image (PNG): {url}")
            return url
        print(f"[TemplateBase] No base image for template_id={template_id}")
        return None
    except Exception as e:
        print(f"[TemplateBase] Error checking template base: {e}")
        return None


def stylize_face_blend(image_url: str, prompt: str) -> tuple[str | None, dict]:
    """Stage 3 of v2.6.0 pipeline: style unification after face-swap.

    After face-swap, the customer's photographic face sits on the caricature body.
    This step runs img2img at VERY LOW strength (0.20-0.25) with the Antonos LoRA.
    Effect: the LoRA 'pushes' the photographic face toward caricature illustration style
    while the low strength preserves the template composition and background unchanged.

    strength 0.20 = 80% of pixels unchanged, 20% guided by prompt/LoRA.
    The face area changes more than flat backgrounds, which is exactly what we want.
    """
    meta = {"attempted": False, "success": False, "error": None}
    if not CFG.get("FAL_LORA_URL"):
        meta["error"] = "no_lora_url_configured"
        return None, meta

    strength = float(CFG.get("STYLE_UNIFY_STRENGTH", 0.22))
    lora_scale = float(CFG.get("STYLE_UNIFY_LORA_SCALE", 0.90))
    steps = int(CFG.get("STYLE_UNIFY_STEPS", 28))
    guidance = float(CFG.get("STYLE_UNIFY_GUIDANCE", 8.0))

    unify_prompt = (
        "ANTONOS hand-drawn caricature illustration style, bold confident ink outlines, "
        "cel-shaded cartoon skin, vivid warm editorial colours, exaggerated caricature features, "
        "NOT photorealistic, NOT a beauty filter, drawn illustration, "
        + prompt
    )

    try:
        meta["attempted"] = True
        print(f"[StyleUnify] img2img blend strength={strength} lora_scale={lora_scale}")
        result = _fal_run("fal-ai/flux-lora/image-to-image", {
            "prompt": unify_prompt,
            "image_url": image_url,
            "strength": strength,
            "image_size": "portrait_4_3",
            "num_images": 1,
            "num_inference_steps": steps,
            "guidance_scale": guidance,
            "enable_safety_checker": True,
            "output_format": "jpeg",
            "loras": [{"path": CFG["FAL_LORA_URL"], "scale": lora_scale}],
        })
        url = _extract_fal_image_url(result)
        if url:
            meta.update({"success": True, "result_url": url})
            print(f"[StyleUnify] Success: {url}")
            return url, meta
        meta["error"] = f"no_url_in_response:{str(result)[:300]}"
    except Exception as e:
        meta["error"] = str(e)[:800]
        print(f"[StyleUnify] Failed: {e}")

    return None, meta


def upscale_to_4k(image_url: str, order_id: str) -> tuple[bytes, dict]:
    """Return high-resolution bytes. Tries AI upscale first, then Pillow local upscale, then original."""
    meta = {"target": "4k", "method": "original", "width": None, "height": None, "upscaled": False}

    # 1) Try fal upscaler models. Model names vary by fal account availability, so fail safely.
    for model in [CFG.get("FAL_UPSCALE_MODEL"), "fal-ai/esrgan", "fal-ai/aura-sr"]:
        if not model:
            continue
        try:
            print(f"[4K] Trying upscaler: {model}")
            result = _fal_run(model, {"image_url": image_url, "scale": 4})
            up_url = None
            if isinstance(result, dict):
                if result.get("image") and isinstance(result["image"], dict):
                    up_url = result["image"].get("url")
                elif result.get("images"):
                    up_url = result["images"][0].get("url")
                elif result.get("url"):
                    up_url = result.get("url")
            if up_url:
                b = requests.get(up_url, timeout=45).content
                meta.update({"method": model, "upscaled": True})
                return b, meta
        except Exception as e:
            print(f"[4K] Upscaler {model} failed: {e}")

    # 2) Local upscale if Pillow exists in the environment.
    try:
        from PIL import Image
        from io import BytesIO
        raw = requests.get(image_url, timeout=45).content
        im = Image.open(BytesIO(raw)).convert("RGB")
        w, h = im.size
        scale = max(1, int(max(3840 / max(w, 1), 2160 / max(h, 1))))
        scale = min(max(scale, 2), 4)
        new_size = (w * scale, h * scale)
        im = im.resize(new_size, Image.Resampling.LANCZOS)
        out = BytesIO()
        im.save(out, format="JPEG", quality=95, optimize=True)
        meta.update({"method": "pillow_lanczos", "width": new_size[0], "height": new_size[1], "upscaled": True})
        return out.getvalue(), meta
    except Exception as e:
        print(f"[4K] Local upscale unavailable/failed: {e}")

    # 3) Last safe fallback.
    raw = requests.get(image_url, timeout=45).content
    return raw, meta


def assess_generated_result(prompt: str, result_url: str, reference_url: str | None = None) -> dict:
    """Lightweight post-generation QA. Does not block delivery unless generation obviously failed."""
    try:
        content = []
        result_resp = requests.get(result_url, timeout=20)
        result_resp.raise_for_status()
        result_media = result_resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
        result_bytes, result_media, result_meta = prepare_image_for_vision_bytes(result_resp.content, result_media)
        print(f"[QA] Vision result image: {result_meta}")
        content.append({"type": "image", "source": {"type": "base64", "media_type": result_media, "data": base64.b64encode(result_bytes).decode()}})
        if reference_url:
            ref_resp = requests.get(reference_url, timeout=20)
            ref_resp.raise_for_status()
            ref_media = ref_resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
            ref_bytes, ref_media, ref_meta = prepare_image_for_vision_bytes(ref_resp.content, ref_media)
            print(f"[QA] Vision reference image: {ref_meta}")
            content.append({"type": "image", "source": {"type": "base64", "media_type": ref_media, "data": base64.b64encode(ref_bytes).decode()}})
        content.append({"type": "text", "text": (
            "Assess this generated image for customer delivery as an AI CARICATURE product. "
            "Return ONLY JSON: {\"deliverable\":true/false,\"quality_score\":1-10,\"identity_score\":1-10,\"style_score\":1-10,\"reason\":\"...\"}. "
            "quality_score = technical completeness, clean image, no corruption, no missing face/body. "
            "identity_score = likeness to the reference: face shape, eyes, nose, mouth, hairline/hair, age, expression, distinctive features. "
            "style_score = how clearly it is a hand-drawn cartoon/caricature illustration with visible linework, stylised shading, exaggerated proportions, and non-photorealistic finish. "
            "IMPORTANT: If the result looks like a retouched photo, selfie, realistic portrait, beauty filter, or simple face enhancement, style_score must be 1-3 and deliverable must be false even if identity is good. "
            "Caricature exaggeration is allowed, but wrong subject, weak likeness, photorealism, blank/corrupted image, no face, or generic beauty portrait should fail."
        )})
        msg = claude_client.messages.create(model="claude-opus-4-20250514", max_tokens=180, messages=[{"role": "user", "content": content}])
        raw = msg.content[0].text.strip()
        if "{" in raw and "}" in raw:
            raw = raw[raw.index("{"):raw.rindex("}")+1]
        data = json.loads(raw)
        data["quality_score"] = int(data.get("quality_score", 7))
        data["identity_score"] = int(data.get("identity_score", 7))
        data["style_score"] = int(data.get("style_score", 1))
        if data["style_score"] < int(CFG.get("MIN_STYLE_SCORE", 6)):
            data["deliverable"] = False
        else:
            data["deliverable"] = bool(data.get("deliverable", True))
        return data
    except Exception as e:
        print(f"[QA] Result QA failed open: {e}")
        return {"deliverable": False, "quality_score": 5, "identity_score": 5, "style_score": 1, "reason": "qa_failed_open_style_unknown"}


def generate_with_lora(prompt: str, photo_urls: list) -> str | None:
    """Backward-compatible wrapper used by older routes/tests."""
    base = generate_base_art(prompt, photo_urls, attempt=1)
    if not base:
        return None
    url, _identity_meta = apply_identity_reference(base, photo_urls, strict=False)
    return url


def run_generation_pipeline(order_id: str):
    """Main AI pipeline — Stripe-safe, identity-aware, 4K-oriented."""
    order = None
    try:
        order = get_order(order_id)
        if not order:
            raise Exception("Order not found")

        update_order_status(order_id, "generating", {"pipeline_version": "2.8.0-inpainting"})
        print(f"[Pipeline] Starting generation for {order_id}")

        template_id    = order["template_id"]
        template       = TEMPLATES.get(template_id, TEMPLATES["custom_free"])
        photo_urls     = order.get("photo_urls", [])
        person_photos  = order.get("person_photos", [])
        answers        = order.get("answers", {}) or {}
        email          = order["email"]
        name           = order["name"]
        plan_id        = order["plan_id"]
        template_name  = order.get("template_name") or template.get("name", template_id)

        # 1. Claude Vision: scene prompt + face description (v2.7.0)
        print("[Pipeline] Claude building identity-aware prompt and face description...")
        prompt = analyze_photo_with_claude(
            photo_urls=photo_urls,
            persons=order.get("persons", "1"),
            template_name=template_name,
            answers={**answers, "template_id": template_id, "notes": order.get("notes", answers.get("notes", ""))},
            person_photos=person_photos,
        )
        print(f"[Pipeline] Prompt: {prompt[:180]}...")
        # v2.7.0: extract face description for template img2img face replacement
        face_description = extract_face_description(photo_urls) if photo_urls else ""
        print(f"[Pipeline] Face description: {face_description}")

        # 2. v2.7.0 template img2img generation + QA retries
        final_ai_url = None
        qa = {}
        generation_debug = []
        attempts = max(1, min(3, int(CFG.get("MAX_GENERATION_RETRIES", 2))))
        for attempt in range(1, attempts + 1):
            print(f"[Pipeline v2.7] Template img2img attempt {attempt}/{attempts}")
            candidate_url, generation_meta = generate_candidate_art(
                prompt, photo_urls, attempt=attempt, strict=True,
                template_id=template_id, face_description=face_description,
                template_name=template_name
            )
            generation_debug.append({"attempt": attempt, "candidate_url": candidate_url, "generation_meta": generation_meta})
            if not candidate_url:
                qa = {"deliverable": False, "quality_score": 1, "identity_score": 1, "reason": "generation_candidate_failed", "generation_meta": generation_meta}
                print(f"[Pipeline v2.4] Candidate failed attempt={attempt}: {generation_meta}")
                continue
            qa = assess_generated_result(prompt, candidate_url, photo_urls[0] if photo_urls else None)
            qa["generation_meta"] = generation_meta
            print(f"[Pipeline v2.4] QA attempt={attempt}: {json.dumps(qa, ensure_ascii=False)[:1200]}")
            if (qa.get("deliverable", True) and qa.get("quality_score", 7) >= int(CFG.get("MIN_QUALITY_SCORE", 6)) and qa.get("identity_score", 7) >= int(CFG.get("MIN_IDENTITY_SCORE", 6)) and qa.get("style_score", 0) >= int(CFG.get("MIN_STYLE_SCORE", 6))):
                final_ai_url = candidate_url
                break
            prompt += ", preserve exact uploaded identity, same age and face structure, avoid generic beauty portrait, stronger likeness, stronger hand-drawn caricature linework, ink outlines, cel shaded cartoon skin, not photorealistic"

        if not final_ai_url:
            raise Exception("AI generation did not produce a deliverable image")

        # 3. 4K/upscale output packaging
        print("[Pipeline] Preparing 4K/high-resolution delivery...")
        img_bytes, upscale_meta = upscale_to_4k(final_ai_url, order_id)
        filename = f"result_{order_id}_4k.jpg" if upscale_meta.get("upscaled") else f"result_{order_id}.jpg"
        result_url = upload_to_gcs(img_bytes, filename, "image/jpeg", folder="results")
        print(f"[Pipeline] Saved to GCS: {result_url} | upscale={upscale_meta}")

        # 4. Send email
        send_result_email(email, name, [result_url], order_id, template_name, plan_id)

        # 5. Complete
        update_order_status(order_id, "completed", {
            "result_urls": [result_url],
            "raw_ai_url": final_ai_url,
            "completed_at": datetime.utcnow().isoformat(),
            "review_email_sent": False,
            "generation_prompt": prompt[:1500],
            "generation_qa": qa,
            "upscale_meta": upscale_meta,
            "generation_debug": generation_debug[-3:] if 'generation_debug' in locals() else [],
        })
        notify_admin(f"✅ Order {order_id} completed | {template_name} | {email}")

    except Exception as e:
        print(f"[Pipeline] ERROR for {order_id}: {e}")
        try:
            update_order_status(order_id, "failed", {"error": str(e), "pipeline_version": "2.8.0-inpainting"})
        except Exception:
            pass
        notify_admin(f"❌ Order {order_id} FAILED: {e}")
        try:
            if order:
                send_fallback_email(order.get("email", ""), order.get("name", ""), order_id)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#   EMAIL
# ══════════════════════════════════════════════════════════════

def send_result_email(email, name, result_urls, order_id, template_name, plan_id):
    plan = PLANS.get(plan_id, PLANS["standard"])
    links_html = "".join([
        f'<a href="{url}" style="display:block;background:#c8792a;color:white;'
        f'padding:14px 28px;margin:8px 0;text-decoration:none;font-weight:bold;'
        f'font-family:Georgia,serif;letter-spacing:1px;font-size:14px;border-radius:4px;">'
        f'⬇ Download Your Caricature</a>'
        for url in result_urls
    ])

    html = f"""
    <!DOCTYPE html><html><body style="background:#f8f0e3;font-family:Georgia,serif;padding:40px 20px">
    <div style="max-width:520px;margin:0 auto;background:#fffdf9;border:2px solid #c8792a;padding:48px;border-radius:8px">
      <div style="font-family:Georgia,serif;font-size:32px;font-weight:700;color:#0f0d0a;margin-bottom:4px">
        Caricature<span style="color:#c8792a">.</span>online
      </div>
      <div style="border-top:2px solid #c8792a;margin:16px 0 32px"></div>
      <h1 style="font-size:36px;font-weight:700;margin:0 0 8px;line-height:1.1;color:#0f0d0a">
        Your Art Is<br><em style="color:#c8792a">Ready!</em>
      </h1>
      <p style="color:#8a7968;font-size:15px;line-height:1.7;margin:16px 0 8px">
        Hi {name} — your <strong>{template_name}</strong> caricature is ready in the style of <strong>Antonos</strong>.
      </p>
      <p style="color:#8a7968;font-size:13px;margin:0 0 32px">Download link expires in <strong>72 hours</strong>.</p>
      {links_html}
      <div style="margin-top:32px;padding:20px;background:#f8f0e3;border-radius:6px;border:1px solid #e8ddd0">
        <p style="font-size:13px;color:#8a7968;margin:0;line-height:1.7">
          ✨ <strong>Want a handmade version?</strong> Visit <a href="https://caricature.photo" style="color:#c8792a">caricature.photo</a> for premium hand-drawn caricatures by the same artist.<br><br>
          📧 Questions? Reply to this email.<br>
          Order ID: <code style="font-size:11px">{order_id}</code>
        </p>
      </div>
    </div>
    </body></html>
    """

    resend.Emails.send({
        "from": CFG["RESEND_FROM"],
        "to": [email],
        "subject": f"🎨 Your Caricature is Ready — {template_name}",
        "html": html
    })


def send_fallback_email(email, name, order_id):
    """Send email when AI fails — offer manual caricature."""
    if not email:
        return
    html = f"""
    <!DOCTYPE html><html><body style="background:#f8f0e3;font-family:Georgia,serif;padding:40px 20px">
    <div style="max-width:520px;margin:0 auto;background:#fffdf9;border:2px solid #c8792a;padding:48px;border-radius:8px">
      <div style="font-family:Georgia,serif;font-size:32px;font-weight:700;color:#0f0d0a;margin-bottom:16px">
        Caricature<span style="color:#c8792a">.</span>online
      </div>
      <h1 style="font-size:28px;font-weight:700;margin:0 0 16px;color:#0f0d0a">We're on it, {name}!</h1>
      <p style="color:#8a7968;font-size:15px;line-height:1.7">
        We encountered a small technical hiccup with your AI generation. 
        Our artist <strong>Antonos</strong> will personally complete your caricature within <strong>24 hours</strong>.
      </p>
      <p style="color:#8a7968;font-size:15px;line-height:1.7;margin-top:16px">
        You'll receive another email with your caricature shortly. No action needed from you.
      </p>
      <p style="color:#8a7968;font-size:13px;margin-top:24px">Order ID: <code>{order_id}</code></p>
    </div>
    </body></html>
    """
    resend.Emails.send({
        "from": CFG["RESEND_FROM"],
        "to": [email],
        "subject": "🎨 Your Caricature — We're On It!",
        "html": html
    })


# ══════════════════════════════════════════════════════════════
#   ROUTES — TEMPLATES
# ══════════════════════════════════════════════════════════════

@app.route("/api/templates", methods=["GET"])
def get_templates():
    occasion = request.args.get("occasion")
    persons  = request.args.get("persons")

    filtered = {}
    for tid, t in TEMPLATES.items():
        if occasion and t["occasion"] != occasion:
            continue
        if persons:
            try:
                p = int(persons)
                tp = t["persons"]
                if isinstance(tp, int) and tp != p:
                    # Allow templates for <= requested persons
                    if tp > p:
                        continue
            except:
                pass
        filtered[tid] = t

    return ok({"templates": filtered, "total": len(filtered)})

@app.route("/api/occasions", methods=["GET"])
def get_occasions():
    return ok({"occasions": OCCASIONS})

@app.route("/api/pricing", methods=["GET"])
def get_pricing():
    persons = request.args.get("persons", "1")
    plan_id = request.args.get("plan", "standard")
    cents   = calc_price(persons, plan_id)
    return ok({
        "persons": persons,
        "plan": plan_id,
        "amount_cents": cents,
        "amount_display": f"€{cents/100:.2f}",
        "plans": PLANS,
        "publishable_key": CFG["STRIPE_PUB"],
    })


# ══════════════════════════════════════════════════════════════
#   ROUTES — UPLOAD
# ══════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════
#   PHOTO QUALITY ASSESSMENT
# ══════════════════════════════════════════════════════════════

def _quality_default_accepted(reason: str = "quality_check_failed") -> dict:
    """Fail-open default: do not block real customer portraits when AI quality checks fail."""
    return {
        "score": 5,
        "face_size": "unknown",
        "lighting": "unknown",
        "sharpness": "unknown",
        "usable": True,
        "visible_features": [],
        "warnings": [reason],
        "recovery_strategy": "partial_analysis",
        "hard_block": False,
    }


def _normalize_quality_result(result: dict) -> dict:
    """Normalize Claude's JSON so frontend receives stable, compatible fields."""
    base = _quality_default_accepted("quality_check_uncertain")
    if not isinstance(result, dict):
        result = {}
    out = {**base, **result}

    try:
        out["score"] = max(1, min(10, int(out.get("score", 5))))
    except Exception:
        out["score"] = 5

    warnings = out.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = [str(warnings)]
    warnings = [str(w).strip() for w in warnings if str(w).strip()]

    face_size = str(out.get("face_size", "unknown")).lower().strip()
    out["face_size"] = face_size

    # Strict block only for confident non-person uploads or real multi-person foregrounds.
    hard_no_face = face_size == "none" and "no_face" in warnings and out["score"] <= 2
    hard_multi = face_size == "multiple" and "multiple_faces" in warnings

    if hard_no_face:
        out.update({
            "score": min(out["score"], 2),
            "usable": False,
            "hard_block": True,
            "recovery_strategy": "template_only",
        })
        if "no_face" not in warnings:
            warnings.append("no_face")
    elif hard_multi:
        out.update({
            "score": min(out["score"], 3),
            "usable": False,
            "hard_block": True,
            "recovery_strategy": "template_only",
        })
        if "multiple_faces" not in warnings:
            warnings.append("multiple_faces")
    else:
        # If there is any uncertainty, accept the upload and let the frontend show a gentle warning.
        out["usable"] = True
        out["hard_block"] = False
        if face_size in ("none", "multiple"):
            out["face_size"] = "unknown"
            if "quality_check_uncertain" not in warnings:
                warnings.append("quality_check_uncertain")
        if out["score"] < 3:
            out["score"] = 3

    out["warnings"] = warnings
    return out


def assess_photo_quality_bytes(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    """Claude Vision quality check directly from upload bytes.

    This avoids false negatives caused by downloading the just-uploaded GCS URL
    before public access/ACL/CDN state is ready. Fail-open on technical errors.
    """
    import base64, json as _json

    if not image_bytes:
        return {
            "score": 1, "face_size": "none", "lighting": "unknown",
            "sharpness": "unknown", "usable": False, "visible_features": [],
            "warnings": ["no_face"], "recovery_strategy": "template_only",
            "hard_block": True,
        }

    no_face_resp = (
        '{"score":1,"face_size":"none","lighting":"unknown","sharpness":"unknown",'
        '"usable":false,"visible_features":[],"warnings":["no_face"],'
        '"recovery_strategy":"template_only","hard_block":true}'
    )
    multi_face_resp = (
        '{"score":2,"face_size":"multiple","lighting":"unknown","sharpness":"unknown",'
        '"usable":false,"visible_features":[],"warnings":["multiple_faces"],'
        '"recovery_strategy":"template_only","hard_block":true}'
    )

    prompt = (
        "You are assessing a customer-uploaded photo for AI caricature generation.\n\n"
        "GOAL: Be permissive with real human portraits, especially babies, toddlers, cropped portraits, "
        "slightly angled faces, mild blur, bright photos, or portraits with harmless background activity.\n\n"
        "CASE A — Clearly NOT a person photo: QR codes, screenshots, diagrams, charts, text pages, "
        "objects, animals, landscapes with no visible human face, abstract images. Only choose this if certain.\n"
        "Return: " + no_face_resp + "\n\n"
        "CASE B — Multiple main people: two or more clear, in-focus foreground faces of similar importance. "
        "Do NOT count blurred background figures, tiny distant people, partial heads at the edge, or a baby held low/partly visible "
        "when one adult is clearly the main portrait subject. Only choose this if certain.\n"
        "Return: " + multi_face_resp + "\n\n"
        "CASE C — One human face is visible as the main subject. Babies, toddlers, children and elderly people are valid. "
        "If any human face is visible and it is not clearly CASE A/B, choose CASE C.\n"
        "Return JSON with: score 1-10, face_size large|medium|small|unknown, lighting good|backlit|dark|harsh|unknown, "
        "sharpness sharp|slightly_blurred|blurred|unknown, usable true, visible_features array, warnings array from "
        "[face_too_small,backlit,blurred,dark,low_resolution,obstructed,quality_check_uncertain], "
        "recovery_strategy full_analysis if score>=6 else partial_analysis, hard_block false.\n\n"
        "IMPORTANT: When in doubt, choose CASE C with usable=true. Return ONLY valid JSON."
    )

    try:
        safe_bytes, safe_media_type, vision_meta = prepare_image_for_vision_bytes(image_bytes, media_type)
        print(f"[Quality] Vision image: {vision_meta}")
        img_b64 = base64.b64encode(safe_bytes).decode()
        msg = claude_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=350,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": safe_media_type or "image/jpeg", "data": img_b64}},
                {"type": "text",  "text": prompt}
            ]}]
        )
        raw = msg.content[0].text.strip()
        if "{" in raw and "}" in raw:
            raw = raw[raw.index("{"):raw.rindex("}")+1]
        result = _normalize_quality_result(_json.loads(raw))
        print(f"[Quality] score={result.get('score')} face={result.get('face_size')} usable={result.get('usable')} hard={result.get('hard_block')} warns={result.get('warnings')}")
        return result
    except Exception as e:
        print(f"[Quality] Error: {e} — accepting upload with warning")
        return _quality_default_accepted("quality_check_failed")


def assess_photo_quality(photo_url: str) -> dict:
    """Backward-compatible URL wrapper. Prefer assess_photo_quality_bytes() in /api/upload."""
    try:
        resp = requests.get(photo_url, timeout=15)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
        return assess_photo_quality_bytes(resp.content, ctype)
    except Exception as e:
        print(f"[QualityURL] Error: {e} — accepting upload with warning")
        return _quality_default_accepted("quality_check_failed")


@app.route("/api/upload", methods=["POST"])
def upload_photo():
    if "file" not in request.files:
        return err("No file provided")
    file = request.files["file"]
    if not file.filename:
        return err("Empty filename")
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        return err("Invalid file type. Use JPG, PNG or WEBP.")
    data = file.read()
    if len(data) > 10 * 1024 * 1024:
        return err("File too large. Max 10MB.")

    upload_id = str(uuid.uuid4())
    ext = file.filename.rsplit(".", 1)[-1].lower()
    filename = f"{upload_id}.{ext}"

    try:
        # Check quality directly from upload bytes before/independent of public GCS URL availability.
        quality   = assess_photo_quality_bytes(data, file.content_type)
        photo_url = upload_to_gcs(data, filename, file.content_type, folder="uploads")
        warns     = quality.get("warnings", ["no_face"])
        score     = quality.get("score", 1)
        db.collection("uploads").document(upload_id).set({
            "upload_id":  upload_id,
            "photo_url":  photo_url,
            "quality":    quality,
            "created_at": datetime.utcnow().isoformat(),
        })
        return ok({
            "upload_id": upload_id,
            "photo_url": photo_url,
            "quality":   quality,
            "score":     score,
            "warnings":  warns,
            "usable":    quality.get("usable", False),
        })
    except Exception as e:
        return err(f"Upload failed: {str(e)}", 500)

# ══════════════════════════════════════════════════════════════
#   ROUTES — PAYMENT
# ══════════════════════════════════════════════════════════════

@app.route("/api/create-payment-intent", methods=["POST"])
def create_payment_intent():
    body = request.get_json(silent=True) or {}

    moderation = moderate_order_request(body)
    if not moderation.get("allowed"):
        try:
            db.collection("moderation_logs").add({
                "created_at": datetime.utcnow().isoformat(),
                "stage": "create_payment_intent",
                "reason": moderation.get("reason"),
                "hits": moderation.get("hits", []),
                "template_id": body.get("template_id"),
                "email": (body.get("email", "") or "").strip().lower(),
            })
        except Exception as e:
            print(f"[Moderation] log failed: {e}")
        return err("This request cannot be processed because it violates our content guidelines.", 400)

    template_id = body.get("template_id") or "custom_free"
    if not template_id or template_id not in TEMPLATES:
        template_id = "custom_free" if body.get("flow") == "free" or body.get("description") else template_id
    if not template_id or template_id not in TEMPLATES:
        return err("Invalid template")

    upload_ids = [str(x) for x in (body.get("upload_ids", []) or []) if x]
    # Backward compatibility with older/newer order.html payload: person_photos -> flat upload_ids
    if isinstance(body.get("person_photos"), list):
        for person in body.get("person_photos", []):
            if isinstance(person, dict):
                for uid in person.get("upload_ids", []) or []:
                    if uid and uid not in upload_ids:
                        upload_ids.append(str(uid))

    persons = str(body.get("persons", "1") or "1")
    plan_id = body.get("plan", "standard")
    answers = body.get("answers", {}) or {}
    notes = body.get("notes", "") or body.get("description", "") or answers.get("notes", "")
    email = (body.get("email", "") or "").strip().lower()
    name = (body.get("name", "") or "").strip()
    flow = body.get("flow", "template")
    description = body.get("description", "") or answers.get("description", "")

    if not upload_ids:
        return err("Please upload at least one photo")
    if not email or "@" not in email:
        return err("Invalid email")
    if plan_id not in PLANS:
        plan_id = "standard"

    person_photos_payload = build_person_photos_from_payload(body, upload_ids)
    photo_urls, resolved_person_photos = resolve_uploads(upload_ids, person_photos_payload)
    if not photo_urls:
        return err("Photos not found. Please re-upload.")

    amount_cents = calc_price(persons, plan_id)
    order_id = f"ord_{str(uuid.uuid4()).replace('-', '')[:16]}"
    template = TEMPLATES.get(template_id, TEMPLATES["custom_free"])

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="eur",
            automatic_payment_methods={"enabled": True},
            receipt_email=email,
            metadata={
                "order_id": order_id,
                "template_id": template_id,
                "plan_id": plan_id,
                "persons": str(persons),
                "pipeline": "v2.8.0-inpainting",
            },
            description=f"Caricature — {template['name']} ({plan_id})"
        )
    except stripe.error.StripeError as e:
        return err(f"Payment setup failed: {getattr(e, 'user_message', str(e))}", 402)

    log_order(order_id, {
        "order_id": order_id,
        "stripe_intent": intent.id,
        "template_id": template_id,
        "template_name": template["name"],
        "occasion": template["occasion"],
        "persons": persons,
        "plan_id": plan_id,
        "amount_cents": amount_cents,
        "photo_urls": photo_urls,
        "upload_ids": upload_ids,
        "person_photos": resolved_person_photos,
        "answers": answers,
        "notes": notes,
        "description": description,
        "flow": flow,
        "email": email,
        "name": name,
        "status": "pending",
        "pipeline_version": "2.8.0-inpainting",
        "moderation": moderation,
        "created_at": datetime.utcnow().isoformat(),
    })

    return ok({
        "clientSecret": intent.client_secret,
        "order_id": order_id,
        "publishable_key": CFG["STRIPE_PUB"],
        "amount_cents": amount_cents,
        "amount_display": f"€{amount_cents/100:.2f}",
    })


# ══════════════════════════════════════════════════════════════
#   ROUTES — STRIPE WEBHOOK
# ══════════════════════════════════════════════════════════════

@app.route("/api/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload    = request.data
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, CFG["STRIPE_WEBHOOK_SEC"])
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        return Response(f"Webhook error: {e}", status=400)

    etype = event["type"]
    print(f"[Webhook] {etype}")

    if etype == "payment_intent.succeeded":
        intent   = event["data"]["object"]
        order_id = intent["metadata"].get("order_id")
        if order_id:
            update_order_status(order_id, "paid", {
                "paid_at": datetime.utcnow().isoformat(),
                "amount_received": intent["amount_received"],
            })
            t = threading.Thread(target=run_generation_pipeline, args=(order_id,))
            t.daemon = True
            t.start()

    elif etype == "payment_intent.payment_failed":
        intent   = event["data"]["object"]
        order_id = intent["metadata"].get("order_id")
        if order_id:
            update_order_status(order_id, "failed")

    elif etype == "charge.refunded":
        charge   = event["data"]["object"]
        order_id = charge.get("metadata", {}).get("order_id")
        if order_id:
            update_order_status(order_id, "refunded")

    return Response(status=200)


# ══════════════════════════════════════════════════════════════
#   ROUTES — ORDER STATUS
# ══════════════════════════════════════════════════════════════

@app.route("/api/order-status/<order_id>", methods=["GET"])
def order_status(order_id):
    order = get_order(order_id)
    if not order:
        return err("Order not found", 404)
    resp = {
        "order_id":     order_id,
        "status":       order["status"],
        "template":     order.get("template_name"),
        "plan":         order.get("plan_id"),
    }
    if order["status"] == "completed":
        resp["result_urls"] = order.get("result_urls", [])
    return ok(resp)


# ══════════════════════════════════════════════════════════════
#   ROUTES — ADMIN
# ══════════════════════════════════════════════════════════════

@app.route("/api/admin/orders", methods=["GET"])
@require_admin
def admin_orders():
    limit = int(request.args.get("limit", 20))
    docs  = db.collection("orders").order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream()
    orders = [d.to_dict() for d in docs]
    return ok({"orders": orders, "count": len(orders)})

@app.route("/api/admin/stats", methods=["GET"])
@require_admin
def admin_stats():
    docs   = list(db.collection("orders").stream())
    orders = [d.to_dict() for d in docs]
    total  = len(orders)
    completed = [o for o in orders if o.get("status") == "completed"]
    revenue   = sum(o.get("amount_cents", 0) for o in completed) / 100

    by_occasion  = {}
    by_template  = {}
    by_plan      = {}

    for o in completed:
        occ = o.get("occasion", "unknown")
        by_occasion[occ] = by_occasion.get(occ, 0) + 1
        tpl = o.get("template_name", "unknown")
        by_template[tpl] = by_template.get(tpl, 0) + 1
        pln = o.get("plan_id", "standard")
        by_plan[pln] = by_plan.get(pln, 0) + 1

    return ok({
        "total_orders":    total,
        "completed_orders": len(completed),
        "revenue_eur":     round(revenue, 2),
        "by_occasion":     by_occasion,
        "by_template":     by_template,
        "by_plan":         by_plan,
    })

@app.route("/api/admin/retry/<order_id>", methods=["POST"])
@require_admin
def admin_retry(order_id):
    order = get_order(order_id)
    if not order:
        return err("Order not found", 404)
    t = threading.Thread(target=run_generation_pipeline, args=(order_id,))
    t.daemon = True
    t.start()
    return ok({"message": f"Retrying order {order_id}"})

@app.route("/api/admin/refund/<order_id>", methods=["POST"])
@require_admin
def admin_refund(order_id):
    order = get_order(order_id)
    if not order:
        return err("Order not found", 404)
    try:
        intent_id = order.get("stripe_intent")
        if intent_id:
            stripe.Refund.create(payment_intent=intent_id)
        update_order_status(order_id, "refunded")
        return ok({"message": f"Refunded order {order_id}"})
    except Exception as e:
        return err(str(e), 500)


# ══════════════════════════════════════════════════════════════
#   ROUTES — GALLERY (sample work)
# ══════════════════════════════════════════════════════════════

@app.route("/api/gallery", methods=["GET"])
def get_gallery():
    styles = {}
    for doc in db.collection("gallery").where("active", "==", True).stream():
        d = doc.to_dict()
        styles[d["style_id"]] = {
            "image_url":  d.get("image_url"),
            "updated_at": d.get("updated_at"),
            "style_id":   d["style_id"],
        }
    return ok({"gallery": styles})


# ══════════════════════════════════════════════════════════════
#   REVIEW EMAIL SYSTEM
# ══════════════════════════════════════════════════════════════

def send_review_request_email(email: str, name: str, order_id: str, result_url: str, template_name: str):
    """Send review request email 24h after order completion."""
    review_url = f"{CFG['BASE_URL']}/review.html?order={order_id}"
    html = f"""
    <!DOCTYPE html><html><body style="background:#f8f0e3;font-family:Georgia,serif;padding:40px 20px">
    <div style="max-width:520px;margin:0 auto;background:#fffdf9;border:2px solid #c8792a;padding:48px;border-radius:8px">
      <div style="font-family:Georgia,serif;font-size:32px;font-weight:700;color:#0f0d0a;margin-bottom:4px">
        Caricature<span style="color:#c8792a">.</span>online
      </div>
      <div style="border-top:2px solid #c8792a;margin:16px 0 32px"></div>
      <h1 style="font-size:28px;font-weight:700;margin:0 0 16px;color:#0f0d0a">
        How did we do, {name}?
      </h1>
      <p style="color:#8a7968;font-size:15px;line-height:1.7;margin:0 0 20px">
        We hope you loved your <strong>{template_name}</strong> caricature! 
        Your feedback takes just 2 minutes and helps us improve — and as a thank you,
        <strong style="color:#c8792a">we'll send you a free caricature immediately after you submit.</strong>
      </p>

      <div style="background:#f5e6d0;border:1px solid #c8792a;border-radius:6px;padding:20px;margin:24px 0;text-align:center">
        <div style="font-size:40px;margin-bottom:8px">🎁</div>
        <div style="font-weight:700;font-size:16px;color:#0f0d0a">Complete the review → Get a free caricature</div>
        <div style="font-size:13px;color:#8a7968;margin-top:4px">Your next caricature, any template, on us.</div>
      </div>

      <a href="{review_url}"
         style="display:block;background:#c8792a;color:white;padding:16px 28px;
                text-decoration:none;font-weight:700;font-size:16px;
                border-radius:6px;text-align:center;margin:24px 0;letter-spacing:.5px">
        ⭐ Leave Your Review & Get Free Caricature
      </a>

      <p style="font-size:13px;color:#8a7968;margin:0;line-height:1.7">
        The form has 5 quick questions about your caricature experience.<br>
        Order ID: <code style="font-size:11px">{order_id}</code>
      </p>
    </div>
    </body></html>
    """
    try:
        resend.Emails.send({
            "from": CFG["RESEND_FROM"],
            "to": [email],
            "subject": "⭐ How was your caricature? (Free one inside!)",
            "html": html
        })
        print(f"[Review] Sent review request to {email} for {order_id}")
    except Exception as e:
        print(f"[Review] Failed to send review email: {e}")


def send_free_caricature_email(email: str, name: str, free_token: str):
    """Send free caricature token after review submission."""
    free_url = f"{CFG['BASE_URL']}/order.html?free_token={free_token}"
    html = f"""
    <!DOCTYPE html><html><body style="background:#f8f0e3;font-family:Georgia,serif;padding:40px 20px">
    <div style="max-width:520px;margin:0 auto;background:#fffdf9;border:2px solid #c8792a;padding:48px;border-radius:8px">
      <div style="font-family:Georgia,serif;font-size:32px;font-weight:700;color:#0f0d0a;margin-bottom:4px">
        Caricature<span style="color:#c8792a">.</span>online
      </div>
      <div style="border-top:2px solid #c8792a;margin:16px 0 32px"></div>
      <h1 style="font-size:32px;font-weight:700;margin:0 0 8px;color:#0f0d0a">
        Your Free Caricature<br><em style="color:#c8792a">is Waiting!</em>
      </h1>
      <p style="color:#8a7968;font-size:15px;line-height:1.7;margin:16px 0 24px">
        Thank you for the feedback, {name}! As promised, here's your free caricature — 
        any template, any occasion, completely on us. No payment needed.
      </p>
      <a href="{free_url}"
         style="display:block;background:#c8792a;color:white;padding:18px 28px;
                text-decoration:none;font-weight:700;font-size:17px;
                border-radius:6px;text-align:center;margin:24px 0;">
        🎨 Create My Free Caricature →
      </a>
      <p style="font-size:13px;color:#8a7968;margin:0;line-height:1.7">
        This link is valid for 7 days and can be used once. No credit card needed.<br>
        Token: <code style="font-size:11px">{free_token}</code>
      </p>
    </div>
    </body></html>
    """
    try:
        resend.Emails.send({
            "from": CFG["RESEND_FROM"],
            "to": [email],
            "subject": "🎁 Your free caricature is waiting — thank you!",
            "html": html
        })
        print(f"[Review] Sent free caricature email to {email}")
    except Exception as e:
        print(f"[Review] Failed to send free email: {e}")


@app.route("/api/cron/review-emails", methods=["POST"])
@require_admin
def cron_review_emails():
    """Cron job: send review emails to orders completed 22-26h ago (runs hourly)."""
    now = datetime.utcnow()
    cutoff_start = (now - timedelta(hours=26)).isoformat()
    cutoff_end   = (now - timedelta(hours=22)).isoformat()

    sent = 0
    try:
        orders = db.collection("orders") \
            .where("status", "==", "completed") \
            .where("review_email_sent", "==", False) \
            .stream()

        for doc in orders:
            order = doc.to_dict()
            completed_at = order.get("completed_at", order.get("created_at", ""))
            if cutoff_start <= completed_at <= cutoff_end:
                send_review_request_email(
                    email=order["email"],
                    name=order["name"],
                    order_id=order["order_id"],
                    result_url=order.get("result_urls", [""])[0],
                    template_name=order.get("template_name", ""),
                )
                db.collection("orders").document(order["order_id"]).update({"review_email_sent": True})
                sent += 1
    except Exception as e:
        print(f"[ReviewCron] Error: {e}")

    return ok({"sent": sent})


@app.route("/api/review/submit", methods=["POST"])
def submit_review():
    """Handle review form submission → store data → send free caricature."""
    body = request.get_json()
    order_id   = body.get("order_id", "").strip()
    email      = body.get("email", "").strip().lower()
    name       = body.get("name", "").strip()
    ratings    = body.get("ratings", {})   # {likeness, quality, speed, overall}
    feedback   = body.get("feedback", "").strip()
    template   = body.get("template_name", "")

    if not order_id or not email:
        return err("Missing order_id or email")

    # Check not already reviewed
    existing = db.collection("reviews").document(order_id).get()
    if existing.exists:
        return err("Already reviewed — check your email for the free caricature link")

    # Generate free token
    free_token = str(uuid.uuid4()).replace("-", "")

    # Store review + free token
    db.collection("reviews").document(order_id).set({
        "order_id":     order_id,
        "email":        email,
        "name":         name,
        "ratings":      ratings,
        "feedback":     feedback,
        "template_name": template,
        "free_token":   free_token,
        "token_used":   False,
        "token_expires": (datetime.utcnow() + timedelta(days=7)).isoformat(),
        "created_at":   datetime.utcnow().isoformat(),
    })

    # Store free token separately for validation
    db.collection("free_tokens").document(free_token).set({
        "free_token":  free_token,
        "email":       email,
        "name":        name,
        "order_id":    order_id,
        "used":        False,
        "expires_at":  (datetime.utcnow() + timedelta(days=7)).isoformat(),
        "created_at":  datetime.utcnow().isoformat(),
    })

    # Send free caricature email
    send_free_caricature_email(email, name, free_token)

    print(f"[Review] Stored review for {order_id}, token {free_token} sent to {email}")
    return ok({"message": "Thank you! Your free caricature link has been sent to your email."})


@app.route("/api/free-order/create", methods=["POST"])
def create_free_order():
    """Create an order using a free token — no Stripe payment needed."""
    body        = request.get_json()
    free_token  = body.get("free_token", "").strip()
    template_id = body.get("template_id") or "custom_free"
    upload_ids  = body.get("upload_ids", [])
    if not upload_ids and isinstance(body.get("person_photos"), list):
        upload_ids = []
        for person in body.get("person_photos", []):
            upload_ids.extend(person.get("upload_ids", []))
    persons     = body.get("persons", "1")
    answers     = body.get("answers", {}) or {}
    notes       = body.get("notes", "") or body.get("description", "") or answers.get("notes", "")
    email       = body.get("email", "").strip().lower()
    name        = body.get("name", "").strip()

    # Validate token
    if not free_token:
        return err("Missing free token")
    token_doc = db.collection("free_tokens").document(free_token).get()
    if not token_doc.exists:
        return err("Invalid or expired token")
    token_data = token_doc.to_dict()
    if token_data.get("used"):
        return err("This free token has already been used")
    if token_data.get("expires_at", "") < datetime.utcnow().isoformat():
        return err("This free token has expired")

    # Validate order inputs
    if not template_id or template_id not in TEMPLATES:
        return err("Invalid template")
    if not upload_ids:
        return err("Please upload at least one photo")
    if not email or "@" not in email:
        return err("Invalid email")

    # Get photo URLs from Firestore
    photo_urls = []
    for uid in upload_ids[:5]:
        doc = db.collection("uploads").document(uid).get()
        if doc.exists:
            photo_urls.append(doc.to_dict()["photo_url"])
    if not photo_urls:
        return err("Photos not found. Please re-upload.")

    template = TEMPLATES.get(template_id, TEMPLATES["custom_free"])
    order_id = f"free_{str(uuid.uuid4()).replace('-','')[:16]}"

    # Mark token as used immediately
    db.collection("free_tokens").document(free_token).update({
        "used": True,
        "used_at": datetime.utcnow().isoformat(),
        "order_id": order_id,
    })

    # Log order (amount = 0, no Stripe)
    log_order(order_id, {
        "order_id":       order_id,
        "stripe_intent":  None,
        "template_id":    template_id,
        "template_name":  template["name"],
        "occasion":       template["occasion"],
        "persons":        persons,
        "plan_id":        "free",
        "amount_cents":   0,
        "photo_urls":     photo_urls,
        "upload_ids":     upload_ids,
        "answers":        answers,
        "notes":          notes,
        "email":          email,
        "name":           name,
        "status":         "pending",
        "is_free":        True,
        "free_token":     free_token,
        "created_at":     datetime.utcnow().isoformat(),
        "review_email_sent": False,
    })

    # Trigger generation pipeline in background
    thread = threading.Thread(target=run_generation_pipeline, args=(order_id,), daemon=True)
    thread.start()

    print(f"[FreeOrder] Created {order_id} for {email} via token {free_token[:8]}…")
    return ok({"order_id": order_id, "message": "Free order created — generating now!"})



def validate_free_token():
    """Validate a free caricature token before order creation."""
    body  = request.get_json()
    token = body.get("free_token", "").strip()
    if not token:
        return err("No token provided")

    doc = db.collection("free_tokens").document(token).get()
    if not doc.exists:
        return err("Invalid or expired token")

    data = doc.to_dict()
    if data.get("used"):
        return err("This token has already been used")
    if data.get("expires_at", "") < datetime.utcnow().isoformat():
        return err("This token has expired")

    return ok({
        "valid": True,
        "email": data["email"],
        "name":  data["name"],
    })


@app.route("/api/free-order/redeem", methods=["POST"])
def redeem_free_token():
    """Mark free token as used after successful order."""
    body  = request.get_json()
    token = body.get("free_token", "").strip()
    if not token:
        return err("No token provided")

    db.collection("free_tokens").document(token).update({
        "used": True,
        "used_at": datetime.utcnow().isoformat(),
    })
    return ok({"redeemed": True})






# ══════════════════════════════════════════════════════════════
#   WEEKLY ANALYTICS REPORT
# ══════════════════════════════════════════════════════════════

def generate_weekly_report() -> dict:
    """Pull all data from Firestore needed for the weekly report."""
    now     = datetime.utcnow()
    week_ago = (now - timedelta(days=7)).isoformat()
    two_weeks_ago = (now - timedelta(days=14)).isoformat()

    all_orders  = [d.to_dict() for d in db.collection("orders").stream()]
    all_reviews = [d.to_dict() for d in db.collection("reviews").stream()]

    # This week vs last week
    this_week  = [o for o in all_orders if o.get("created_at","") >= week_ago]
    last_week  = [o for o in all_orders if two_weeks_ago <= o.get("created_at","") < week_ago]

    def week_stats(orders):
        completed = [o for o in orders if o.get("status") == "completed"]
        revenue   = sum(o.get("amount_cents",0) for o in completed) / 100
        failed    = [o for o in orders if o.get("status") == "failed"]
        by_tpl    = {}
        by_occ    = {}
        by_plan   = {}
        for o in completed:
            t = o.get("template_name","?"); by_tpl[t] = by_tpl.get(t,0)+1
            c = o.get("occasion","?");      by_occ[c] = by_occ.get(c,0)+1
            p = o.get("plan_id","?");       by_plan[p] = by_plan.get(p,0)+1
        return {
            "orders":    len(orders),
            "completed": len(completed),
            "failed":    len(failed),
            "revenue":   round(revenue,2),
            "avg_order": round(revenue/len(completed),2) if completed else 0,
            "by_template": dict(sorted(by_tpl.items(), key=lambda x:-x[1])),
            "by_occasion": dict(sorted(by_occ.items(), key=lambda x:-x[1])),
            "by_plan":     dict(sorted(by_plan.items(), key=lambda x:-x[1])),
        }

    tw = week_stats(this_week)
    lw = week_stats(last_week)

    # Review data this week
    week_reviews = [r for r in all_reviews if r.get("created_at","") >= week_ago]
    def avg(lst): return round(sum(lst)/len(lst),1) if lst else 0
    review_ratings = {
        "likeness":   avg([r.get("ratings",{}).get("likeness",0)  for r in week_reviews if r.get("ratings",{}).get("likeness")]),
        "quality":    avg([r.get("ratings",{}).get("quality",0)   for r in week_reviews if r.get("ratings",{}).get("quality")]),
        "speed":      avg([r.get("ratings",{}).get("speed",0)     for r in week_reviews if r.get("ratings",{}).get("speed")]),
        "overall":    avg([r.get("ratings",{}).get("overall",0)   for r in week_reviews if r.get("ratings",{}).get("overall")]),
    }
    feedback_texts = [r.get("feedback","") for r in week_reviews if r.get("feedback","").strip()]

    # Template scores (all time)
    tpl_stats = {}
    for o in all_orders:
        if o.get("status") != "completed": continue
        t = o.get("template_name","?")
        if t not in tpl_stats: tpl_stats[t] = {"sales":0,"ratings":[]}
        tpl_stats[t]["sales"] += 1
    for r in all_reviews:
        t = r.get("template_name","")
        if t and t in tpl_stats:
            ov = r.get("ratings",{}).get("overall",0)
            if ov: tpl_stats[t]["ratings"].append(ov)
    for t in tpl_stats:
        s = tpl_stats[t]
        s["avg_rating"] = avg(s["ratings"]) if s["ratings"] else None
        s["score"] = round(s["sales"]*0.5 + (s["avg_rating"] or 3)*s["sales"]*0.5, 1)

    top_templates    = sorted(tpl_stats.items(), key=lambda x:-x[1]["score"])[:5]
    worst_templates  = [t for t in sorted(tpl_stats.items(), key=lambda x:x[1]["score"]) if t[1]["ratings"]][:3]

    return {
        "period":          f"{week_ago[:10]} → {now.date()}",
        "this_week":       tw,
        "last_week":       lw,
        "revenue_change":  round(tw["revenue"]-lw["revenue"],2),
        "orders_change":   tw["orders"]-lw["orders"],
        "review_count":    len(week_reviews),
        "review_ratings":  review_ratings,
        "feedback_texts":  feedback_texts,
        "top_templates":   top_templates,
        "worst_templates": worst_templates,
        "all_time_orders": len(all_orders),
        "all_time_revenue": round(sum(o.get("amount_cents",0) for o in all_orders if o.get("status")=="completed")/100,2),
    }


def build_report_email(data: dict, claude_analysis: str) -> str:
    tw  = data["this_week"]
    lw  = data["last_week"]
    rev_arrow = "📈" if data["revenue_change"] >= 0 else "📉"
    rev_color = "#2d7a4f" if data["revenue_change"] >= 0 else "#c0392b"

    top_tpl_rows = "".join([
        f'<tr><td style="padding:8px 12px;font-size:13px">{t}</td>'
        f'<td style="padding:8px 12px;font-size:13px;text-align:center">{s["sales"]}</td>'
        f'<td style="padding:8px 12px;font-size:13px;text-align:center">'
        f'{"⭐ "+str(s["avg_rating"]) if s["avg_rating"] else "—"}</td></tr>'
        for t,s in data["top_templates"]
    ])

    worst_tpl_rows = "".join([
        f'<tr><td style="padding:8px 12px;font-size:13px;color:#c0392b">{t}</td>'
        f'<td style="padding:8px 12px;font-size:13px;text-align:center">{s["sales"]}</td>'
        f'<td style="padding:8px 12px;font-size:13px;text-align:center;color:#c0392b">'
        f'{"⭐ "+str(s["avg_rating"]) if s["avg_rating"] else "—"}</td></tr>'
        for t,s in data["worst_templates"]
    ])

    feedback_html = "".join([
        f'<div style="background:#f8f0e3;border-left:3px solid #c8792a;padding:10px 14px;'
        f'margin:8px 0;font-size:13px;font-style:italic;color:#4a3e34">"{f}"</div>'
        for f in data["feedback_texts"][:5]
    ])

    r = data["review_ratings"]
    ratings_html = f"""
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin:16px 0">
      <div style="text-align:center"><div style="font-size:24px;font-weight:700;color:#c8792a">{r['likeness'] or '—'}</div><div style="font-size:11px;color:#8a7968">LIKENESS</div></div>
      <div style="text-align:center"><div style="font-size:24px;font-weight:700;color:#c8792a">{r['quality'] or '—'}</div><div style="font-size:11px;color:#8a7968">QUALITY</div></div>
      <div style="text-align:center"><div style="font-size:24px;font-weight:700;color:#c8792a">{r['speed'] or '—'}</div><div style="font-size:11px;color:#8a7968">SPEED</div></div>
      <div style="text-align:center"><div style="font-size:24px;font-weight:700;color:#c8792a">{r['overall'] or '—'}</div><div style="font-size:11px;color:#8a7968">OVERALL</div></div>
    </div>"""

    return f"""<!DOCTYPE html><html><body style="background:#f8f0e3;font-family:Georgia,serif;padding:32px 20px">
<div style="max-width:600px;margin:0 auto;background:#fffdf9;border:2px solid #c8792a;padding:40px;border-radius:8px">

  <div style="font-family:Georgia,serif;font-size:28px;font-weight:700;color:#0f0d0a;margin-bottom:4px">
    Caricature<span style="color:#c8792a">.</span>online
  </div>
  <div style="font-size:13px;color:#8a7968;margin-bottom:24px">Weekly Report · {data['period']}</div>
  <div style="border-top:2px solid #c8792a;margin-bottom:28px"></div>

  <!-- KPIs -->
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:28px">
    <div style="background:#f8f0e3;border-radius:8px;padding:16px;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#0f0d0a">€{tw['revenue']}</div>
      <div style="font-size:11px;color:#8a7968;margin-top:4px">REVENUE</div>
      <div style="font-size:12px;color:{rev_color};margin-top:4px">{rev_arrow} {'+' if data['revenue_change']>=0 else ''}€{data['revenue_change']} vs last week</div>
    </div>
    <div style="background:#f8f0e3;border-radius:8px;padding:16px;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#0f0d0a">{tw['completed']}</div>
      <div style="font-size:11px;color:#8a7968;margin-top:4px">ORDERS</div>
      <div style="font-size:12px;color:{rev_color};margin-top:4px">{'+' if data['orders_change']>=0 else ''}{data['orders_change']} vs last week</div>
    </div>
    <div style="background:#f8f0e3;border-radius:8px;padding:16px;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#0f0d0a">€{tw['avg_order']}</div>
      <div style="font-size:11px;color:#8a7968;margin-top:4px">AVG ORDER</div>
      <div style="font-size:12px;color:#8a7968;margin-top:4px">{data['review_count']} reviews</div>
    </div>
  </div>

  <!-- Claude Analysis -->
  <div style="background:#0f0d0a;border-radius:8px;padding:24px;margin-bottom:28px">
    <div style="font-size:11px;font-weight:700;letter-spacing:2px;color:#c8792a;margin-bottom:12px">🤖 AI ANALYSIS & ACTION ITEMS</div>
    <div style="font-size:14px;color:#f8f0e3;line-height:1.7;white-space:pre-line">{claude_analysis}</div>
  </div>

  <!-- Review Ratings -->
  <h3 style="font-size:14px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#8a7968;margin-bottom:8px">Customer Ratings ({data['review_count']} reviews)</h3>
  {ratings_html}

  <!-- Customer Feedback -->
  {f'<h3 style="font-size:14px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#8a7968;margin:20px 0 8px">What They Said</h3>{feedback_html}' if data['feedback_texts'] else ''}

  <!-- Templates -->
  <h3 style="font-size:14px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#8a7968;margin:24px 0 8px">🏆 Top Templates</h3>
  <table style="width:100%;border-collapse:collapse;border:1px solid #e8ddd0;border-radius:8px;overflow:hidden">
    <tr style="background:#0f0d0a"><th style="padding:8px 12px;font-size:11px;color:#f8f0e3;text-align:left">Template</th><th style="padding:8px 12px;font-size:11px;color:#f8f0e3">Sales</th><th style="padding:8px 12px;font-size:11px;color:#f8f0e3">Rating</th></tr>
    {top_tpl_rows}
  </table>

  {f'''<h3 style="font-size:14px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#c0392b;margin:20px 0 8px">⚠️ Underperforming Templates</h3>
  <table style="width:100%;border-collapse:collapse;border:1px solid #e8ddd0;border-radius:8px;overflow:hidden">
    <tr style="background:#0f0d0a"><th style="padding:8px 12px;font-size:11px;color:#f8f0e3;text-align:left">Template</th><th style="padding:8px 12px;font-size:11px;color:#f8f0e3">Sales</th><th style="padding:8px 12px;font-size:11px;color:#f8f0e3">Rating</th></tr>
    {worst_tpl_rows}
  </table>''' if data['worst_templates'] else ''}

  <!-- All time -->
  <div style="margin-top:28px;padding-top:20px;border-top:1px solid #e8ddd0;display:flex;justify-content:space-between;font-size:13px;color:#8a7968">
    <span>All-time orders: <strong style="color:#0f0d0a">{data['all_time_orders']}</strong></span>
    <span>All-time revenue: <strong style="color:#c8792a">€{data['all_time_revenue']}</strong></span>
  </div>
</div>
</body></html>"""


@app.route("/api/cron/weekly-report", methods=["POST"])
@require_admin
def cron_weekly_report():
    """Generate and email the weekly analytics report. Schedule: every Monday 08:00."""
    try:
        # 1. Pull all data from Firestore
        data = generate_weekly_report()
        tw   = data["this_week"]

        # 2. Ask Claude to analyse and give 3 action items
        prompt = f"""You are the analytics AI for Caricature.online, an AI caricature platform.
Analyse this week's data and give exactly 3 prioritised action items.

WEEK DATA:
- Revenue: €{tw['revenue']} ({'+' if data['revenue_change']>=0 else ''}€{data['revenue_change']} vs last week)
- Orders: {tw['completed']} completed, {tw['failed']} failed
- Top occasion: {list(tw['by_occasion'].keys())[0] if tw['by_occasion'] else 'N/A'}
- Top template: {list(tw['by_template'].keys())[0] if tw['by_template'] else 'N/A'}
- Review ratings: Likeness {data['review_ratings']['likeness']}/5, Quality {data['review_ratings']['quality']}/5, Overall {data['review_ratings']['overall']}/5
- Customer feedback samples: {'; '.join(data['feedback_texts'][:4]) or 'No feedback this week'}
- Underperforming templates: {[t for t,_ in data['worst_templates']] or 'None'}
- Total all-time revenue: €{data['all_time_revenue']}

Write a concise analysis (3-4 sentences) then list exactly 3 action items ranked by revenue impact.
Format:
ANALYSIS: [your analysis]

ACTION 1 (High impact): [specific action]
ACTION 2 (Medium impact): [specific action]  
ACTION 3 (Quick win): [specific action]

Be specific. Mention template names, prompt changes, or marketing tactics. Max 200 words."""

        msg = claude_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=400,
            messages=[{"role":"user","content":prompt}]
        )
        claude_analysis = msg.content[0].text.strip()

        # 3. Build and send email
        week_label = datetime.utcnow().strftime("Week of %b %d, %Y")
        revenue_arrow = "📈" if data["revenue_change"] >= 0 else "📉"
        html = build_report_email(data, claude_analysis)

        resend.Emails.send({
            "from": CFG["RESEND_FROM"],
            "to": [CFG["ADMIN_EMAIL"]],
            "subject": f"{revenue_arrow} Caricature.online — {week_label} · €{tw['revenue']} · {tw['completed']} orders",
            "html": html
        })
        print(f"[WeeklyReport] Sent report: €{tw['revenue']}, {tw['completed']} orders")
        return ok({"sent": True, "revenue": tw["revenue"], "orders": tw["completed"]})

    except Exception as e:
        print(f"[WeeklyReport] Error: {e}")
        return err(f"Report failed: {str(e)}", 500)


# ══════════════════════════════════════════════════════════════
#   GALLERY MANAGEMENT
# ══════════════════════════════════════════════════════════════

# One representative template per occasion for gallery previews
GALLERY_STYLES = {
    "wedding":   {"style_id": "wedding",   "template_id": "wed_happily",   "desc": "romantic wedding couple caricature, elegant, love theme"},
    "business":  {"style_id": "business",  "template_id": "biz_analytics", "desc": "professional business caricature, office setting, confident"},
    "birthday":  {"style_id": "birthday",  "template_id": "bday_superhero","desc": "fun birthday party caricature, celebration, colorful balloons"},
    "kids":      {"style_id": "kids",      "template_id": "kids_ahoy",     "desc": "cute kids pirate party caricature, playful, fun"},
    "superhero": {"style_id": "superhero", "template_id": "hero_superyou", "desc": "superhero caricature, cape, powerful pose, comic style"},
    "family":    {"style_id": "family",    "template_id": "fam_cooking",   "desc": "family cooking together caricature, warm, cheerful kitchen"},
    "sports":    {"style_id": "sports",    "template_id": "sport_nba",     "desc": "basketball player caricature, dynamic, court background"},
    "travel":    {"style_id": "travel",    "template_id": "sport_travel",  "desc": "world traveler caricature, globe, adventure, suitcase"},
    "music":     {"style_id": "music",     "template_id": "biz_guitar",    "desc": "rock guitarist caricature, electric guitar, stage lights"},
    "christmas": {"style_id": "christmas", "template_id": "bday_santa",    "desc": "Santa Claus caricature, jolly, festive, red suit, gifts"},
    "cooking":   {"style_id": "cooking",   "template_id": "biz_grand_chef","desc": "master chef caricature, tall white hat, kitchen, gourmet"},
    "motorcycle":{"style_id": "motorcycle","template_id": "sport_yamaha",  "desc": "motorcycle rider caricature, helmet, speed, open road"},
}

def _fal_run_with_timeout(model: str, arguments: dict, timeout: int = 90):
    """Run fal_client.run() with a hard timeout via threading."""
    import fal_client
    fal_client.api_key = CFG["FAL_KEY"]  # set in every thread context
    result_box = [None]
    error_box  = [None]
    def _call():
        try:
            import os as _os
            import fal_client as fc
            _os.environ["FAL_KEY"] = CFG["FAL_KEY"]
            fc.api_key = CFG["FAL_KEY"]
            result_box[0] = fc.run(model, arguments=arguments)
        except Exception as e:
            error_box[0] = e
    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        print(f"[Gallery] Timeout ({timeout}s) on {model}")
        return None
    if error_box[0]:
        raise error_box[0]
    return result_box[0]


def _generate_gallery_image(style: dict) -> str | None:
    """Generate one gallery preview image using FLUX (no photo needed)."""
    import fal_client
    fal_client.api_key = CFG["FAL_KEY"]
    prompt = (
        f"ANTONOS caricature art style, {style['desc']}, "
        f"exaggerated cartoon features, bold outlines, vivid colors, "
        f"professional digital illustration, white background, "
        f"caricature.photo artist style, high quality"
    )
    # Try LoRA first (90s hard timeout)
    if CFG["FAL_LORA_URL"]:
        try:
            print(f"[Gallery] Trying LoRA for {style['style_id']}...")
            result = _fal_run_with_timeout(
                "fal-ai/flux-lora",
                {"prompt": prompt, "loras": [{"path": CFG["FAL_LORA_URL"], "scale": 0.9}],
                 "image_size": "square_hd", "num_images": 1,
                 "num_inference_steps": 28, "guidance_scale": 7.5},
                timeout=90
            )
            if result and result.get("images"):
                return result["images"][0]["url"]
        except Exception as e:
            print(f"[Gallery] LoRA failed for {style['style_id']}: {e}")

    # Fallback: FLUX schnell (60s hard timeout)
    try:
        print(f"[Gallery] Trying FLUX schnell for {style['style_id']}...")
        result = _fal_run_with_timeout(
            "fal-ai/flux/schnell",
            {"prompt": prompt, "image_size": "square_hd",
             "num_images": 1, "num_inference_steps": 8},
            timeout=60
        )
        if result and result.get("images"):
            return result["images"][0]["url"]
    except Exception as e:
        print(f"[Gallery] FLUX fallback failed for {style['style_id']}: {e}")

    return None

def _save_gallery_image(style_id: str, image_url: str) -> str:
    """Download generated image and save to GCS, return public URL."""
    import urllib.request
    import io
    bucket = gcs_client.bucket(CFG["GCS_BUCKET"])
    blob_name = f"gallery/{style_id}.jpg"
    blob = bucket.blob(blob_name)
    with urllib.request.urlopen(image_url) as resp:
        image_bytes = resp.read()
    blob.upload_from_string(image_bytes, content_type="image/jpeg")
    # bucket has uniform IAM public access — no per-object ACL needed
    public_url = f"https://storage.googleapis.com/{CFG['GCS_BUCKET']}/{blob_name}"
    # Save to Firestore
    db.collection("gallery").document(style_id).set({
        "style_id":   style_id,
        "image_url":  public_url,
        "active":     True,
        "updated_at": datetime.utcnow().isoformat(),
    })
    return public_url

@app.route("/api/admin/gallery/regenerate-all", methods=["POST"])
@require_admin
def gallery_regenerate_all():
    """Regenerate all gallery preview images in background thread."""
    def _run():
        results = {}
        for style_id, style in GALLERY_STYLES.items():
            try:
                print(f"[Gallery] Generating {style_id}...")
                url = _generate_gallery_image(style)
                if url:
                    saved = _save_gallery_image(style_id, url)
                    results[style_id] = {"status": "ok", "url": saved}
                    print(f"[Gallery] ✅ {style_id} → {saved}")
                else:
                    results[style_id] = {"status": "failed"}
                    print(f"[Gallery] ❌ {style_id} — no image returned")
            except Exception as e:
                results[style_id] = {"status": "error", "error": str(e)}
                print(f"[Gallery] ❌ {style_id} error: {e}")
        print(f"[Gallery] Regeneration complete: {results}")

    threading.Thread(target=_run, daemon=True).start()
    return ok({
        "message": f"Regenerating {len(GALLERY_STYLES)} gallery images in background",
        "styles":  list(GALLERY_STYLES.keys()),
        "eta_seconds": len(GALLERY_STYLES) * 20,
    })

@app.route("/api/admin/gallery/regenerate/<style_id>", methods=["POST"])
@require_admin
def gallery_regenerate_one(style_id):
    """Regenerate a single gallery style."""
    style = GALLERY_STYLES.get(style_id)
    if not style:
        return err(f"Unknown style_id: {style_id}. Valid: {list(GALLERY_STYLES.keys())}")
    try:
        url = _generate_gallery_image(style)
        if not url:
            return err("Image generation failed")
        saved = _save_gallery_image(style_id, url)
        return ok({"style_id": style_id, "url": saved})
    except Exception as e:
        return err(f"Error: {e}")



@app.route("/api/admin/upload-template-base", methods=["POST"])
@require_admin
def upload_template_base():
    """Upload a real Antonos artwork as the base image for a template.

    This is how you register the actual Antonos caricature illustrations
    so the v2.6.0 pipeline can use them directly as the generation canvas.

    Usage:
        curl -X POST https://api.caricature.online/api/admin/upload-template-base \\
          -H "X-Admin-Secret: YOUR_SECRET" \\
          -F "template_id=hero_spartan" \\
          -F "file=@caricature-superhero-The-Spartan-antonos.jpg"

    The file is stored at GCS: template_bases/{template_id}.jpg
    All future orders using this template will use this image as the base.
    """
    template_id = (request.form.get("template_id") or "").strip()
    if not template_id:
        return err("template_id is required")
    if template_id not in TEMPLATES:
        return err(f"Unknown template_id: {template_id}. Valid IDs: {list(TEMPLATES.keys())[:10]}...")

    f = request.files.get("file")
    if not f:
        return err("file is required (-F 'file=@image.jpg')")

    allowed = {"image/jpeg", "image/png", "image/webp"}
    if f.content_type not in allowed:
        return err(f"Invalid file type: {f.content_type}. Use JPG, PNG or WEBP.")

    data = f.read()
    if len(data) > 15 * 1024 * 1024:
        return err("File too large. Max 15MB.")
    if not data:
        return err("File is empty.")

    ext = "jpg"
    if f.content_type == "image/png":
        ext = "png"
    elif f.content_type == "image/webp":
        ext = "webp"

    folder = CFG.get("TEMPLATE_IMAGES_FOLDER", "template_bases")
    filename = f"{template_id}.{ext}"
    try:
        url = upload_to_gcs(data, filename, f.content_type, folder=folder)
        template_info = TEMPLATES.get(template_id, {})
        db.collection("template_bases").document(template_id).set({
            "template_id": template_id,
            "template_name": template_info.get("name", template_id),
            "image_url": url,
            "uploaded_at": datetime.utcnow().isoformat(),
            "file_size": len(data),
        })
        print(f"[TemplateBase] Uploaded base for {template_id}: {url}")
        return ok({
            "template_id": template_id,
            "template_name": template_info.get("name", template_id),
            "image_url": url,
            "message": f"Template base image uploaded. Future orders for '{template_id}' will use this as the Antonos canvas.",
        })
    except Exception as e:
        return err(f"Upload failed: {e}", 500)


@app.route("/api/admin/template-bases", methods=["GET"])
@require_admin
def list_template_bases():
    """List all registered Antonos template base images."""
    try:
        docs = db.collection("template_bases").stream()
        bases = {d.id: d.to_dict() for d in docs}
        total = len(TEMPLATES)
        registered = len(bases)
        missing = [tid for tid in TEMPLATES if tid not in bases]
        return ok({
            "registered": registered,
            "total_templates": total,
            "coverage_pct": round(registered / total * 100, 1),
            "bases": bases,
            "missing_template_ids": missing[:20],
            "message": f"{registered}/{total} templates have Antonos base images registered.",
        })
    except Exception as e:
        return err(f"Error: {e}")


@app.route("/api/verify-same-person", methods=["POST"])
def verify_same_person():
    """Use Claude Vision to check if two photos are of the same person."""
    import base64
    data = request.json or {}
    url1 = data.get("photo_url1", "")
    url2 = data.get("photo_url2", "")
    if not url1 or not url2:
        return err("Two photo URLs required")
    try:
        imgs = []
        for url in [url1, url2]:
            img_data = requests.get(url, timeout=15).content
            imgs.append(base64.b64encode(img_data).decode())

        msg = claude_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=150,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imgs[0]}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imgs[1]}},
                {"type": "text", "text": (
                    "Are these two photos of THE EXACT SAME individual person?\n"
                    "Compare carefully: face shape, eye colour and shape, nose, "
                    "ears, hairline, skin tone, distinctive marks.\n"
                    "IMPORTANT: Babies and children of similar age often look alike "
                    "but are NOT the same person unless ALL specific features match.\n"
                    "Only say same_person=true if you are HIGHLY CONFIDENT.\n"
                    "When in doubt, say same_person=false.\n"
                    "Reply ONLY with JSON: "
                    '{"same_person": true or false, "confidence": "high" or "medium" or "low"}'
                )}
            ]}]
        )
        import json
        raw = msg.content[0].text.strip()
        if "{" in raw:
            raw = raw[raw.index("{"):raw.rindex("}")+1]
        result = json.loads(raw)
        print(f"[SamePerson] same={result.get('same_person')} conf={result.get('confidence')}")
        return ok(result)
    except Exception as e:
        print(f"[SamePerson] Error: {e}")
        return ok({"same_person": False, "confidence": "low"})


# ══════════════════════════════════════════════════════════════
#   ADMIN — DIRECT TEST GENERATION
#   Use this to test likeness/4K without Stripe, checkout, email, or order flow.
# ══════════════════════════════════════════════════════════════

@app.route("/api/admin/test-generate", methods=["POST"])
@require_admin
def admin_test_generate():
    """Generate a test caricature directly from uploaded photo(s).

    Multipart form fields:
      - file=@photo.jpg                         required primary reference
      - files=@photo2.jpg                       optional additional refs; repeatable
      - template_id=hero_spartan                optional, defaults to custom_free
      - notes="Test likeness"                  optional prompt guidance
      - persons=1                               optional
      - plan_id=premium                         optional
      - save_test=true                          optional Firestore log toggle

    This endpoint intentionally bypasses Stripe, order logging, and email delivery. v2.4 uses identity-first img2img before legacy face-swap fallback.
    It is protected by X-Admin-Secret and should never be exposed in frontend code.
    """
    started_at = datetime.utcnow()
    test_id = f"test_{str(uuid.uuid4()).replace('-', '')[:16]}"

    try:
        # ── 1. Read and validate multipart inputs ───────────────────────
        primary = request.files.get("file")
        extra_files = request.files.getlist("files") or []
        all_files = []
        if primary:
            all_files.append(primary)
        for f in extra_files:
            if f and f.filename:
                all_files.append(f)

        if not all_files:
            return err("No file provided. Use -F \"file=@C:\\path\\photo.jpg\"", 400)

        allowed = {"image/jpeg", "image/png", "image/webp"}
        if len(all_files) > 6:
            return err("Too many reference photos. Max 6 for admin test.", 400)

        template_id = (request.form.get("template_id") or "custom_free").strip()
        if template_id not in TEMPLATES:
            return err(f"Invalid template_id: {template_id}", 400)

        template = TEMPLATES.get(template_id, TEMPLATES["custom_free"])
        persons = (request.form.get("persons") or str(template.get("persons", 1)) or "1").strip()
        plan_id = (request.form.get("plan_id") or request.form.get("plan") or "premium").strip()
        if plan_id not in PLANS and plan_id != "free":
            plan_id = "premium"

        notes = (request.form.get("notes") or request.form.get("description") or "Admin direct generation test").strip()
        strict_mode = (request.form.get("strict", "true").lower() not in {"0", "false", "no", "off"})
        debug_mode = (request.form.get("debug", "true").lower() not in {"0", "false", "no", "off"})
        moderation = moderate_text(notes)
        if not moderation.get("allowed"):
            return err("This test request violates content guidelines.", 400)

        # ── 2. Upload references + quality check ────────────────────────
        photo_urls = []
        upload_refs = []
        qualities = []

        for idx, f in enumerate(all_files, start=1):
            if f.content_type not in allowed:
                return err(f"Invalid file type for reference {idx}. Use JPG, PNG or WEBP.", 400)
            data = f.read()
            if len(data) > 10 * 1024 * 1024:
                return err(f"Reference {idx} is too large. Max 10MB.", 400)
            if not data:
                return err(f"Reference {idx} is empty.", 400)

            ext = (f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "jpg")
            if ext not in {"jpg", "jpeg", "png", "webp"}:
                ext = "jpg"
            filename = f"{test_id}_ref{idx}.{ext}"

            quality = assess_photo_quality_bytes(data, f.content_type)
            photo_url = upload_to_gcs(data, filename, f.content_type, folder="admin_tests/uploads")
            photo_urls.append(photo_url)
            qualities.append(quality)
            upload_refs.append({
                "index": idx,
                "filename": f.filename,
                "content_type": f.content_type,
                "photo_url": photo_url,
                "quality": quality,
            })

        # Hard block only if the primary image is confidently unusable.
        primary_quality = qualities[0] if qualities else {}
        if primary_quality.get("hard_block") and not primary_quality.get("usable"):
            return ok({
                "test_id": test_id,
                "blocked": True,
                "reason": "primary_photo_unusable",
                "quality": primary_quality,
                "photo_urls": photo_urls,
                "message": "Primary photo was rejected by quality check. Try a clearer face photo.",
            }, 422)

        # ── 3. Build prompt + extract face description (v2.7.0) ────────────
        answers = {
            "occasion": template.get("occasion", "other"),
            "template_id": template_id,
            "template_name": template.get("name", template_id),
            "notes": notes,
            "description": notes,
        }
        prompt = analyze_photo_with_claude(
            photo_urls=photo_urls,
            persons=persons,
            template_name=template.get("name", template_id),
            answers=answers,
            person_photos=[{"person_index": 1, "photo_urls": photo_urls, "qualities": qualities}],
        )
        # v2.7.0: precise face description drives the face replacement in template img2img
        face_description = extract_face_description(photo_urls) if photo_urls else ""
        print(f"[AdminTest v2.7] {test_id} face_description: {face_description}")

        # ── 4. v2.7.0 Template img2img generation, QA, upscale ────────────
        final_ai_url = None
        qa = {}
        candidate_debug = []
        attempts = max(1, min(3, int(CFG.get("MAX_GENERATION_RETRIES", 2))))
        working_prompt = prompt

        for attempt in range(1, attempts + 1):
            print(f"[AdminTest v2.7] {test_id} template img2img attempt {attempt}/{attempts} strict={strict_mode}")
            candidate_url, generation_meta = generate_candidate_art(
                working_prompt, photo_urls, attempt=attempt, strict=strict_mode,
                template_id=template_id, face_description=face_description,
                template_name=template.get("name", template_id)
            )
            if not candidate_url:
                qa = {"deliverable": False, "quality_score": 1, "identity_score": 1, "reason": "candidate_generation_failed", "generation_meta": generation_meta}
                candidate_debug.append({"attempt": attempt, "candidate_url": None, "generation_meta": generation_meta, "qa": qa})
                print(f"[AdminTest v2.4] {test_id} candidate failed attempt {attempt}: {json.dumps(generation_meta, ensure_ascii=False)[:1200]}")
                continue

            qa = assess_generated_result(working_prompt, candidate_url, photo_urls[0] if photo_urls else None)
            qa["generation_meta"] = generation_meta
            candidate_debug.append({
                "attempt": attempt,
                "candidate_url": candidate_url,
                "base_url": generation_meta.get("base_url"),
                "generation_meta": generation_meta,
                "qa": qa
            })
            print(f"[AdminTest v2.4] {test_id} QA attempt {attempt}: {json.dumps(qa, ensure_ascii=False)[:1200]}")

            passes_strict = qa.get("deliverable", True) and qa.get("quality_score", 7) >= 6 and qa.get("identity_score", 7) >= 6
            if passes_strict:
                final_ai_url = candidate_url
                break
            # In admin debug / strict=false, do not stop at the first weak candidate.
            # Generate all variants and later return the best score for inspection.
            if not strict_mode:
                pass
            working_prompt += ", preserve exact uploaded identity, same age and facial structure, avoid generic adult beauty portrait, stronger hand-drawn caricature linework, ink outlines, cel shaded cartoon skin, not photorealistic, not selfie"

        if not final_ai_url and (not strict_mode) and candidate_debug:
            valid_candidates = [c for c in candidate_debug if c.get("candidate_url")]
            if valid_candidates:
                def _score(c):
                    q = c.get("qa", {}) or {}
                    return (int(q.get("identity_score", 0)) * 2) + int(q.get("quality_score", 0)) + (int(q.get("style_score", 0)) * 2)
                best = sorted(valid_candidates, key=_score, reverse=True)[0]
                final_ai_url = best.get("candidate_url")
                qa = best.get("qa", qa)

        if not final_ai_url:
            return ok({
                "test_id": test_id,
                "success": False,
                "error": "Admin test generation failed: no deliverable image returned",
                "strict_mode": strict_mode,
                "debug_mode": debug_mode,
                "template_id": template_id,
                "template_name": template.get("name", template_id),
                "photo_urls": photo_urls,
                "quality": primary_quality,
                "all_quality": qualities,
                "prompt": working_prompt[:1800],
                "debug_candidate_urls": candidate_debug,
                "message": "No candidate passed strict QA. Retry with -F strict=false to inspect candidates."
            }, 422)

        img_bytes, upscale_meta = upscale_to_4k(final_ai_url, test_id)
        result_filename = f"{test_id}_result_4k.jpg" if upscale_meta.get("upscaled") else f"{test_id}_result.jpg"
        result_url = upload_to_gcs(img_bytes, result_filename, "image/jpeg", folder="admin_tests/results")

        # ── 5. Optional log for QA history ──────────────────────────────
        duration_seconds = round((datetime.utcnow() - started_at).total_seconds(), 2)
        save_test = (request.form.get("save_test", "true").lower() not in {"0", "false", "no"})
        log_payload = {
            "test_id": test_id,
            "created_at": started_at.isoformat(),
            "duration_seconds": duration_seconds,
            "template_id": template_id,
            "template_name": template.get("name", template_id),
            "persons": persons,
            "plan_id": plan_id,
            "notes": notes,
            "photo_urls": photo_urls,
            "upload_refs": upload_refs,
            "raw_ai_url": final_ai_url,
            "result_url": result_url,
            "generation_prompt": working_prompt[:1800],
            "generation_qa": qa,
            "upscale_meta": upscale_meta,
            "pipeline_version": "2.8.0-inpainting",
            "strict_mode": strict_mode,
            "debug_mode": debug_mode,
            "debug_candidate_urls": candidate_debug if debug_mode else [],
        }
        if save_test:
            try:
                db.collection("admin_generation_tests").document(test_id).set(log_payload)
            except Exception as e:
                print(f"[AdminTest] Firestore log failed: {e}")

        return ok({
            "test_id": test_id,
            "template_id": template_id,
            "template_name": template.get("name", template_id),
            "photo_url": photo_urls[0] if photo_urls else None,
            "photo_urls": photo_urls,
            "result_url": result_url,
            "raw_ai_url": final_ai_url,
            "quality": primary_quality,
            "all_quality": qualities,
            "qa": qa,
            "upscale_meta": upscale_meta,
            "prompt": working_prompt[:1800],
            "duration_seconds": duration_seconds,
            "strict_mode": strict_mode,
            "debug_mode": debug_mode,
            "debug_candidate_urls": candidate_debug if debug_mode else [],
            "message": "Admin test generated successfully. Open result_url to inspect likeness and 4K output.",
        })

    except Exception as e:
        print(f"[AdminTest] ERROR {test_id}: {e}")
        try:
            db.collection("admin_generation_tests").document(test_id).set({
                "test_id": test_id,
                "created_at": started_at.isoformat(),
                "status": "failed",
                "error": str(e),
                "pipeline_version": "2.8.0-inpainting",
            }, merge=True)
        except Exception:
            pass
        return err(f"Admin test generation failed: {str(e)}", 500)


@app.route("/health", methods=["GET"])
def health():
    return ok({
        "status":    "healthy",
        "version":   "2.8.0-inpainting",
        "timestamp": datetime.utcnow().isoformat(),
        "lora_ready": bool(CFG["FAL_LORA_URL"]),
    })

@app.route("/", methods=["GET"])
def root():
    return ok({"service": "Caricature API", "version": "2.8.0-inpainting", "docs": "/health"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
