import re

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\main.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add TelegramSettingsPayload
content = content.replace(
    '''class OpenAISettingsPayload(BaseModel):
    api_key: str
    model: str
    classification_model: str''',
    '''class OpenAISettingsPayload(BaseModel):
    api_key: str
    model: str
    classification_model: str

class TelegramSettingsPayload(BaseModel):
    bot_token: str
    chat_id: str'''
)

telegram_endpoints = """
@app.get("/telegram-settings")
def get_telegram_settings(role: Role) -> Dict[str, object]:
    require_admin(role)
    with db_connect() as conn:
        bot_token = get_setting(conn, "telegram_bot_token")
        chat_id = get_setting(conn, "telegram_chat_id")
    return {
        "has_telegram_bot_token": bool(bot_token),
        "telegram_chat_id": chat_id or "",
        "has_telegram_chat_id": bool(chat_id),
    }

@app.put("/telegram-settings")
def update_telegram_settings(payload: TelegramSettingsPayload, role: Role) -> Dict[str, object]:
    require_admin(role)
    with db_connect() as conn:
        update_setting(conn, "telegram_bot_token", payload.bot_token)
        update_setting(conn, "telegram_chat_id", payload.chat_id)
    return {"status": "ok"}
"""

content = content.replace(
    '''@app.get("/openai-settings")''',
    telegram_endpoints + '\n@app.get("/openai-settings")'
)

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\main.py", "w", encoding="utf-8") as f:
    f.write(content)
print("main done")
