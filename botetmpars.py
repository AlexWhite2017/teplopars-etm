import os
import logging
import requests
import json
import asyncio
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

# Настройки для Render
TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", 8000))
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL", "") + "/webhook"

# Файл для хранения цен
PRICES_FILE = "prices.json"

# Создаем приложение Telegram
application = Application.builder().token(TOKEN).build()

class PriceMonitor:
    """Мониторинг цен на обогревательные приборы с etm.ru"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        self.base_url = "https://www.etm.ru"
        self.category_url = "https://www.etm.ru/catalog/6040_obogrevatelnye_pribory"
    
    def parse_products(self):
        """Парсинг товаров из категории обогревательных приборов"""
        try:
            logger.info("Начинаем парсинг цен с etm.ru...")
            
            response = requests.get(self.category_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            products = []
            
            # Ищем карточки товаров - эти селекторы могут потребовать адаптации
            product_cards = soup.select('.catalog-item, .product-card, .item')
            
            if not product_cards:
                # Альтернативные селекторы
                product_cards = soup.select('[data-product-id], .js-product')
            
            logger.info(f"Найдено карточек товаров: {len(product_cards)}")
            
            for card in product_cards:
                try:
                    # Извлекаем название товара
                    name_elem = card.select_one('.item-name, .product-name, .name, h3, h4')
                    if not name_elem:
                        continue
                    
                    product_name = name_elem.get_text(strip=True)
                    
                    # Извлекаем цену
                    price_elem = card.select_one('.price, .item-price, .product-price, [data-price]')
                    if not price_elem:
                        continue
                    
                    price_text = price_elem.get_text(strip=True)
                    # Очищаем цену от лишних символов
                    price = self.clean_price(price_text)
                    
                    # Извлекаем ссылку на товар
                    link_elem = card.find('a')
                    product_link = link_elem.get('href') if link_elem else ''
                    if product_link and not product_link.startswith('http'):
                        product_link = self.base_url + product_link
                    
                    # Извлекаем артикул/ID товара
                    product_id = card.get('data-product-id') or card.get('id') or self.generate_product_id(product_name)
                    
                    if product_name and price > 0:
                        products.append({
                            'id': product_id,
                            'name': product_name,
                            'price': price,
                            'link': product_link,
                            'last_updated': datetime.now().isoformat()
                        })
                        
                except Exception as e:
                    logger.warning(f"Ошибка при обработке карточки товара: {e}")
                    continue
            
            logger.info(f"Успешно обработано товаров: {len(products)}")
            return products
            
        except Exception as e:
            logger.error(f"Ошибка при парсинге: {e}")
            return []
    
    def clean_price(self, price_text):
        """Очистка и преобразование цены в число"""
        try:
            # Удаляем все символы кроме цифр и запятой/точки
            cleaned = ''.join(c for c in price_text if c.isdigit() or c in ',.')
            # Заменяем запятую на точку для float преобразования
            cleaned = cleaned.replace(',', '.').replace(' ', '')
            # Берем только первую цену если их несколько
            if 'р' in cleaned.lower():
                cleaned = cleaned.split('р')[0]
            return float(cleaned)
        except:
            return 0
    
    def generate_product_id(self, product_name):
        """Генерация ID товара на основе названия"""
        return str(hash(product_name))[:10]
    
    def load_previous_prices(self):
        """Загрузка предыдущих цен из файла"""
        try:
            if os.path.exists(PRICES_FILE):
                with open(PRICES_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Ошибка при загрузке предыдущих цен: {e}")
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
            
            logger.info(f"Сохранено цен для {len(prices_data)} товаров")
            return prices_data
        except Exception as e:
            logger.error(f"Ошибка при сохранении цен: {e}")
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
                    
                    # Если изменение больше 10% в любую сторону
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
        "Я буду уведомлять вас об изменениях цен на 10% и более!"
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
        "*Функциональность:*\n"
        "• Парсинг цен с etm.ru\n"
        "• Сравнение с предыдущими ценами\n"
        "• Уведомления об изменениях ±10%\n"
        "• Автоматическое сохранение данных\n\n"
        "📞 Поддержка: @Alex_De_White"
    )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def check_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка текущих цен"""
    await update.message.reply_text("🔄 Загружаю актуальные цены...")
    
    try:
        products = price_monitor.parse_products()
        
        if not products:
            await update.message.reply_text("❌ Не удалось загрузить цены. Попробуйте позже.")
            return
        
        # Сохраняем текущие цены
        price_monitor.save_current_prices(products)
        
        # Формируем сообщение с топ-5 товаров
        message = "📊 *Текущие цены на обогревательные приборы:*\n\n"
        
        for i, product in enumerate(products[:5], 1):
            message += f"{i}. *{product['name']}*\n"
            message += f"   💰 *{product['price']} руб.*\n"
            if product['link']:
                message += f"   🔗 [Ссылка]({product['link']})\n"
            message += "\n"
        
        message += f"Всего товаров в категории: {len(products)}"
        
        await update.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Ошибка при проверке цен: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке цен.")

