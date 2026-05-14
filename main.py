# ══════════════════════════════════════════════════════════════
#   CARICATURE.ONLINE — Backend v2.1
#   AI Caricature in the style of Antonos (caricature.photo)
#   Stack: Flask · Firestore · GCS · fal.ai LoRA · Stripe · Resend
# ══════════════════════════════════════════════════════════════

import os, uuid, threading, time, requests
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
#   AI PIPELINE — LoRA Generation
# ══════════════════════════════════════════════════════════════

def analyze_photo_with_claude(photo_url: str, persons: str, template_name: str, answers: dict) -> str:
    """Use Claude Vision to create a detailed prompt for the LoRA model."""
    try:
        # Download the photo
        img_data = requests.get(photo_url, timeout=15).content
        import base64
        img_b64 = base64.b64encode(img_data).decode()

        occasion_desc = answers.get("occasion", "")
        notes = answers.get("notes", "")

        msg = claude_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
                    },
                    {
                        "type": "text",
                        "text": f"""Analyze this photo and write a caricature generation prompt.
Template: {template_name}
Occasion: {occasion_desc}
Notes: {notes}

Describe the person's key features (face shape, hair, distinctive features) in 2-3 sentences.
Then write a final prompt in this format:
PROMPT: ANTONOS caricature style, [person description], {template_name} theme, exaggerated features, bold lines, colorful, professional caricature art, caricature.photo style

Write only the PROMPT line, nothing else."""
                    }
                ]
            }]
        )
        text = msg.content[0].text.strip()
        if "PROMPT:" in text:
            return text.split("PROMPT:")[-1].strip()
        return text
    except Exception as e:
        print(f"[Claude] Error: {e}")
        template = TEMPLATES.get(answers.get("template_id", ""), {})
        return f"ANTONOS caricature style, {template.get('desc','portrait')}, {template_name} theme, exaggerated features, bold lines, colorful, professional caricature art"


def generate_with_lora(prompt: str, photo_urls: list) -> str | None:
    """Generate caricature using fal.ai LoRA or face-swap."""
    import fal_client
    fal_client.api_key = CFG["FAL_KEY"]

    # Method 1: LoRA trained on Antonos style
    if CFG["FAL_LORA_URL"]:
        try:
            print(f"[AI] Generating with LoRA: {CFG['FAL_LORA_URL'][:50]}...")
            result = fal_client.run(
                "fal-ai/flux-lora",
                arguments={
                    "prompt": prompt,
                    "loras": [{"path": CFG["FAL_LORA_URL"], "scale": 1.0}],
                    "image_size": "portrait_4_3",
                    "num_images": 1,
                    "num_inference_steps": 28,
                    "guidance_scale": 7.5,
                }
            )
            if result and result.get("images"):
                return result["images"][0]["url"]
        except Exception as e:
            print(f"[AI] LoRA failed: {e}")

    # Method 2: Face swap (if we have reference photo + LoRA)
    if photo_urls and CFG["FAL_LORA_URL"]:
        try:
            print(f"[AI] Trying face swap...")
            result = fal_client.run(
                "fal-ai/face-swap",
                arguments={
                    "base_image_url": photo_urls[0],
                    "face_image_url": photo_urls[0],
                    "prompt": prompt,
                }
            )
            if result and result.get("image"):
                return result["image"]["url"]
        except Exception as e:
            print(f"[AI] Face swap failed: {e}")

    # Method 3: Standard FLUX without LoRA
    try:
        print(f"[AI] Falling back to standard FLUX...")
        result = fal_client.run(
            "fal-ai/flux/schnell",
            arguments={
                "prompt": prompt,
                "image_size": "portrait_4_3",
                "num_images": 1,
                "num_inference_steps": 8,
            }
        )
        if result and result.get("images"):
            return result["images"][0]["url"]
    except Exception as e:
        print(f"[AI] FLUX fallback failed: {e}")

    return None


