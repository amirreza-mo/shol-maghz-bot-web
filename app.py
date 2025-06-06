import os
import json
import logging
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai

# --- پیکربندی لاگ‌گیری ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- پیکربندی Flask ---
app = Flask(__name__)

# --- توکن و کلید API ---
# API Key رو از متغیرهای محیطی (Environment Variables) می‌خوانیم.
# این روش برای دیپلوی روی سرورها (مثل Vercel) امن‌تر و استانداردتره.
GEMINI_API_KEY = os.getenv("AIzaSyCVBV8tbcgis0cOXVs7ekzMV2XvfnxcerE")

if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY environment variable is not set. Please set it in Vercel or locally for development.")
    # اگر در محیط توسعه محلی اجرا می‌کنید، می‌توانید اینجا کلید را به صورت موقت قرار دهید:
    # GEMINI_API_KEY = "YOUR_GEMINI_API_KEY_HERE"
    # اما قبل از دیپلوی روی سرور، حتماً این خط را پاک کرده یا کامنت کنید و از os.getenv استفاده کنید.
    raise ValueError("GEMINI_API_KEY environment variable is not set.")


# --- پیکربندی Gemini ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    # از مدل 'gemini-1.5-flash' استفاده می‌کنیم که سبک‌تر و سریع‌تره
    gemini_model = genai.GenerativeModel('gemini-1.5-flash')
    logger.info("Gemini API configured successfully.")
except Exception as e:
    logger.error(f"Error configuring Gemini API: {e}")
    # اگر پیکربندی API ناموفق بود، برنامه را متوقف می‌کنیم.
    exit(1)

# --- فایل برای ذخیره تاریخچه گفتگو (حافظه دائمی) ---
CONVERSATION_HISTORY_FILE = 'conversation_history.json'

def load_conversation_history():
    """تاریخچه گفتگوها را از فایل بارگذاری می‌کند."""
    if os.path.exists(CONVERSATION_HISTORY_FILE):
        with open(CONVERSATION_HISTORY_FILE, 'r', encoding='utf-8') as f:
            try:
                loaded_data = json.load(f)
                # اطمینان حاصل می‌کنیم که تاریخچه در فرمت صحیح {"role": "...", "parts": [{"text": "..."}]} باشد.
                # اگر فرمت قدیمی بود، آن را به فرمت جدید تبدیل می‌کنیم.
                # این برای سازگاری با تاریخچه‌هایی که قبلاً ذخیره شده‌اند، مفید است.
                for chat_id in loaded_data:
                    new_history_list = []
                    for msg in loaded_data[chat_id]:
                        if "parts" not in msg and "text" in msg:
                            new_history_list.append({"role": msg["role"], "parts": [{"text": msg["text"]}]})
                        elif "parts" in msg:
                            # اگر از قبل parts داشت، مطمئن می‌شویم که فرمت داخلیش درست است
                            fixed_parts = []
                            for part_item in msg["parts"]:
                                if "text" in part_item:
                                    fixed_parts.append({"text": part_item["text"]})
                                # در این نسخه، ما ابزار یا دیتای دیگری (مثل تصاویر) را مدیریت نمی‌کنیم.
                            new_history_list.append({"role": msg["role"], "parts": fixed_parts})
                    loaded_data[chat_id] = new_history_list
                return loaded_data
            except json.JSONDecodeError:
                logger.warning("Error decoding JSON from conversation history file. Starting with empty history.")
                return {}
    return {}

