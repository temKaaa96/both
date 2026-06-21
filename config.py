import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8926744054"))

# VK API токен для поиска ВКонтакте
# Получи на https://vk.com/dev → Создать приложение → Standalone
# Затем получи токен через https://oauth.vk.com/authorize?client_id=ВАШ_ID&display=page&scope=friends,offline&response_type=token&v=5.131
VK_TOKEN = os.environ.get("VK_TOKEN", "YOUR_VK_TOKEN")
