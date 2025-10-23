import os
import logging
import requests
import json
import asyncio
import sys
import random
import time
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from bs4 import BeautifulSoup
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse
import uvicorn

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Безопасное получение токена с проверкой
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    logger.error("❌ ОШИБКА: BOT_TOKEN не установлен в переменных окружения!")
    sys.exit(1)

# Настройки для Render
PORT = int(os.environ.get("PORT", 8000))
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL", "") + "/webhook"

# Файл для хранения цен
PRICES_FILE = "prices.json"

# Создаем приложение Telegram
try:
    application = Application.builder().token(TOKEN).build()
    logger.info("✅ Приложение Telegram успешно создано")
except Exception as e:
    logger.error(f"❌ Ошибка при создании приложения: {e}")
    sys.exit(1)

class PriceMonitor:
    """Мониторинг цен на обогревательные приборы с etm.ru"""
    
    def __init__(self):
        self.session = requests.Session()
        self.setup_headers()
        self.base_url = "https://www.etm.ru"
        self.category_url = "https://www.etm.ru/catalog/6040_obogrevatelnye_pribory"
    
    def setup_headers(self):
        """Настройка реалистичных заголовков для обхода защиты"""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
            'DNT': '1',
        })
    
    def get_with_retry(self, url, max_retries=3):
        """Выполняет запрос с повторными попытками и случайными задержками"""
        for attempt in range(max_retries):
            try:
                # Случайная задержка между запросами
                if attempt > 0:
                    delay = random.uniform(2, 5)
                    logger.info(f"Повторная попытка {attempt + 1}/{max_retries} через {delay:.1f} сек...")
                    time.sleep(delay)
                
                # Слегка меняем User-Agent для каждой попытки
                self.session.headers['User-Agent'] = self.rotate_user_agent()
                
                response = self.session.get(url, timeout=15)
                
                if response.status_code == 200:
                    return response
                elif response.status_code == 444:
                    logger.warning(f"Попытка {attempt + 1}: Получена ошибка 444 (блокировка)")
                    continue
                else:
                    logger.warning(f"Попытка {attempt + 1}: Статус {response.status_code}")
                    continue
                    
            except requests.exceptions.Timeout:
                logger.warning(f"Попытка {attempt + 1}: Таймаут")
                continue
            except requests.exceptions.RequestException as e:
                logger.warning(f"Попытка {attempt + 1}: Ошибка сети - {e}")
                continue
        
        return None
    
    def rotate_user_agent(self):
        """Случайный выбор User-Agent"""
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0'
        ]
        return random.choice(user_agents)
    
    def parse_products(self):
        """Парсинг товаров из категории обогревательные приборы"""
        try:
            logger.info("🔄 Начинаем парсинг цен с etm.ru...")
            
            response = self.get_with_retry(self.category_url)
            
            if not response:
                logger.error("❌ Все попытки парсинга завершились неудачно")
                return []
            
            # Проверяем, что получили HTML, а не страницу с блокировкой
            if 'cloudflare' in response.text.lower() or 'access denied' in response.text.lower():
                logger.error("❌ Обнаружена защита Cloudflare или блокировка доступа")
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            products = []
            
            # Пробуем разные селекторы для поиска товаров
            product_selectors = [
                '.catalog-item', '.product-card', '.item', 
                '[data-product-id]', '.js-product', '.product-item',
                '.catalog__item', '.goods-item', '.item-product'
            ]
            
            product_cards = None
            for selector in product_selectors:
                product_cards = soup.select(selector)
                if product_cards:
                    logger.info(f"✅ Найден селектор: {selector}")
                    break
            
            if not product_cards:
                logger.warning("⚠️ Не найдено товаров с помощью стандартных селекторов")
                # Пробуем найти любые карточки с ценами
                product_cards = soup.find_all(class_=lambda x: x and any(word in str(x).lower() for word in ['item', 'card', 'product', 'goods']))
            
            logger.info(f"📦 Найдено карточек товаров: {len(product_cards)}")
            
            for card in product_cards[:20]:  # Ограничиваем для теста
                try:
                    # Извлекаем название товара
                    name_selectors = ['.item-name', '.product-name', '.name', 'h3', 'h4', '.title', '.goods-name']
                    product_name = None
                    for selector in name_selectors:
                        name_elem = card.select_one(selector)
                        if name_elem:
                            product_name = name_elem.get_text(strip=True)
                            break
                    
                    if not product_name:
                        continue
                    
                    # Извлекаем цену
                    price_selectors = ['.price', '.item-price', '.product-price', '[data-price]', '.cost', '.value']
                    price = 0
                    for selector in price_selectors:
                        price_elem = card.select_one(selector)
                        if price_elem:
                            price_text = price_elem.get_text(strip=True)
                            price = self.clean_price(price_text)
                            if price > 0:
                                break
                    
                    if price <= 0:
                        continue
                    
                    # Извлекаем ссылку на товар
                    link_elem = card.find('a')
                    product_link = link_elem.get('href') if link_elem else ''
                    if product_link and not product_link.startswith('http'):
                        product_link = self.base_url + product_link
                    
                    # Генерируем ID товара
                    product_id = card.get('data-product-id') or card.get('id') or self.generate_product_id(product_name)
                    
                    products.append({
                        'id': product_id,
                        'name': product_name[:100],  # Ограничиваем длину названия
                        'price': price,
                        'link': product_link,
                        'last_updated': datetime.now().isoformat()
                    })
                        
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка при обработке карточки товара: {e}")
                    continue
            
            logger.info(f"✅ Успешно обработано товаров: {len(products)}")
            return products
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при парсинге: {e}")
            return []
    
    def clean_price(self, price_text):
        """Очистка и преобразование цены в число"""
        try:
            # Удаляем все символы кроме цифр и запятой/точки
            cleaned = ''.join(c for c in price_text if c.isdigit() or c in ',.')
            cleaned = cleaned.replace(',', '.').replace(' ', '')
            # Удаляем все символы после первой точки (если цена в формате 123.45 руб)
            if '.' in cleaned:
                parts = cleaned.split('.')
                if len(parts) > 2:
                    cleaned = parts[0] + '.' + parts[1]
            return float(cleaned)
        except:
            return 0
    
    def generate_product_id(self, product_name):
        """Генерация ID товара на основе названия"""
        import hashlib
        return hashlib.md5(product_name.encode()).hexdigest()[:10]
    
    def load_previous_prices(self):
        """Загрузка предыдущих цен из файла"""
        try:
            if os.path.exists(PRICES_FILE):
                with open(PRICES_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке предыдущих цен: {e}")
            return {}
    
    def save_current_prices(self, products):
        """Сохранение текущих цен в файл"""
        try:
            prices_data = {}
            for product in products:
                prices_data[product['id']] = {
                    'name': product['name'],
                    'price': product['price'],
                    'link': product['link'],
                    'last_updated': product['last_updated']
                }
            
            with open(PRICES_FILE, 'w', encoding='utf-8') as f:
                json.dump(prices_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"💾 Сохранено цен для {len(prices_data)} товаров")
            return prices_data
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении цен: {e}")
            return {}
    
    def check_price_changes(self, current_products):
        """Проверка изменений цен на 10% и более"""
        previous_prices = self.load_previous_prices()
        changes = []
        
        for product in current_products:
            product_id = product['id']
            current_price = product['price']
            
            if product_id in previous_prices:
                previous_price = previous_prices[product_id]['price']
                
                if previous_price > 0:
                    change_percent = ((current_price - previous_price) / previous_price) * 100
                    
                    if abs(change_percent) >= 10:
                        changes.append({
                            'name': product['name'],
                            'previous_price': previous_price,
                            'current_price': current_price,
                            'change_percent': change_percent,
                            'link': product['link']
                        })
        
        return changes

# Создаем монитор цен
price_monitor = PriceMonitor()

# ===== ОБРАБОТЧИКИ КОМАНД ТЕЛЕГРАМ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    welcome_text = (
        "🔍 *Мониторинг цен на обогревательные приборы*\n\n"
        "Я отслеживаю цены на сайте etm.ru в категории:\n"
        "«Обогревательные приборы»\n\n"
        "*Доступные команды:*\n"
        "/check - проверить текущие цены\n"
        "/monitor - запустить мониторинг изменений\n"
        "/get_prices - выгрузить файл с данными цен\n"
        "/help - справка\n\n"
        "⚡ *Важно:* Сайт etm.ru может блокировать запросы. Если цены не загружаются, попробуйте позже."
    )
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /help"""
    help_text = (
        "ℹ️ *Помощь по боту мониторинга цен*\n\n"
        "*Команды:*\n"
        "/start - начать работу\n"
        "/check - проверить текущие цены\n"
        "/monitor - проверить изменения цен\n"
        "/get_prices - получить файл с данными цен\n"
        "/help - эта справка\n\n"
        "*Возможные проблемы:*\n"
        "• Сайт etm.ru может блокировать запросы\n"
        "• Попробуйте команду /check несколько раз\n"
        "• Время работы может быть ограничено\n\n"
        "📞 Поддержка: @Alex_De_White"
    )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def check_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка текущих цен"""
    await update.message.reply_text("🔄 Загружаю актуальные цены... Это может занять до 30 секунд.")
    
    try:
        products = price_monitor.parse_products()
        
        if not products:
            await update.message.reply_text(
                "❌ Не удалось загрузить цены. Сайт etm.ru временно блокирует запросы.\n\n"
                "🔧 *Что можно сделать:*\n"
                "• Попробуйте позже\n"
                "• Используйте команду /check повторно\n"
                "• Сайт может быть недоступен с серверов Render"
            )
            return
        
        price_monitor.save_current_prices(products)
        
        message = "📊 *Текущие цены на обогревательные приборы:*\n\n"
        
        for i, product in enumerate(products[:8], 1):  # Показываем больше товаров
            message += f"{i}. *{product['name']}*\n"
            message += f"   💰 *{product['price']:.2f} руб.*\n"
            if product['link']:
                message += f"   🔗 [Ссылка]({product['link']})\n"
            message += "\n"
        
        message += f"Всего найдено товаров: {len(products)}"
        
        await update.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке цен: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке цен.")

