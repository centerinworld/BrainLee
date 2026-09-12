with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\main.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(
    '''class OpenAISettingsPayload(BaseModel):
    api_key: str = ""
    model: str = Field(min_length=2)
    classification_model: str = "gpt-5.4-mini"''',
    '''class OpenAISettingsPayload(BaseModel):
    api_key: str = ""
    model: str = Field(min_length=2)
    classification_model: str = "gpt-5.4-mini"

class TelegramSettingsPayload(BaseModel):
    bot_token: str = ""
    chat_id: str = ""'''
)

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\main.py", "w", encoding="utf-8") as f:
    f.write(content)
print("done")
