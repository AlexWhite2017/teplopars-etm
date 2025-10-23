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
PRICES_FILE = "prices.json"

# Создаем приложение Telegram
try:
    application = Application.builder().token(TOKEN).build()
    logger.info("✅ Приложение Telegram успешно создано")
except Exception as e:
    logger.error(f"❌ Ошибка при создании приложения: {e}")
    sys.exit(1)

class PriceMonitor:
    """Мониторинг цен на обогревательные приборы с DNS-Shop"""
    
    def __init__(self):
        self.session = requests.Session()
        self.setup_headers()
        self.base_url = "https://www.dns-shop.ru"
        self.category_url = "https://www.dns-shop.ru/catalog/17a89fab16404e77/obogrevateli/"
    
    def setup_headers(self):
        """Настройка реалистичных заголовков"""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        })
    
    def parse_products_dns(self):
        """Парсинг обогревателей с DNS-Shop"""
        try:
            logger.info("🔄 Начинаем парсинг цен с DNS-Shop...")
            
            response = self.session.get(self.category_url, timeout=15)
            
            if response.status_code != 200:
                logger.error(f"❌ Ошибка HTTP: {response.status_code}")
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            products = []
            
            # Ищем карточки товаров на DNS-Shop
            product_cards = soup.select('.catalog-product, .product-card, [data-id]')
            
            logger.info(f"📦 Найдено карточек товаров: {len(product_cards)}")
            
            for card in product_cards[:15]:  # Ограничиваем количество
                try:
                    # Название товара
                    name_elem = card.select_one('.catalog-product__name, .product-title, .title')
                    if not name_elem:
                        continue
                    
                    product_name = name_elem.get_text(strip=True)
                    
                    # Цена товара
                    price_elem = card.select_one('.product-buy__price, .price, .cost')
                    if not price_elem:
                        continue
                    
                    price_text = price_elem.get_text(strip=True)
                    price = self.clean_price(price_text)
                    
                    if price <= 0:
                        continue
                    
                    # Ссылка на товар
                    link_elem = card.find('a')
                    product_link = link_elem.get('href') if link_elem else ''
                    if product_link and not product_link.startswith('http'):
                        product_link = self.base_url + product_link
                    
                    # ID товара
                    product_id = card.get('data-id') or self.generate_product_id(product_name)
                    
                    products.append({
                        'id': product_id,
                        'name': product_name[:100],
                        'price': price,
                        'link': product_link,
                        'source': 'DNS-Shop',
                        'last_updated': datetime.now().isoformat()
                    })
                        
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка при обработке карточки: {e}")
                    continue
            
            logger.info(f"✅ Успешно обработано товаров: {len(products)}")
            return products
            
        except Exception as e:
            logger.error(f"❌ Ошибка при парсинге DNS-Shop: {e}")
            return []
    
    def parse_products_citilink(self):
        """Парсинг обогревателей с Citilink (альтернатива)"""
        try:
            logger.info("🔄 Пробуем Citilink...")
            
            url = "https://www.citilink.ru/catalog/obogrevateli/"
            response = self.session.get(url, timeout=15)
            
            if response.status_code != 200:
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            products = []
            
            product_cards = soup.select('.ProductCard, .product_data, [data-product-id]')
            
            for card in product_cards[:10]:
                try:
                    name_elem = card.select_one('.ProductCard__name, .title')
                    price_elem = card.select_one('.ProductCard__price, .price')
                    
                    if name_elem and price_elem:
                        product_name = name_elem.get_text(strip=True)
                        price = self.clean_price(price_elem.get_text(strip=True))
                        
                        if price > 0:
                            products.append({
                                'id': self.generate_product_id(product_name),
                                'name': product_name[:100],
                                'price': price,
                                'link': '',
                                'source': 'Citilink',
                                'last_updated': datetime.now().isoformat()
                            })
                except Exception:
                    continue
            
            return products
            
        except Exception as e:
            logger.error(f"❌ Ошибка при парсинге Citilink: {e}")
            return []
    
    def clean_price(self, price_text):
        """Очистка и преобразование цены в число"""
        try:
            # Удаляем все символы кроме цифр
            cleaned = ''.join(c for c in price_text if c.isdigit())
            return float(cleaned) if cleaned else 0
        except:
            return 0
    
    def generate_product_id(self, product_name):
        """Генерация ID товара"""
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
                    'source': product.get('source', 'Unknown'),
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
                            'link': product['link'],
                            'source': product.get('source', 'Unknown')
                        })
        
        return changes

# Создаем монитор цен
price_monitor = PriceMonitor()

