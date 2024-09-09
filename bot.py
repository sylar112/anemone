from telegram import Update, InputMediaPhoto, ReplyKeyboardRemove, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, ConversationHandler
from PIL import Image
from telegram.constants import ParseMode
from tenacity import retry, wait_fixed, stop_after_attempt
import asyncio
import requests
import logging
import os
import shutil
import io
import aiohttp
from tenacity import retry, wait_fixed, stop_after_attempt, RetryError
import tempfile
import re

# Укажите ваши токены и ключ для imgbb
TELEGRAM_BOT_TOKEN = '7538468672:AAGRzsQVHQ1mzXgQuBbZjSA4FezIirJxjRA'
TELEGRAPH_TOKEN = 'c244b32be4b76eb082d690914944da14238249bbdd55f6ffd349b9e000c1'
IMGBB_API_KEY = 'fdd4c4ac12a927568f6bd0704d2553fa'

# Состояния
ASKING_FOR_ARTIST_LINK, ASKING_FOR_AUTHOR_NAME, ASKING_FOR_IMAGE = range(3)

# Сохранение данных состояния пользователя
user_data = {}

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    if user_id not in user_data:
        logger.info(f"User {user_id} started the process.")
        await update.message.reply_text('Пожалуйста, отправьте ссылку на автора.\n \n В боте есть команда /restart которая перезапускает процесс на любом этапе')
        user_data[user_id] = {'status': 'awaiting_artist_link'}  # Инициализация данных для пользователя
        return ASKING_FOR_ARTIST_LINK
    else:
        if user_data[user_id]['status'] == 'awaiting_artist_link':
            return await handle_artist_link(update, context)
        elif user_data[user_id]['status'] == 'awaiting_author_name':
            return await handle_author_name(update, context)
        elif user_data[user_id]['status'] == 'awaiting_image':
            return await handle_image(update, context)

