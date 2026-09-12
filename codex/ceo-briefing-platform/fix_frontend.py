import re

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\frontend\admin-console-runtime-v2.js", "r", encoding="utf-8") as f:
    content = f.read()

# Replace category array
content = content.replace(
    'const CATEGORY_ORDER = ["kai", "government", "hanwha", "lig", "reference"];',
    'const CATEGORY_ORDER = ["kai", "government", "space", "hanwha", "lig", "reference"];'
)

# Replace labels
content = content.replace(
    '''const CATEGORY_LABELS = {
  kai: "KAI 관련",
  government: "항공/방산",
  hanwha: "한화에어로",
  lig: "LIG D&A",
  reference: "참고 기사",
};''',
    '''const CATEGORY_LABELS = {
  kai: "KAI 관련",
  government: "항공/방산",
  space: "우주",
  hanwha: "한화에어로",
  lig: "LIG D&A",
  reference: "참고 기사",
};'''
)

telegram_html = """      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Telegram API</p>
            <h3>Breaking News Alert Settings</h3>
          </div>
        </div>
        <form id="telegramForm" class="stack">
          <label class="field"><span>Bot Token</span><input id="telegramBotToken" type="password" placeholder="${settings.has_telegram_bot_token ? "saved" : "123456:ABC-DEF..."}"></label>
          <label class="field"><span>Chat ID</span><input id="telegramChatId" placeholder="${settings.has_telegram_chat_id ? settings.telegram_chat_id : "-1001234567890"}"></label>
          <button class="button" type="submit">Save Telegram Settings</button>
        </form>
      </article>
"""

# Find where to insert Telegram Settings HTML
content = content.replace(
    '''        <form id="naverNewsForm" class="stack">
          <label class="field"><span>Client ID</span><input id="naverClientId" placeholder="${naverSettings.has_client_id ? naverSettings.client_id_masked || "saved" : "NAVER CLIENT ID"}"></label>
          <label class="field"><span>Client Secret</span><input id="naverClientSecret" type="password" placeholder="${naverSettings.has_client_secret ? naverSettings.client_secret_masked || "saved" : "NAVER CLIENT SECRET"}"></label>
          <button class="button" type="submit">Save Naver Settings</button>
        </form>
      </article>''',
    '''        <form id="naverNewsForm" class="stack">
          <label class="field"><span>Client ID</span><input id="naverClientId" placeholder="${naverSettings.has_client_id ? naverSettings.client_id_masked || "saved" : "NAVER CLIENT ID"}"></label>
          <label class="field"><span>Client Secret</span><input id="naverClientSecret" type="password" placeholder="${naverSettings.has_client_secret ? naverSettings.client_secret_masked || "saved" : "NAVER CLIENT SECRET"}"></label>
          <button class="button" type="submit">Save Naver Settings</button>
        </form>
      </article>
''' + telegram_html
)

telegram_js = """  document.querySelector("#telegramForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await api(`/telegram-settings?role=${state.role}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bot_token: document.querySelector("#telegramBotToken").value.trim(),
        chat_id: document.querySelector("#telegramChatId").value.trim(),
      }),
    });
    setNotice("Telegram settings saved.");
    await renderActivePage();
  });
"""

content = content.replace(
    '''    setNotice("Naver News settings saved.");
    await renderActivePage();
  });
}''',
    '''    setNotice("Naver News settings saved.");
    await renderActivePage();
  });
''' + telegram_js + '}'
)

content = content.replace(
    '''  const [settings, naverSettings] = await Promise.all([''',
    '''  const [settings, naverSettings, telegramSettingsRes] = await Promise.all(['''
)
content = content.replace(
    '''      client_secret_masked: "",
    }),
  ]);''',
    '''      client_secret_masked: "",
    }),
    apiOptional(`/telegram-settings?role=${state.role}`, { has_telegram_bot_token: false, has_telegram_chat_id: false }),
  ]);
  const telegramSettings = telegramSettingsRes || {};
  settings.has_telegram_bot_token = telegramSettings.has_telegram_bot_token;
  settings.telegram_chat_id = telegramSettings.telegram_chat_id;
  settings.has_telegram_chat_id = telegramSettings.has_telegram_chat_id;
'''
)

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\frontend\admin-console-runtime-v2.js", "w", encoding="utf-8") as f:
    f.write(content)
print("frontend done")