async def monitor_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка изменений цен"""
    await update.message.reply_text("🔍 Проверяю изменения цен...")
    
    try:
        # Получаем текущие цены
        current_products = price_monitor.parse_products()
        
        if not current_products:
            await update.message.reply_text("❌ Не удалось загрузить текущие цены.")
            return
        
        # Сохраняем текущие цены
        price_monitor.save_current_prices(current_products)
        
        # Проверяем изменения
        changes = price_monitor.check_price_changes(current_products)
        
        if not changes:
            await update.message.reply_text("✅ Значительных изменений цен не обнаружено.")
            return
        
        # Формируем сообщение об изменениях
        message = "🚨 *Обнаружены изменения цен!*\n\n"
        
        for change in changes[:10]:  # Ограничиваем 10 изменениями
            direction = "📈" if change['change_percent'] > 0 else "📉"
            message += f"{direction} *{change['name']}*\n"
            message += f"   Было: {change['previous_price']} руб.\n"
            message += f"   Стало: {change['current_price']} руб.\n"
            message += f"   Изменение: {change['change_percent']:+.1f}%\n"
            if change['link']:
                message += f"   🔗 [Товар]({change['link']})\n"
            message += "\n"
        
        if len(changes) > 10:
            message += f"... и еще {len(changes) - 10} изменений"
        
        await update.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Ошибка при мониторинге цен: {e}")
        await update.message.reply_text("❌ Произошла ошибка при проверке изменений.")

async def get_prices_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выгрузка файла с данными цен"""
    try:
        # Проверяем существование файла
        if not os.path.exists(PRICES_FILE):
            await update.message.reply_text("❌ Файл с ценами еще не создан. Сначала выполните команду /check")
            return
        
        # Читаем и проверяем файл
        with open(PRICES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Создаем временный файл с красивым форматированием
        temp_filename = f"prices_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(temp_filename, 'w', encoding='utf-8') as temp_file:
            json.dump(data, temp_file, ensure_ascii=False, indent=2, sort_keys=True)
        
        # Отправляем файл пользователю
        with open(temp_filename, 'rb') as file_to_send:
            await update.message.reply_document(
                document=file_to_send,
                filename=temp_filename,
                caption="📄 Файл с данными о ценах на обогревательные приборы"
            )
        
        # Удаляем временный файл
        os.remove(temp_filename)
        
        # Дополнительная информация о данных
        stats_message = (
            f"📊 *Статистика данных:*\n"
            f"• Товаров в базе: {len(data)}\n"
            f"• Последнее обновление: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"• Размер файла: {os.path.getsize(PRICES_FILE)} байт"
        )
        await update.message.reply_text(stats_message, parse_mode='Markdown')
        
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка формата JSON: {e}")
        await update.message.reply_text("❌ Ошибка в формате файла цен.")
    except Exception as e:
        logger.error(f"Ошибка при выгрузке файла: {e}")
        await update.message.reply_text("❌ Произошла ошибка при выгрузке файла.")

# ===== ВЕБХУК ЭНДПОИНТЫ ДЛЯ RENDER =====
async def webhook(request: Request) -> Response:
    """Эндпоинт для вебхуков от Telegram"""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.update_queue.put(update)
        return Response()
    except Exception as e:
        logger.error(f"Ошибка в вебхуке: {e}")
        return Response(status_code=500)

async def health_check(request: Request) -> PlainTextResponse:
    """Эндпоинт для проверки здоровья приложения"""
    return PlainTextResponse("OK")

async def set_webhook():
    """Установка вебхука при запуске"""
    if WEBHOOK_URL:
        await application.bot.set_webhook(url=f"{WEBHOOK_URL}")
        logger.info(f"Вебхук установлен: {WEBHOOK_URL}")
    else:
        logger.warning("RENDER_EXTERNAL_URL не установлен, вебхук не настроен")

# ===== РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ =====
def setup_handlers():
    """Регистрация всех обработчиков"""
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_prices))
    application.add_handler(CommandHandler("monitor", monitor_prices))
    application.add_handler(CommandHandler("get_prices", get_prices_file))

# ===== ЗАПУСК ПРИЛОЖЕНИЯ =====
async def main():
    """Основная функция запуска"""
    logger.info("🔄 Инициализация бота мониторинга цен...")
    
    # Регистрируем обработчики
    setup_handlers()
    
    # Запускаем приложение
    await application.initialize()
    await application.start()
    
    # Устанавливаем вебхук
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
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