# ... остальные функции (monitor_prices, get_prices_file, вебхуки) остаются без изменений ...
# Копируйте их из предыдущего кода

# ===== ВЕБХУК ЭНДПОИНТЫ =====
async def webhook(request: Request) -> Response:
    """Эндпоинт для вебхуков от Telegram"""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.update_queue.put(update)
        return Response()
    except Exception as e:
        logger.error(f"❌ Ошибка в вебхуке: {e}")
        return Response(status_code=500)

async def health_check(request: Request) -> PlainTextResponse:
    """Эндпоинт для проверки здоровья приложения"""
    return PlainTextResponse("OK")

async def set_webhook():
    """Установка вебхука при запуске"""
    if WEBHOOK_URL:
        try:
            await application.bot.set_webhook(url=f"{WEBHOOK_URL}")
            logger.info(f"✅ Вебхук установлен: {WEBHOOK_URL}")
        except Exception as e:
            logger.error(f"❌ Ошибка установки вебхука: {e}")
    else:
        logger.warning("⚠️ RENDER_EXTERNAL_URL не установлен, вебхук не настроен")

# ===== РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ =====
def setup_handlers():
    """Регистрация всех обработчиков"""
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_prices))
    # Добавьте другие обработчики если они у вас есть
    logger.info("✅ Все обработчики команд зарегистрированы")

# ===== ЗАПУСК ПРИЛОЖЕНИЯ =====
async def main():
    """Основная функция запуска"""
    try:
        logger.info("🔄 Инициализация бота мониторинга цен...")
        
        setup_handlers()
        
        await application.initialize()
        await application.start()
        logger.info("✅ Приложение Telegram инициализировано и запущено")
        
        await set_webhook()
        
        # Создаем Starlette приложение
        starlette_app = Starlette(routes=[
            Route("/webhook", webhook, methods=["POST"]),
            Route("/healthcheck", health_check, methods=["GET"]),
            Route("/", health_check, methods=["GET"]),
        ])
        
        # Запускаем сервер
        config = uvicorn.Config(
            app=starlette_app,
            host="0.0.0.0",
            port=PORT,
            log_level="info"
        )
        server = uvicorn.Server(config)
        
        logger.info(f"🤖 Бот мониторинга цен запущен на порту {PORT}")
        logger.info(f"🌐 Вебхук URL: {WEBHOOK_URL}")
        
        await server.serve()
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске: {e}")
        await application.stop()
        raise

if __name__ == "__main__":
    asyncio.run(main())