def run_generation_pipeline(order_id: str):
    """Main AI pipeline — runs in background thread after payment."""
    try:
        order = get_order(order_id)
        if not order:
            raise Exception("Order not found")

        update_order_status(order_id, "generating")
        print(f"[Pipeline] Starting generation for {order_id}")

        template_id   = order["template_id"]
        template      = TEMPLATES.get(template_id, {})
        photo_urls    = order.get("photo_urls", [])
        answers       = order.get("answers", {})
        email         = order["email"]
        name          = order["name"]
        plan_id       = order["plan_id"]

        # 1. Claude Vision analysis
        print(f"[Pipeline] Claude analyzing photo...")
        prompt = analyze_photo_with_claude(
            photo_urls[0] if photo_urls else "",
            order.get("persons", "1"),
            template.get("name", template_id),
            {**answers, "template_id": template_id}
        )
        print(f"[Pipeline] Prompt: {prompt[:100]}...")

        # 2. Generate with LoRA
        print(f"[Pipeline] Generating caricature...")
        raw_url = generate_with_lora(prompt, photo_urls)

        if not raw_url:
            raise Exception("All AI generation methods failed")

        # 3. Save to GCS
        img_bytes = requests.get(raw_url, timeout=30).content
        filename  = f"result_{order_id}.jpg"
        result_url = upload_to_gcs(img_bytes, filename, "image/jpeg", folder="results")
        print(f"[Pipeline] Saved to GCS: {result_url}")

        # 4. Send email
        send_result_email(email, name, [result_url], order_id, template.get("name", ""), plan_id)

        # 5. Complete
        update_order_status(order_id, "completed", {
            "result_urls": [result_url],
            "completed_at": datetime.utcnow().isoformat(),
            "review_email_sent": False,
        })
        notify_admin(f"✅ Order {order_id} completed | {template.get('name','')} | {email}")

    except Exception as e:
        print(f"[Pipeline] ERROR for {order_id}: {e}")
        update_order_status(order_id, "failed", {"error": str(e)})
        notify_admin(f"❌ Order {order_id} FAILED: {e}")
        # Offer manual caricature.photo as fallback
        try:
            send_fallback_email(order.get("email",""), order.get("name",""), order_id)
        except:
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
    })


# ══════════════════════════════════════════════════════════════
#   ROUTES — UPLOAD
# ══════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════
#   PHOTO QUALITY ASSESSMENT
# ══════════════════════════════════════════════════════════════