async def restart(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    if user_id in user_data:
        del user_data[user_id]
    logger.info(f"User {user_id} restarted the process.")
    await update.message.reply_text('Процесс сброшен. Пожалуйста, начните заново. \n Отправьте ссылку на автора.')
    user_data[user_id] = {'status': 'awaiting_artist_link'}
    return ASKING_FOR_ARTIST_LINK

HELP_TEXT = """
```Python
        "Статья телеграф будет формироваться именно в той последовательности в которой вы будете присылать файлы изображений и/или текст.\n Текст поддерживает следующие тэги (без кавычек)\: \n \n \"\*\*\*\" \- горизонтальная линия по центру, служит для визуального отделения информации. Для применения отправить \*\*\* отдельным сообщением и в этом месте в статье телеграф появится горизонтальный разделитель \n \n \"\_любой текст\_\" \- курсив \n \"\*любой текст\*\" - жирный текст \n \"\(ссылка\)\[текст ссылки\]\" \- гиперссылка \n Этими тэгами можно выделить абсолютно любое слово или слова в вашем сообщении и они будут жирными или курсивными, либо сформируют гиперссылку с кликабельным текстом \n \n \"видео\: \" \- ссылка на vimeo или ютуб \n \"цитата\: \"  \n \"цитата по центру\: \"  \n \"заголовок\: \"  \n \"подзаголовок\: \"  \n  Эти тэги применяются на всё сообщение целиком, для их применения просто введите в начале сообщения нужный тэг. Каждое отдельное текстовое сообщение отправленное боту будет отображаться в статье как отдельный абзац, к каждому новому сообщению можно применить уникальный тэг при необходимости. Сообщение без тэга будет обычным текстом \n \n Например сообщение\:\(без кавычек\) \n \"тэг цитата\: \*Волк\* никогда не будет жить в \_загоне\_, но загоны всегда будут жить в \*волке\*\"\n В телеграфе будет выглядеть как цитата в которой слово \"волк\" выделено жирным, а \"загоне\" \- курсивом\n\n Или например текст \"видео\: \(сылка на видео ютуб или вимео начинающаяся с https\)\" \- отправленный отдельным сообщением, будет отображаться как интерактивное видео в статье телеграф\n```\n""" 

async def help_command(update: Update, context: CallbackContext) -> None: await update.message.reply_text( HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2 )

async def handle_artist_link(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    if user_id in user_data and user_data[user_id]['status'] == 'awaiting_artist_link':
        user_data[user_id]['artist_link'] = update.message.text
        logger.info(f"User {user_id} provided author link: {update.message.text}")
        await update.message.reply_text('Теперь отправьте имя автора.')
        user_data[user_id]['status'] = 'awaiting_author_name'
        return ASKING_FOR_AUTHOR_NAME
    else:
        await update.message.reply_text('Ошибка: данные не найдены.')
        return ConversationHandler.END

async def handle_author_name(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    if user_id in user_data and user_data[user_id]['status'] == 'awaiting_author_name':
        user_data[user_id]['author_name'] = update.message.text
        logger.info(f"User {user_id} provided author name: {update.message.text}")
        # Сохраняем имя автора как заголовок статьи
        user_data[user_id]['title'] = update.message.text
        await update.message.reply_text('Теперь отправьте изображения или текст. \n \n Текст поддерживает различное форматирование. Для получения помощи введите /help  \n \n Так же вы можете начажать /restart для сброса')
        user_data[user_id]['status'] = 'awaiting_image'
        return ASKING_FOR_IMAGE
    else:
        await update.message.reply_text('Ошибка: данные не найдены. Попробуйте снова или нажмите /restart')
        return ConversationHandler.END

def compress_image(file_path: str, output_path: str) -> None:
    # Определяем максимальный размер файла в байтах (5 МБ)
    max_size = 5 * 1024 * 1024

    # Открываем изображение
    with Image.open(file_path) as img:
        # Проверяем формат и размер изображения
        if img.format == 'PNG' and os.path.getsize(file_path) > max_size:
            # Если PNG и размер больше 5 МБ, конвертируем в JPG
            img = img.convert('RGB')
            temp_path = file_path.rsplit('.', 1)[0] + '.jpg'
            img.save(temp_path, format='JPEG', quality=90)
            file_path = temp_path
            img = Image.open(file_path)
        
        # Если изображение имеет альфа-канал, преобразуем его в RGB
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            img = img.convert('RGB')

        # Сохраняем изображение в формате JPG с начальным качеством
        quality = 90
        img.save(output_path, format='JPEG', quality=quality)

        # Проверяем размер файла и сжимаем при необходимости
        while os.path.getsize(output_path) > max_size:
            quality -= 10
            if quality < 10:
                break
            img.save(output_path, format='JPEG', quality=quality)

        # Если изображение всё ещё больше 5 МБ, уменьшаем разрешение
        while os.path.getsize(output_path) > max_size:
            width, height = img.size
            img = img.resize((width // 2, height // 2), Image.ANTIALIAS)
            img.save(output_path, format='JPEG', quality=quality)

        # Удаляем временный JPG файл, если он был создан
        if file_path.endswith('.jpg'):
            os.remove(file_path)

# Асинхронная функция для загрузки изображения на imgbb
async def upload_image_to_imgbb(file_path: str) -> str:
    async with aiohttp.ClientSession() as session:
        with open(file_path, 'rb') as f:
            form = aiohttp.FormData()
            form.add_field('key', IMGBB_API_KEY)
            form.add_field('image', f)

            async with session.post('https://api.imgbb.com/1/upload', data=form) as response:
                if response.status == 200:
                    response_json = await response.json()
                    return response_json['data']['url']
                else:
                    raise Exception(f"Ошибка загрузки на imgbb: {response.status}")

import re

# Определяем разметку тегов
markup_tags = {
    '*': 'strong',  # Жирный текст
    '_': 'em',      # Курсив
}

def apply_markup(text: str) -> dict:
    """Применяет разметку к тексту на основе команд и возвращает узел контента в формате Telegra.ph."""
    
    text_lower = text.lower()

    # Обработка команд
    if text_lower.startswith("подзаголовок: "):
        content = text[len("Подзаголовок: "):]
        content = apply_markup_to_content(content)
        return {"tag": "h4", "children": content}
    elif text_lower.startswith("цитата: "):
        content = text[len("Цитата: "):]
        content = apply_markup_to_content(content)
        return {"tag": "blockquote", "children": content}
    elif text_lower.startswith("заголовок: "):
        content = text[len("Заголовок: "):]
        content = apply_markup_to_content(content)
        return {"tag": "h3", "children": content}
    elif text_lower.startswith("цитата по центру: "):
        content = text[len("Цитата по центру: "):]
        content = apply_markup_to_content(content)
        return {"tag": "aside", "children": content}
    elif text_lower.startswith("***"):
        return {"tag": "hr"}
    elif text_lower.startswith("видео: "):
        video_url = text[len("Видео: "):].strip()
        # Кодируем URL, чтобы он подходил для использования в src
        encoded_url = re.sub(r'https://', 'https%3A%2F%2F', video_url)
        
        # Проверяем, это YouTube или Vimeo
        if "youtube.com" in video_url or "youtu.be" in video_url:
            return {
                "tag": "figure",
                "children": [
                    {
                        "tag": "iframe",
                        "attrs": {
                            "src": f"/embed/youtube?url={encoded_url}",
                            "width": 640,
                            "height": 360,
                            "frameborder": 0,
                            "allowtransparency": "true",
                            "allowfullscreen": "true",
                            "scrolling": "no"
                        }
                    }
                ]
            }
        elif "vimeo.com" in video_url:
            return {
                "tag": "figure",
                "children": [
                    {
                        "tag": "iframe",
                        "attrs": {
                            "src": f"/embed/vimeo?url={encoded_url}",
                            "width": 640,
                            "height": 360,
                            "frameborder": 0,
                            "allowtransparency": "true",
                            "allowfullscreen": "true",
                            "scrolling": "no"
                        }
                    }
                ]
            }

    # Если команда не распознана, обрабатываем текст с разметкой
    content = apply_markup_to_content(text)
    return {"tag": "div", "children": content}

def apply_markup_to_content(content: str) -> list:
    """Обрабатывает разметку в тексте и возвращает список узлов для Telegra.ph."""
    nodes = []

    # Регулярные выражения для разметки
    regex_markup = re.compile(r'(\*|_)(.*?)\1', re.DOTALL)
    link_regex = re.compile(r'\((.*?)\)\[(.*?)\]', re.DOTALL)

    # Сначала обрабатываем гиперссылки
    if link_regex.search(content):
        content = process_links(content)

    pos = 0
    for match in regex_markup.finditer(content):
        # Добавляем текст до текущего совпадения
        if pos < match.start():
            nodes.append(content[pos:match.start()])

        # Определяем тег и добавляем узел
        tag = markup_tags.get(match.group(1))
        if tag:
            text = match.group(2)
            nodes.append({"tag": tag, "children": [text]})

        # Обновляем позицию
        pos = match.end()

    # Добавляем оставшийся текст
    if pos < len(content):
        nodes.append(content[pos:])

    return nodes

def process_links(content: str) -> list:
    """Обрабатывает текст, заменяя ссылки на элементы <a>."""
    nodes = []
    link_regex = re.compile(r'\((.*?)\)\[(.*?)\]', re.DOTALL)
    
    pos = 0
    for match in link_regex.finditer(content):
        # Добавляем текст до текущего совпадения
        nodes.append(content[pos:match.start()])

        # Добавляем узел ссылки
        url, link_text = match.groups()
        nodes.append({"tag": "a", "attrs": {"href": url}, "children": [{"tag": "text", "children": [link_text]}]})

        # Обновляем позицию
        pos = match.end()

    # Добавляем оставшийся текст
    if pos < len(content):
        nodes.append(content[pos:])

    return nodes


# Обновленная функция handle_image для обработки изображений
async def handle_image(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    if user_id in user_data and user_data[user_id]['status'] == 'awaiting_image':
        if update.message.photo:
            await update.message.reply_text('Пожалуйста, отправьте изображение как файл (формат JPG или PNG), без сжатия.')
            return ASKING_FOR_IMAGE
        elif update.message.document:
            file_name = update.message.document.file_name
            file_ext = file_name.lower().split('.')[-1]
            file = await context.bot.get_file(update.message.document.file_id)

            # Создаём временный файл
            with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_ext}') as tmp_file:
                file_path = tmp_file.name
                await file.download_to_drive(file_path)

            # Если файл .rar, переименовываем его в .gif
            if file_ext == 'rar':
                new_file_path = f'{os.path.splitext(file_path)[0]}.gif'
                shutil.move(file_path, new_file_path)
                file_path = new_file_path
                file_name = os.path.basename(file_path)
                file_ext = 'gif'

            if file_ext in ('jpg', 'jpeg', 'png', 'gif'):
                # Если изображение больше 5 МБ, сжимаем его
                if os.path.getsize(file_path) > 5 * 1024 * 1024:
                    compressed_path = f'{os.path.splitext(file_path)[0]}_compressed.jpg'
                    compress_image(file_path, compressed_path)
                    file_path = compressed_path

                try:
                    # Запускаем асинхронную загрузку изображений
                    image_url = await upload_image_to_imgbb(file_path)
                    if 'media' not in user_data[user_id]:
                        user_data[user_id]['media'] = []
                    user_data[user_id]['media'].append({'type': 'image', 'url': image_url})
                    os.remove(file_path)  # Удаляем временный файл
                    await update.message.reply_text('Одно изображение добавлено.\n\n Если желаете завершить публикацию введите /publish для завершения.')
                    return ASKING_FOR_IMAGE
                except Exception as e:
                    await update.message.reply_text(f'Ошибка при загрузке изображения на imgbb: {str(e)} Можете попробовать прислать файл ещё раз или нажать /restart')
                    return ConversationHandler.END
            else:
                await update.message.reply_text('Пожалуйста, отправьте изображение в формате JPG, PNG или GIF, без сжатия.')
                return ASKING_FOR_IMAGE
        elif update.message.text:
            # Обработка текстовых сообщений с разметкой
            formatted_text = apply_markup(update.message.text)
            if 'media' not in user_data[user_id]:
                user_data[user_id]['media'] = []
            user_data[user_id]['media'].append({'type': 'text', 'content': formatted_text})
            await update.message.reply_text('Текст успешно добавлен. Вы можете отправить ещё текст или изображения. \n\n Либо завершить публикацию с помощью команды /publish.')
            return ASKING_FOR_IMAGE
        else:
            await update.message.reply_text('Пожалуйста, отправьте изображение как файл (формат JPG, PNG или GIF), без сжатия, или текст.')
            return ASKING_FOR_IMAGE
    else:
        await update.message.reply_text('Ошибка: данные не найдены. Попробуйте отправить снова. Или нажмите /restart')
        return ConversationHandler.END

@retry(wait=wait_fixed(2), stop=stop_after_attempt(3))
def make_request(url, data):
    response = requests.post(url, json=data)
    response.raise_for_status()
    return response.json()

# Функция для отправки медиа-сообщений с повторными попытками
@retry(wait=wait_fixed(2), stop=stop_after_attempt(3))
async def send_media_with_retries(update, media_group, caption):
    try:
        await update.message.reply_text(caption, parse_mode='HTML')
        await update.message.reply_media_group(media=media_group)
    except Exception as e:
        logger.error(f"Failed to send media group: {e}")
        raise  # Перекидываем исключение для повторных попыток

async def send_media_group(update, media_group, caption):
    if not media_group:
        logger.error("Media group is empty")
        return
    try:
        await update.message.reply_text(caption, parse_mode='HTML')
        await update.message.reply_media_group(media=media_group)
    except Exception as e:
        logger.error(f"Failed to send media group: {e}")
        raise

async def publish(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    if user_id in user_data:
        try:
            author_name = "Anemone"  # Установлено имя автора
            author_link = "https://t.me/anemonn"  # Ссылка на автора
            artist_link = user_data[user_id]['artist_link']
            media = user_data[user_id].get('media', [])
            title = user_data[user_id].get('title', 'test')
            

            # Создание статьи в Telegra.ph
            content = [
                {
                    'tag': 'p',
                    'children': [
                        {
                            'tag': 'a',
                            'attrs': {'href': artist_link},
                            'children': [artist_link]  # Используем URL как текст гиперссылки
                        }
                    ]
                }
            ]

            # Добавление изображений с разделителями
            for index, item in enumerate(media):
                if item['type'] == 'text':
                    content.append({'tag': 'p', 'children': [item['content']]})
                elif item['type'] == 'image':
                    content.append({'tag': 'img', 'attrs': {'src': item['url']}})
                    # Добавляем горизонтальный разделитель, если это не последнее изображение
                    if index < len(media) - 1:
                        content.append({'tag': 'hr'})

            # Добавляем текст с уменьшенным размером в конце статьи
            content.append({'tag': 'hr'})  # Добавляем разделитель
            content.append({
                'tag': 'i',  # Тег для выделения текста курсивом
                'children': [f'Оригиналы доступны в браузере через меню (⋮)']
            })

            response = requests.post('https://api.telegra.ph/createPage', json={
                'access_token': TELEGRAPH_TOKEN,
                'title': title,
                'author_name': author_name,
                'author_url': author_link,  # Ссылка на автора
                'content': content
            })
            response.raise_for_status()  # Проверяем, что запрос выполнен успешно
            response_json = response.json()

            if response_json.get('ok'):
                article_url = f"https://telegra.ph/{response_json['result']['path']}"

                # Получение списка содержимого статьи
                article_response = requests.get(f'https://api.telegra.ph/getPage?access_token={TELEGRAPH_TOKEN}&path={response_json["result"]["path"]}&return_content=true')
                article_response.raise_for_status()
                article_data = article_response.json()

                              # Подсчет количества изображений
                image_count = sum(1 for item in article_data.get('result', {}).get('content', []) if item.get('tag') == 'img')

                if image_count > 1:
                    message_with_link = f'Автор: {title}\n<a href="{article_url}">Оригинал</a>'
                    await update.message.reply_text(message_with_link, parse_mode='HTML')

                    media_groups = [media[i:i + 10] for i in range(0, len(media), 10)]

                    for group in media_groups:
                        media_group = [InputMediaPhoto(media=item['url']) for item in group if item['type'] == 'image']

                        # Вставьте проверку здесь
                        if not media_group:
                            logger.error("Media group is empty")
                            await update.message.reply_text('Ошибка при отправке медиа. /restart')
                            return

                        try:
                            await update.message.reply_media_group(media_group)
                        except Exception as e:
                            logger.error(f"Failed to send media group: {e}")
                            await update.message.reply_text('Ошибка при отправке медиа. /restart')

                elif image_count == 1:
                    # Если одно изображение, отправляем одно сообщение с изображением и ссылкой
                    single_image = next((item for item in media if item['type'] == 'image'), None)
                    if single_image:
                        await update.message.reply_photo(
                            photo=single_image['url'],
                            caption=f'Автор: {title}\n<a href="{article_url}">Оригинал</a>',
                            parse_mode='HTML'
                        )


                # Отправляем сообщение с количеством изображений
                await update.message.reply_text(f'В статье {image_count} изображений.')

                del user_data[user_id]
                await update.message.reply_text(
                    'Процесс завершен. Бот перезапущен успешно.',
                    reply_markup=ReplyKeyboardRemove()
                )
                logger.info(f"User {user_id}'s data cleared and process completed.")
                await start(update, context)
                return ConversationHandler.END
            else:
                await update.message.reply_text('Ошибка при создании статьи. /restart')
        except requests.RequestException as e:
            logger.error(f"Request error: {e}")
            await update.message.reply_text('Ошибка при создании статьи. /restart')
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            await update.message.reply_text('Произошла непредвиденная ошибка. /restart')
        
        del user_data[user_id]
        logger.info(f"Error occurred. User {user_id}'s data cleared.")
        return ConversationHandler.END
    else:
        await update.message.reply_text('Ошибка: данные не найдены. /restart')
        return ConversationHandler.END

async def unknown_message(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    if user_id not in user_data:
        await update.message.reply_text('Неизвестное сообщение. Пожалуйста, отправьте ссылку на автора, имя автора или изображение. В случае если это сообщение повторяется нажмите /restart')
    else:
        # Обработка сообщений в процессе
        if user_data[user_id]['status'] == 'awaiting_artist_link':
            await handle_artist_link(update, context)
        elif user_data[user_id]['status'] == 'awaiting_author_name':
            await handle_author_name(update, context)
        elif user_data[user_id]['status'] == 'awaiting_image':
            await handle_image(update, context)


def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Настройка ConversationHandler
    conversation_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, start)],
        states={
            ASKING_FOR_ARTIST_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_artist_link)],
            ASKING_FOR_AUTHOR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_author_name)],
            ASKING_FOR_IMAGE: [MessageHandler(filters.PHOTO | filters.Document.ALL, handle_image)]
        },
        fallbacks=[MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_message)],
        per_user=True
    )

    application.add_handler(CommandHandler('restart', restart))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('publish', publish))
    application.add_handler(conversation_handler)
    logger.info("Bot started and polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
