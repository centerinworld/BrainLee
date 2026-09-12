with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\main.py", "r", encoding="utf-8") as f:
    content = f.read()

if "class TelegramSettingsPayload" not in content:
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
    with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\main.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Fixed payload")
else:
    print("Payload already exists")