def assess_photo_quality(photo_url: str) -> dict:
    """Claude Vision — is there a human face? Returns quality dict."""
    import base64, json as _json
    pessimistic = {
        "score": 1, "face_size": "none", "lighting": "unknown",
        "sharpness": "unknown", "usable": False,
        "visible_features": [], "warnings": ["no_face"],
        "recovery_strategy": "template_only"
    }
    no_face_resp = (
        '{"score":1,"face_size":"none","lighting":"unknown","sharpness":"unknown",'
        '"usable":false,"visible_features":[],"warnings":["no_face"],'
        '"recovery_strategy":"template_only"}'
    )
    multi_face_resp = (
        '{"score":1,"face_size":"multiple","lighting":"unknown","sharpness":"unknown",'
        '"usable":false,"visible_features":[],"warnings":["multiple_faces"],'
        '"recovery_strategy":"template_only"}'
    )
    prompt = (
        "You are assessing a photo for caricature art generation.\n\n"
        "TASK: Determine if this photo contains a human face as the PRIMARY subject.\n\n"
        "CASE A — Clearly NOT a person photo (QR codes, diagrams, charts, text, "
        "screenshots, objects, landscapes with no people, abstract images).\n"
        "Return: " + no_face_resp + "\n\n"
        "CASE B — Multiple CLEAR, IN-FOCUS faces of similar prominence in the "
        "FOREGROUND (e.g. a group selfie, two people posing together).\n"
        "IMPORTANT: Blurred background figures, partially visible people at edges, "
        "or small distant people do NOT count. Only flag if there are clearly TWO OR MORE "
        "people who are ALL in-focus main subjects.\n"
        "Return: " + multi_face_resp + "\n\n"
        "CASE C — One human face is the main subject, OR the image is clearly a portrait "
        "of one person even if slightly blurry or with background activity.\n"
        "Babies, toddlers, children, and elderly people ARE valid subjects.\n"
        "If you can see a human face at all, this is CASE C.\n"
        "Assess quality: Score 1-10. face_size: large|medium|small.\n"
        "lighting: good|backlit|dark|harsh. sharpness: sharp|slightly_blurred|blurred.\n"
        "usable: true if a face is visible (even if not perfect quality).\n"
        "warnings: array from [face_too_small,backlit,blurred,low_resolution,obstructed].\n"
        "recovery_strategy: full_analysis if score>=6, partial_analysis if 3-5.\n"
        "IMPORTANT: When in doubt, choose CASE C. Only use CASE A/B when you are CERTAIN.\n"
        "Return ONLY valid JSON."
    )
    try:
        img_data = requests.get(photo_url, timeout=15).content
        img_b64  = base64.b64encode(img_data).decode()
        msg = claude_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text",  "text": prompt}
            ]}]
        )
        raw = msg.content[0].text.strip()
        if "{" in raw:
            raw = raw[raw.index("{"):raw.rindex("}")+1]
        result = {**pessimistic, **_json.loads(raw)}
        # Safety net: face_size=none → always no_face
        if result.get("face_size") in ("none", "multiple"):
            if result.get("face_size") == "none" and "no_face" not in result.get("warnings", []):
                result.setdefault("warnings", []).append("no_face")
            if result.get("face_size") == "multiple" and "multiple_faces" not in result.get("warnings", []):
                result.setdefault("warnings", []).append("multiple_faces")
            result["usable"] = False
        print(f"[Quality] score={result.get('score')} face={result.get('face_size')} warns={result.get('warnings')}")
        return result
    except Exception as e:
        print(f"[Quality] Error: {e} — pessimistic defaults")
        return pessimistic


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
        photo_url = upload_to_gcs(data, filename, file.content_type, folder="uploads")
        quality   = assess_photo_quality(photo_url)
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
    body        = request.get_json()
    template_id = body.get("template_id")
    upload_ids  = body.get("upload_ids", [])
    persons     = body.get("persons", "1")
    plan_id     = body.get("plan", "standard")
    answers     = body.get("answers", {})
    notes       = body.get("notes", "")
    email       = body.get("email", "").strip().lower()
    name        = body.get("name", "").strip()

    if not template_id or template_id not in TEMPLATES:
        return err("Invalid template")
    if not upload_ids:
        return err("Please upload at least one photo")
    if not email or "@" not in email:
        return err("Invalid email")
    if plan_id not in PLANS:
        plan_id = "standard"

    # Get photo URLs
    photo_urls = []
    for uid in upload_ids[:5]:
        doc = db.collection("uploads").document(uid).get()
        if doc.exists:
            photo_urls.append(doc.to_dict()["photo_url"])
    if not photo_urls:
        return err("Photos not found. Please re-upload.")

    amount_cents = calc_price(persons, plan_id)
    order_id     = f"ord_{str(uuid.uuid4()).replace('-','')[:16]}"
    template     = TEMPLATES[template_id]

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="eur",
            automatic_payment_methods={"enabled": True},
            receipt_email=email,
            metadata={
                "order_id":   order_id,
                "template_id": template_id,
                "plan_id":    plan_id,
                "persons":    str(persons),
            },
            description=f"Caricature — {template['name']} ({plan_id})"
        )
    except stripe.error.StripeError as e:
        return err(f"Payment setup failed: {e.user_message}", 402)

    log_order(order_id, {
        "order_id":       order_id,
        "stripe_intent":  intent.id,
        "template_id":    template_id,
        "template_name":  template["name"],
        "occasion":       template["occasion"],
        "persons":        persons,
        "plan_id":        plan_id,
        "amount_cents":   amount_cents,
        "photo_urls":     photo_urls,
        "upload_ids":     upload_ids,
        "answers":        answers,
        "notes":          notes,
        "email":          email,
        "name":           name,
        "status":         "pending",
        "created_at":     datetime.utcnow().isoformat(),
    })

    return ok({
        "clientSecret":    intent.client_secret,
        "order_id":        order_id,
        "publishable_key": CFG["STRIPE_PUB"],
        "amount_cents":    amount_cents,
        "amount_display":  f"€{amount_cents/100:.2f}",
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
    template_id = body.get("template_id")
    upload_ids  = body.get("upload_ids", [])
    persons     = body.get("persons", "1")
    answers     = body.get("answers", {})
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

    template = TEMPLATES[template_id]
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


@app.route("/health", methods=["GET"])
def health():
    return ok({
        "status":    "healthy",
        "version":   "2.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "lora_ready": bool(CFG["FAL_LORA_URL"]),
    })

@app.route("/", methods=["GET"])
def root():
    return ok({"service": "Caricature API", "version": "2.0.0", "docs": "/health"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