# ===== ОБРАБОТЧИКИ КОМАНД ТЕЛЕГРАМ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    welcome_text = (
        "🔍 *Мониторинг цен на обогревательные приборы*\n\n"
        "Я отслеживаю цены в интернет-магазинах:\n"
        "• DNS-Shop\n"
        "• Citilink\n\n"
        "*Доступные команды:*\n"
        "/check - проверить текущие цены\n"
        "/monitor - проверить изменения цен\n"
        "/get_prices - выгрузить файл с данными\n"
        "/help - справка\n\n"
        "⚡ *Примечание:* etm.ru блокирует запросы, использую альтернативные источники."
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
        "/get_prices - получить файл с данными\n"
        "/help - эта справка\n\n"
        "*Источники данных:*\n"
        "• DNS-Shop - обогреватели\n"
        "• Citilink - обогреватели\n\n"
        "📞 Поддержка: @Alex_De_White"
    )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def check_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка текущих цен"""
    await update.message.reply_text("🔄 Загружаю цены с DNS-Shop и Citilink...")
    
    try:
        # Пробуем разные источники
        products = []
        
        dns_products = price_monitor.parse_products_dns()
        if dns_products:
            products.extend(dns_products)
            logger.info(f"✅ DNS-Shop: {len(dns_products)} товаров")
        
        citilink_products = price_monitor.parse_products_citilink()
        if citilink_products:
            products.extend(citilink_products)
            logger.info(f"✅ Citilink: {len(citilink_products)} товаров")
        
        if not products:
            await update.message.reply_text(
                "❌ Не удалось загрузить цены ни с одного источника.\n\n"
                "🔧 *Возможные причины:*\n"
                "• Временные проблемы с сайтами\n"
                "• Блокировка запросов с Render\n"
                "• Изменение структуры сайтов\n\n"
                "Попробуйте позже или используйте другие источники."
            )
            return
        
        # Сохраняем цены
        price_monitor.save_current_prices(products)
        
        # Формируем сообщение
        message = "📊 *Текущие цены на обогреватели:*\n\n"
        
        for i, product in enumerate(products[:8], 1):
            source_icon = "🛒" if product.get('source') == 'DNS-Shop' else "🔵"
            message += f"{i}. {source_icon} *{product['name']}*\n"
            message += f"   💰 *{product['price']:.0f} руб.*\n"
            if product.get('source'):
                message += f"   📍 {product['source']}\n"
            if product['link']:
                message += f"   🔗 [Ссылка]({product['link']})\n"
            message += "\n"
        
        message += f"Всего найдено товаров: {len(products)}"
        
        await update.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке цен: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке цен.")

async def monitor_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка изменений цен"""
    await update.message.reply_text("🔍 Проверяю изменения цен...")
    
    try:
        # Получаем текущие цены
        products = []
        products.extend(price_monitor.parse_products_dns())
        products.extend(price_monitor.parse_products_citilink())
        
        if not products:
            await update.message.reply_text("❌ Не удалось загрузить текущие цены.")
            return
        
        price_monitor.save_current_prices(products)
        changes = price_monitor.check_price_changes(products)
        
        if not changes:
            await update.message.reply_text("✅ Значительных изменений цен не обнаружено.")
            return
        
        # Формируем сообщение об изменениях
        message = "🚨 *Обнаружены изменения цен!*\n\n"
        
        for change in changes[:8]:
            direction = "📈" if change['change_percent'] > 0 else "📉"
            source_icon = "🛒" if change.get('source') == 'DNS-Shop' else "🔵"
            message += f"{direction} {source_icon} *{change['name']}*\n"
            message += f"   Было: {change['previous_price']:.0f} руб.\n"
            message += f"   Стало: {change['current_price']:.0f} руб.\n"
            message += f"   Изменение: {change['change_percent']:+.1f}%\n"
            if change.get('source'):
                message += f"   📍 {change['source']}\n"
            message += "\n"
        
        if len(changes) > 8:
            message += f"... и еще {len(changes) - 8} изменений"
        
        await update.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка при мониторинге цен: {e}")
        await update.message.reply_text("❌ Произошла ошибка при проверке изменений.")

async def get_prices_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выгрузка файла с данными цен"""
    try:
        if not os.path.exists(PRICES_FILE):
            await update.message.reply_text("❌ Файл с ценами еще не создан. Сначала выполните команду /check")
            return
        
        with open(PRICES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        temp_filename = f"prices_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(temp_filename, 'w', encoding='utf-8') as temp_file:
            json.dump(data, temp_file, ensure_ascii=False, indent=2, sort_keys=True)
        
        with open(temp_filename, 'rb') as file_to_send:
            await update.message.reply_document(
                document=file_to_send,
                filename=temp_filename,
                caption="📄 Файл с данными о ценах на обогреватели"
            )
        
        os.remove(temp_filename)
        
        stats_message = (
            f"📊 *Статистика данных:*\n"
            f"• Товаров в базе: {len(data)}\n"
            f"• Последнее обновление: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"• Размер файла: {os.path.getsize(PRICES_FILE)} байт"
        )
        await update.message.reply_text(stats_message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"❌ Ошибка при выгрузке файла: {e}")
        await update.message.reply_text("❌ Произошла ошибка при выгрузке файла.")

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
    application.add_handler(CommandHandler("monitor", monitor_prices))
    application.add_handler(CommandHandler("get_prices", get_prices_file))
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