def save_conversation_history(history):
    """تاریخچه گفتگوها را در فایل ذخیره می‌کند."""
    savable_history = {}
    for chat_id, chat_obj in history.items():
        current_chat_history = []
        for msg in chat_obj.history: # msg در اینجا یک Content object است
            parts_list = []
            for part in msg.parts:
                if hasattr(part, 'text'):
                    parts_list.append({"text": part.text})
                # در این نسخه، دیگر نیازی به مدیریت function_call یا function_response نیست.
            current_chat_history.append({"role": msg.role, "parts": parts_list})
        savable_history[chat_id] = current_chat_history

    with open(CONVERSATION_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(savable_history, f, ensure_ascii=False, indent=4)


# بارگذاری تاریخچه گفتگوها هنگام راه‌اندازی برنامه
# نکته: اگر فایل conversation_history.json از قبل وجود داشته باشد و فرمت آن قدیمی باشد،
# تابع load_conversation_history سعی می‌کند آن را به فرمت جدید تبدیل کند.
# اما برای جلوگیری از مشکلات احتمالی، بهتر است در صورت بروز خطا، این فایل را دستی حذف کنید.
loaded_raw_history = load_conversation_history()
active_conversations = {} # این دیکشنری، اشیاء Chat فعال را نگهداری می‌کند


# --- روت اصلی سایت (صفحه چت) ---
@app.route('/')
def index():
    """نمایش صفحه اصلی چت."""
    return render_template('index.html')

# --- API برای ارسال پیام به ربات ---
@app.route('/chat', methods=['POST'])
def chat():
    """دریافت پیام از کاربر و ارسال به Gemini."""
    user_message = request.json.get('message')
    chat_id = request.json.get('user_id', 'web_user_1')

    logger.info(f"Received message from {chat_id}: '{user_message}'")

    if not user_message:
        return jsonify({"response": "لطفاً یک پیام معتبر ارسال کنید."}), 400

    # اگر برای این user_id، چت فعال نداریم، یک چت جدید بسازید.
    if chat_id not in active_conversations:
        initial_history_for_user = loaded_raw_history.get(chat_id, [])
        # در این نسخه، آرگومان tools دیگر به start_chat پاس داده نمی‌شود.
        active_conversations[chat_id] = gemini_model.start_chat(history=initial_history_for_user)
        logger.info(f"Started new chat session for {chat_id} with existing history loaded.")

    try:
        # ارسال پیام کاربر به مدل Gemini
        response = active_conversations[chat_id].send_message(user_message)

        # در این نسخه، دیگر نیازی به حلقه برای مدیریت فراخوانی ابزار نیست.
        gemini_reply = response.text

        # ذخیره تاریخچه به‌روز شده در فایل
        save_conversation_history(active_conversations)

        logger.info(f"Replied to {chat_id} with Gemini response.")
        return jsonify({"response": gemini_reply})

    except Exception as e:
        logger.error(f"Error generating content with Gemini for {chat_id}: {e}")
        error_message = "متاسفانه در حال حاضر نمی‌توانم به سوال شما پاسخ دهم. لطفاً بعداً امتحان کنید."
        # مدیریت خطاهای رایج API برای ارائه پیام‌های مفیدتر به کاربر
        if "Blocked by LDR" in str(e) or "403" in str(e) or "429" in str(e) or "geographic restrictions" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            error_message = "احتمالاً به دلیل محدودیت‌های جغرافیایی، مصرف بیش از حد (Rate Limit) یا دسترسی، ارتباط با Gemini API مسدود شده است."
        elif "Invalid API key" in str(e):
            error_message = "کلید API جیمینی شما نامعتبر است. لطفاً آن را دوباره بررسی کنید."
        elif "connection" in str(e).lower() or "proxy" in str(e).lower():
            error_message = "خطا در اتصال به سرورهای Gemini. لطفاً اینترنت/فیلترشکن خود را بررسی کنید."
        elif "content has been blocked" in str(e).lower():
            error_message = "پاسخ تولید شده توسط Gemini به دلیل سیاست‌های محتوایی بلاک شد. (لطفاً محتوای دیگری را امتحان کنید)"
            logger.warning(f"Gemini response blocked for {chat_id}.")
            # در صورت بلاک شدن محتوا، تاریخچه چت فعلی را پاک می‌کنیم تا از تکرار مشکل جلوگیری شود.
            if chat_id in active_conversations:
                del active_conversations[chat_id]
                logger.info(f"Chat session cleared for {chat_id} due to content blocking.")
        elif "No such file or directory" in str(e):
            error_message = "فایل تاریخچه گفتگو یافت نشد. لطفاً مطمئن شوید که برنامه به درستی اجرا می‌شود."

        return jsonify({"response": error_message}), 500

if __name__ == '__main__':
    # در محیط توسعه، debug=True کمک‌کننده است.
    # در محیط پروداکشن (مثل Vercel)، debug باید False باشد یا کلاً حذف شود.
    app.run(debug=True, port=5000)