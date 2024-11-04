from telegram import Update, InputMediaPhoto, ReplyKeyboardRemove, InputMediaDocument, InputMediaVideo, InlineKeyboardButton, InlineKeyboardMarkup, Message, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, ConversationHandler, CallbackQueryHandler
from PIL import Image
from telegram.constants import ParseMode
from tenacity import retry, wait_fixed, stop_after_attempt
from background import keep_alive
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
from requests.exceptions import Timeout
from bs4 import BeautifulSoup
import wikipediaapi
import wikipedia
import gpt_helper
from gpt_helper import add_to_context, generate_gemini_response, limit_response_length, user_contexts, save_context_to_firebase, load_context_from_firebase, get_clean_response_text
from collections import deque
from aiohttp import ClientSession, ClientTimeout, FormData
import chardet
import json
import os

# Укажите ваши токены и ключ для imgbb
TELEGRAM_BOT_TOKEN = '7538468672:AAEOEFS7V0z0uDzZkeGNQKYsDGlzdOziAZI'
TELEGRAPH_TOKEN = 'c244b32be4b76eb082d690914944da14238249bbdd55f6ffd349b9e000c1'
IMGBB_API_KEY = '25c8af109577638da9ba88a667be22b1'
GROUP_CHAT_ID = -1002233281756

# Состояния
# Состояния
ASKING_FOR_ARTIST_LINK, ASKING_FOR_AUTHOR_NAME, ASKING_FOR_IMAGE, EDITING_FRAGMENT, ASKING_FOR_FILE, ASKING_FOR_OCR, RUNNING_GPT_MODE = range(7)
# Сохранение данных состояния пользователя
user_data = {}
publish_data = {}
users_in_send_mode = set()
media_group_storage = {}
is_search_mode = {}
is_ocr_mode = {}
is_gpt_mode = {}

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id

    # Проверяем, есть ли пользователь в данных
    if update.message:
        message_to_reply = update.message
        user_id = update.message.from_user.id
    elif update.callback_query:
        message_to_reply = update.callback_query.message
        user_id = update.callback_query.from_user.id
    else:
        return ConversationHandler.END  # На случай, если ни одно условие не выполнится

    # Проверяем, есть ли пользователь в данных
    if user_id not in user_data:
        logger.info(f"User {user_id} started the process.")
        
        # Создаем кнопку "Начать поиск"
        # Создаем кнопку "Начать поиск"
        keyboard = [
            [InlineKeyboardButton("🎨 Найти автора или проверить на ИИ 🎨", callback_data='start_search')],
            [InlineKeyboardButton("🌱 Распознать (Растение или текст) 🌱", callback_data='start_ocr')],            
            [InlineKeyboardButton("🦊 Поговорить с ботом 🦊", callback_data='run_gpt')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Отправляем сообщение с кнопкой
        await message_to_reply.reply_text(
            '🌠Этот бот поможет вам создать пост для группы Anemone. Изначально пост будет виден исключительно вам, так что не бойтесь экспериментировать и смотреть что получится\n\n'
            'Для начала, пожалуйста, отправьте ссылку на автора. Если у вас её нет, то отправьте любой текст\n\n'
            '<i>Так же вы можете воспользоваться одной из кнопок ниже чтобы найти автора по изображению, найти серию и таймметку аниме по кадру из него, проверить вероятность использования ИИ для создания изображения, распознать текст или растение. либо поговорить с ботом</i>\n\n',
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

        user_data[user_id] = {'status': 'awaiting_artist_link'}
        return ASKING_FOR_ARTIST_LINK

    # Проверяем, если бот в режиме поиска
    if is_search_mode.get(user_id, False):
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
            image_path = 'temp_image.jpg'
        elif update.message.document and update.message.document.mime_type.startswith('image/'):
            file = await update.message.document.get_file()
            image_path = 'temp_image.jpg'
        else:
            await update.message.reply_text("Пожалуйста, отправьте изображение для поиска.")
            return ASKING_FOR_FILE

        await file.download_to_drive(image_path)

        # Отправляем первоначальное сообщение о загрузке файла
        loading_message = await update.message.reply_text("Загрузка файла на хостинг...")

        # Загружаем изображение на Catbox
        img_url = await upload_catbox(image_path)
        context.user_data['img_url'] = img_url 

        # Обновляем сообщение о статусе загрузки
        await loading_message.edit_text("Файл успешно загружен! Ожидание ответа от SauceNAO...обычно это занимает до 5 секунд")

        # Создаем URL для поиска
        search_url = f"https://saucenao.com/search.php?db=999&url={img_url}"
        yandex_search_url = f"https://yandex.ru/images/search?source=collections&rpt=imageview&url={img_url}"
        google_search_url = f"https://lens.google.com/uploadbyurl?url={img_url}"
        bing_search_url = f"https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIVSP&sbisrc=UrlPaste&q=imgurl:{img_url}"

        keyboard = [
            [InlineKeyboardButton("АИ или нет?", callback_data='ai_or_not')],
            [InlineKeyboardButton("Все результаты на SauceNAO", url=search_url)],
            [InlineKeyboardButton("Поиск через Yandex Images", url=yandex_search_url)],
            [InlineKeyboardButton("Поиск через Google Images", url=google_search_url)],
            [InlineKeyboardButton("Поиск через Bing Images", url=bing_search_url)],
            [InlineKeyboardButton("Завершить поиск", callback_data='finish_search')],
            [InlineKeyboardButton("‼️Полный Сброс Бота‼️", callback_data='restart')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)        

        try:
            # Получаем авторов и ссылки через SauceNAO
            authors_text, external_links, jp_name, details_text, ep_name, ep_time, dA_id, full_author_text, pixiv_id, twitter_id = await search_image_saucenao(image_path)
        except Exception as e:
            # Обработка ошибок, например, превышение лимита запросов
            if str(e) == "Лимит превышен":
                await loading_message.edit_text("Лимит запросов к SauceNAO у бота на сегодня исчерпан. Всего их 100 запросов на всех пользователей бота в сутки. Попробуйте через пару часов, либо воспользуйтесь одной из кнопок ниже чтобы поискать источники самостоятельно.", reply_markup=reply_markup)
            else:
                await loading_message.edit_text(f"Произошла ошибка при обращении к SauceNAO: {str(e)}", reply_markup=reply_markup)
            os.remove(image_path)
            return ASKING_FOR_FILE

        os.remove(image_path)

        # Подготовка ссылок в удобном для чтения формате
        links_text = "\n".join(f"{i + 1}. {link}" for i, link in enumerate(external_links)) if isinstance(external_links, list) else None
        
        # Формируем сообщение
        reply_text = "Результаты поиска:\n"
        if authors_text:
            reply_text += f"Название: {authors_text}\n"
        if details_text:
            reply_text += f"Детали: {details_text}\n\n"
        if jp_name:
            reply_text += f"JP Название: {jp_name}\n"
        if ep_name:
            reply_text += f"{ep_name}\n"
        if dA_id:
            reply_text += f"dA ID: {dA_id}\n"
        if twitter_id:
            reply_text += f"Твиттер:\n{twitter_id}"               
        if pixiv_id:
            reply_text += f"Pixiv: {pixiv_id}\n"
        if full_author_text:
            reply_text += f"Автор: {full_author_text}\n"
        if ep_time:
            reply_text += f"{ep_time}\n\n"
        if links_text:
            reply_text += f"Ссылки:\n{links_text}"



        # Если нет данных, отправляем сообщение о том, что ничего не найдено
        if not authors_text and not links_text:
            reply_text = (
                "К сожалению, ничего не найдено. "
                "Возможно изображение сгенерировано, возможно автор малоизвестен или изображение слишком свежее. Отправьте другое изображение или завершите поиск"
            )

        # Обновляем сообщение результатами поиска с кнопками
        await loading_message.edit_text(reply_text.strip(), reply_markup=reply_markup)

        return ASKING_FOR_FILE



    # Проверяем, если бот в режиме ocr
    if is_ocr_mode.get(user_id, False):
        # Проверяем, отправил ли пользователь фото или документ
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
            image_path = 'temp_image.jpg'
        elif update.message.document and update.message.document.mime_type.startswith('image/'):
            file = await update.message.document.get_file()
            image_path = 'temp_image.jpg'
        else:
            keyboard = [
                [InlineKeyboardButton("Отменить режим распознавания", callback_data='finish_ocr')],
                [InlineKeyboardButton("‼️Полный Сброс Бота‼️", callback_data='restart')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "Пожалуйста, отправьте изображение для распознавания",
                reply_markup=reply_markup
            )
            return ASKING_FOR_OCR

        # Загружаем файл и отправляем сообщение о процессе
        loading_message = await update.message.reply_text("Загрузка изображения...")

        await file.download_to_drive(image_path)

        # Загружаем изображение на Catbox и обновляем сообщение
        await loading_message.edit_text("Изображение загружается на Catbox...")
        img_url = await second_upload_image(image_path)
        inat_url = "https://www.inaturalist.org/computer_vision_demo"

        context.user_data['img_url'] = img_url

        # Формируем клавиатуру с кнопками для распознавания
        keyboard = [
            [InlineKeyboardButton("📃Распознать текст📃", callback_data='recognize_text')],
            [InlineKeyboardButton("🖼️Распознать текст через GPT🖼️", callback_data='text_rec_with_gpt')],  # Новая кнопка            
            [InlineKeyboardButton("🌸Распознать растение🌸", callback_data='recognize_plant')],
            [InlineKeyboardButton("Распознать на iNaturalist", url=inat_url)],
            [InlineKeyboardButton("Отменить режим распознавания", callback_data='finish_ocr')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Обновляем сообщение с кнопками после успешной загрузки
        await loading_message.edit_text(
            "Изображение успешно загружено! Что именно вы желаете распознать? Обычно обработка запроса занимает до 10-15 секунд. Распознавание через  GPT поддерживает текст написанный от руки, но читаемым почерком",
            reply_markup=reply_markup
        )

        return ASKING_FOR_OCR


    # Проверяем, если бот в режиме GPT
    if is_gpt_mode.get(user_id, False):
        return await gpt_running(update, context)  # Вызываем функцию gpt_running

    # Основная логика для работы с изображениями
    if update.message:
        message_to_reply = update.message

        # Проверяем состояние пользователя
        if user_data.get(user_id, {}).get('status') == 'awaiting_artist_link':
            # Проверка, если пользователь отправил изображение как файл (document)
            if update.message.document and update.message.document.mime_type.startswith('image/'):
                caption = update.message.caption.strip() if update.message.caption else ''
                parts = caption.split(maxsplit=1)
                if len(parts) > 0:
                    artist_link = parts[0]  # Первая часть - это ссылка
                    author_name = parts[1] if len(parts) > 1 else ''  # Остальная часть - это текст

                    # Сохраняем имя автора как заголовок статьи напрямую
                    user_data[user_id] = {
                        'status': 'awaiting_image',
                        'artist_link': artist_link,
                        'author_name': author_name,
                        'title': author_name,  # Используем как заголовок
                        'media': [],
                        'image_counter': 0,
                    }
                    # Обработка изображения
                    await handle_image(update, context)

                    # Вызов команды /publish после обработки изображения
                    await publish(update, context)

                    # Завершение процесса для данного пользователя
                    if user_id in user_data:
                        del user_data[user_id]  # Очистка данных пользователя, если нужно
                    else:
                        logger.warning(f"Попытка удалить несуществующий ключ: {user_id}") # Очистка данных пользователя, если нужно

            # Проверка, если пользователь отправил изображение как фото (photo)
            elif update.message.photo:
                await message_to_reply.reply_text(
                    "Пожалуйста отправьте файл документом /restart"
                )
                return ConversationHandler.END

    # Проверка, если событие пришло от callback_query
    elif update.callback_query:
        message_to_reply = update.callback_query.message
    else:
        return ConversationHandler.END

    # Обработка состояний пользователя
    status = user_data[user_id].get('status')
    if status == 'awaiting_artist_link':
        return await handle_artist_link(update, context)
    elif status == 'awaiting_author_name':
        return await handle_author_name(update, context)
    elif status == 'awaiting_image':
        return await handle_image(update, context)
    else:
        await message_to_reply.reply_text('🚫Ошибка: некорректное состояние.')

        return ConversationHandler.END

async def run_gpt(update: Update, context: CallbackContext) -> int:
    if update.message:
        user_id = update.message.from_user.id  # Когда вызвано командой /search
        message_to_reply = update.message
    elif update.callback_query:
        user_id = update.callback_query.from_user.id  # Когда нажата кнопка "Начать поиск"
        message_to_reply = update.callback_query.message
        
        # Убираем индикатор загрузки на кнопке
        await update.callback_query.answer()

    # Устанавливаем флаг режима GPT и сбрасываем другие режимы
    is_gpt_mode[user_id] = True
    is_search_mode[user_id] = False
    is_ocr_mode[user_id] = False
    keyboard = [
        [InlineKeyboardButton("Выйти из режима диалога", callback_data='stop_gpt')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    # Отправляем сообщение о начале режима общения с GPT
    await message_to_reply.reply_text("Режим общения с GPT активирован. Отправьте ваше сообщение для ответа.",  
            reply_markup=reply_markup  # Добавляем кнопки
        )
    
    return RUNNING_GPT_MODE

async def stop_gpt(update: Update, context: CallbackContext) -> int:
    # Проверяем, был ли вызов через кнопку или команду
    if update.callback_query:
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()
        await query.message.reply_text(
            "Режим общения с GPT отключен. Вы вернулись к основному режиму.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎨 Найти автора или проверить на ИИ 🎨", callback_data='start_search')],
                [InlineKeyboardButton("🌱 Распознать (Растение или текст) 🌱", callback_data='start_ocr')], 
                [InlineKeyboardButton("‼️Полный Сброс Бота‼️", callback_data='restart')]
            ])
        )
    else:
        # Если вызов произошел через команду
        user_id = update.message.from_user.id
        await update.message.reply_text(
            "Режим общения с GPT отключен. Вы вернулись к основному режиму.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎨 Найти автора или проверить на ИИ 🎨", callback_data='start_search')],
                [InlineKeyboardButton("🌱 Распознать (Растение или текст) 🌱", callback_data='start_ocr')], 
                [InlineKeyboardButton("‼️Полный Сброс Бота‼️", callback_data='restart')]
            ])
        )

    is_gpt_mode[user_id] = False  # Отключаем режим GPT для пользователя
    return ConversationHandler.END




import re
import logging

async def send_reply_with_limit(update, text, reply_markup=None):
    MAX_MESSAGE_LENGTH = 4096
    # Разбиваем текст на части, если он превышает максимальную длину
    text_parts = [text[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(text), MAX_MESSAGE_LENGTH)]
    
    for part in text_parts:
        # Логируем текст перед отправкой
        logging.info(f"Отправка текста в Telegram: {part[:200]}...")  # Логируем первые 200 символов
        # Убираем обратные слэши перед специальными символами Markdown V2
        part = escape_gpt_markdown_v2(part)
        await update.message.reply_text(part, reply_markup=reply_markup, parse_mode='MarkdownV2')



def escape_gpt_markdown_v2(text):
    # Проверка на наличие экранирования и удаление, если оно присутствует
    if re.search(r'\\[\\\*\[\]\(\)\{\}\.\!\?\-\#\@\&\$\%\^\&\+\=\~]', text):
        # Убираем экранирование у всех специальных символов Markdown
        text = re.sub(r'\\([\\\*\[\]\(\)\{\}\.\!\?\-\#\@\&\$\%\^\&\+\=\~])', r'\1', text)

    # Временная замена ** на |TEMP| без экранирования
    text = re.sub(r'\*\*(.*?)\*\*', r'|TEMP|\1|TEMP|', text)

    # Временная замена ``` на |CODE_BLOCK| для исключения из экранирования
    text = text.replace('```', '|CODE_BLOCK|')

    # Временная замена ` на |INLINE_CODE| для исключения из экранирования
    text = text.replace('`', '|INLINE_CODE|')

    # Экранируем все специальные символы
    text = re.sub(r'(?<!\\)([\\\*\[\]\(\)\{\}\.\_\!\|\?\-\#\@\&\$\%\^\&\+\=\~])', r'\\\1', text)

    # Восстанавливаем |TEMP| обратно на *
    text = text.replace('|TEMP|', '*')

    # Восстанавливаем |CODE_BLOCK| обратно на ```
    text = text.replace('|CODE_BLOCK|', '```')

    # Восстанавливаем |INLINE_CODE| обратно на `
    text = text.replace('|INLINE_CODE|', '`')

    return text

async def gpt_running(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    user_message = update.message.text
    user_image = None

    reset_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("Сбросить диалог", callback_data='reset_dialog')],
        [InlineKeyboardButton("Выйти из режима диалога", callback_data='stop_gpt')]
    ])

    # Проверка, отправил ли пользователь текстовый файл
    if update.message.document:
        file_name = update.message.document.file_name
        file_extension = file_name.split('.')[-1].lower()
        caption_text = update.message.caption or "Текст из файла"

        if file_extension in ["txt", "pdf"]:
            try:
                # Скачиваем файл
                file = await update.message.document.get_file()
                file_data = io.BytesIO()
                await file.download_to_memory(out=file_data)

                # Извлекаем текст в зависимости от типа файла
                if file_extension == "txt":
                    file_data.seek(0)
                    raw_data = file_data.read()
                    result = chardet.detect(raw_data)  # Определяем кодировку
                    encoding = result['encoding']
                    file_data.seek(0)  # Возвращаемся в начало файла

                    try:
                        file_text = raw_data.decode(encoding)  # Декодируем текст
                    except UnicodeDecodeError as e:
                        logging.error(f"Ошибка декодирования файла: {e}")
                        await update.message.reply_text("Ошибка при декодировании текстового файла. Попробуйте другой файл.")
                        return RUNNING_GPT_MODE

                elif file_extension == "pdf":
                    file_text = extract_text_from_pdf(file_data)

                # Логируем текст перед отправкой
                logging.info(f"Текст из файла: {file_text[:200]}")  # Логирование первых 200 символов

                # Генерация ответа с текстом из файла
                response_text = generate_gemini_response(user_id, query=caption_text, text=file_text)

                if response_text:
                    await send_reply_with_limit(update, response_text, reply_markup=reset_button)
                else:
                    await update.message.reply_text("Произошла ошибка при генерации ответа. Попробуйте снова.")

                return RUNNING_GPT_MODE  # Возвращаем состояние

            except Exception as e:
                logging.error(f"Ошибка при обработке текстового файла: {e}")
                await update.message.reply_text("Ошибка при обработке текстового файла. Попробуйте снова.")
                return RUNNING_GPT_MODE
        else:
            await update.message.reply_text("Поддерживаются только текстовые файлы в формате .txt и .pdf.")
            return RUNNING_GPT_MODE

    # Проверка, отправил ли пользователь изображение
    if update.message.photo:
        try:
            photo_file = await update.message.photo[-1].get_file()
            img_data = io.BytesIO()
            await photo_file.download_to_memory(out=img_data)
            img = Image.open(img_data)

            # Получение текстового описания от пользователя
            user_message = update.message.caption or "Изображение без описания"
            add_to_context(user_id, f"[Изображение] {user_message}", message_type="image_description")

            # Генерация ответа с изображением
            response_text = generate_gemini_response(user_id, query=user_message, image=img)

            # Логируем ответ перед отправкой
            logging.info(f"Ответ с изображением, который пытается отправить бот: {response_text}")

            # Добавление ответа в контекст и отправка пользователю
            if response_text:
                add_to_context(user_id, response_text, message_type="bot_response")
                clean_response = get_clean_response_text(response_text)
                await send_reply_with_limit(update, clean_response, reply_markup=reset_button)
            else:
                await update.message.reply_text("Произошла ошибка при генерации ответа. Попробуйте снова.")

            return RUNNING_GPT_MODE  # <-- Добавьте return здесь

        except Exception as e:
            logging.error(f"Ошибка при загрузке изображения: {e}")
            await update.message.reply_text("Ошибка при обработке изображения. Попробуйте снова.")
            return RUNNING_GPT_MODE
    else:
        # Обработка текстового запроса без изображения
        add_to_context(user_id, user_message, message_type="user_message")
        response_text = generate_gemini_response(user_id, query=user_message)
        
        # Логируем ответ перед отправкой
        logging.info(f"Текстовый ответ, который пытается отправить бот: {response_text}")
        
        # Добавление ответа в контекст и отправка пользователю
        if response_text:
            add_to_context(user_id, response_text, message_type="bot_response")
            
            # Убираем метку времени и тип сообщения для отображения пользователю
            clean_response = get_clean_response_text(response_text)
            await send_reply_with_limit(update, clean_response, reply_markup=reset_button)
        else:
            await update.message.reply_text("Произошла ошибка при генерации ответа. Попробуйте снова.")

    return RUNNING_GPT_MODE

# Функция для обработки нажатия кнопки "Сбросить диалог"
async def reset_dialog(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    user_contexts[user_id] = deque(maxlen=700)  # Очищаем контекст пользователя
    await query.answer("Диалог сброшен.")
    keyboard = [
        [InlineKeyboardButton("Выйти из режима диалога", callback_data='stop_gpt')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Диалог сброшен. Вы можете начать новый разговор.", reply_markup=reply_markup)






async def text_rec_with_gpt(update, context):
    user_id = update.effective_user.id
    img_url = context.user_data.get('img_url')

    # Проверяем наличие изображения в контексте
    if not img_url:
        await update.callback_query.answer("Изображение не найдено.")
        return

    try:
        # Открываем файл temp_image.jpg для обработки
        file = open('temp_image.jpg', 'rb')
        
        # Загружаем изображение как объект PIL.Image
        image = Image.open(file)
        image.load()  # Загружаем изображение полностью
        
        # Запрос для Gemini с указанием распознавания текста
        query = "Распознай текст на данном изображении. В ответ пришли только распознанный текст либо если распознать не вышло то сообщи об этом"
        
        # Генерация ответа через Gemini
        response = generate_gemini_response(user_id, query=query, image=image, use_context=False)
        
        # Закрываем файл после использования
        file.close()
        
        # Проверяем и отправляем ответ пользователю
        await update.callback_query.message.reply_text(response or "Ошибка при распознавании текста.")
    
    except Exception:
        await update.callback_query.message.reply_text("Произошла ошибка при обработке изображения.")




import re
from bs4 import BeautifulSoup
import aiohttp


async def search_image_saucenao(image_path: str):
    url = 'https://saucenao.com/search.php'
    params = {
        'api_key': 'd3d3b527510c50ca559d38901614b0da7c86db75',
        'output_type': 0,
        'numres': 10,
        'db': 999,
    }

    async with aiohttp.ClientSession() as session:
        with open(image_path, 'rb') as image_file:
            files = {'file': image_file}

            async with session.post(url, params=params, data=files) as response:
                # Проверка на превышение лимита
                if response.status == 429:
                    html_content = await response.text()
                    if "Daily Search Limit Exceeded" in html_content:
                        raise Exception("Лимит превышен")  # Бросаем исключение, если превышен лимит
                    else:
                        logging.error("Ошибка 429: неизвестная причина")
                        return None, [], None, None, None, None, None, None, None
                
                # Проверка успешного ответа
                if response.status == 200:
                    html_content = await response.text()
                    soup = BeautifulSoup(html_content, 'html.parser')

                    # Находим все блоки результатов
                    result_blocks = soup.find_all('td', class_='resulttablecontent')
                    results = []

                    # Проверяем, до какого места мы можем обрабатывать результаты
                    for block in result_blocks:
                        if block.find_parent(class_='result', id='result-hidden-notification'):
                            break

                        similarity_info = block.find('div', class_='resultsimilarityinfo')
                        if similarity_info:
                            similarity_percentage = float(similarity_info.text.replace('%', '').strip())
                            
                            if similarity_percentage >= 60:
                                results.append((similarity_percentage, block))

                    # Инициализируем переменные
                    authors_text = None
                    links = []
                    jp_name = None
                    details_text = None
                    ep_name = None
                    ep_time = None
                    dA_id = None
                    full_author_text = None
                    pixiv_id = None
                    twitter_id = None

                    if results:
                        results.sort(key=lambda x: x[0], reverse=True)
                        best_match = results[0][1]

                        result_title_div = best_match.find('div', class_='resulttitle')
                        authors_parts = []
                        details_parts = []

                        if result_title_div:
                            for elem in result_title_div.children:
                                if elem.name == "strong" and 'subtext' not in elem.get("class", []):
                                    authors_text = elem.text.strip()
                                    break
                                elif elem.name == "small":
                                    details_parts.append(elem.text.strip())
                            
                            if not authors_text:
                                authors_text = " ".join(authors_parts).replace("  ", " ").strip()
                            details_text = result_title_div.get_text(separator="\n", strip=True)
                            details_text = "\n".join(details_text.splitlines()[1:]).strip()

                        result_content_div = best_match.find('div', class_='resultcontentcolumn')

                        if result_content_div:
                            ep_name = ""
                            ep_time = None

                            ep_span = result_content_div.find('span', class_='subtext', string="EP")
                            if ep_span:
                                ep_name = ep_span.find_next('strong').next_sibling.strip()
                                ep_name = f"Название эпизода: {ep_name}"

                            subtext_spans = result_content_div.find_all('span', class_='subtext')
                            for span in subtext_spans:
                                if "Est Time:" in span.get_text():
                                    ep_time = span.get_text().replace("Est Time:", "").strip()
                                    ep_time = f"Таймметка скриншота в эпизоде: {ep_time}"
                                    break

                            dA_id_link = result_content_div.find('a', href=True)
                            if dA_id_link and "deviantart" in dA_id_link['href']:
                                dA_id = dA_id_link['href']
                            pixiv_id_link = result_content_div.find('a', href=True)
                            if pixiv_id_link and "pixiv" in pixiv_id_link['href']:
                                pixiv_id = pixiv_id_link['href']   
                            twitter_id_link = result_content_div.find('a', href=True)
                            if twitter_id_link and "twitter.com" in twitter_id_link['href']: 
                                twitter_id = twitter_id_link['href']  # Формируем строку в нужном формате
                            else:
                                twitter_id = None                                              

                            full_author_text = ""
                            author_tag = result_content_div.find('strong', string=lambda text: text.strip() == "Author:")
                            if author_tag:
                                author_link_tag = author_tag.find_next('a', class_='linkify')
                                if author_link_tag:
                                    author_name = author_link_tag.text.strip()
                                    author_url = author_link_tag['href']
                                    full_author_text = f"{author_name} - {author_url}"

                            result_miscinfo_div = best_match.find('div', class_='resultmiscinfo')
                            external_links = [a['href'] for a in result_miscinfo_div.find_all('a', href=True)] if result_miscinfo_div else []

                            jp_name_div = result_content_div.find('span', class_='subtext', string="JP")
                            jp_name = jp_name_div.find_next_sibling(text=True).strip() if jp_name_div else None

                        return authors_text, external_links, jp_name, details_text, ep_name, ep_time, dA_id, full_author_text, pixiv_id, twitter_id
                    else:
                        return None, [], None, None, None, None, None, None, None, None
                else:
                    logging.error(f"Ошибка {response.status}: {await response.text()}")
                    return None, [], None, None, None, None, None, None, None



async def second_upload_image(file_path: str) -> str:
    try:
        # Попытка загрузки на Catbox с таймаутом 5 секунд
        return await asyncio.wait_for(upload_catbox(file_path), timeout=5)
    except asyncio.TimeoutError:
        print("Таймаут при загрузке на Catbox. Переход к FreeImage.")
        return await upload_free_image(file_path)
    except Exception as e:
        print(f"Ошибка при загрузке на Catbox: {e}")
        return await upload_free_image(file_path)

async def upload_catbox(file_path: str) -> str:
    async with aiohttp.ClientSession() as session:
        with open(file_path, 'rb') as f:
            form = aiohttp.FormData()
            form.add_field('reqtype', 'fileupload')
            form.add_field('fileToUpload', f)
            form.add_field('userhash', '1f68d2a125c66f6ab79a4f89c')  # Замените на ваш реальный userhash

            async with session.post('https://catbox.moe/user/api.php', data=form) as response:
                if response.status == 200:
                    return await response.text()  # возвращает URL загруженного файла
                else:
                    raise Exception(f"Ошибка загрузки на Catbox: {response.status}")

async def upload_free_image(file_path: str) -> str:
    async with aiohttp.ClientSession() as session:
        with open(file_path, 'rb') as f:  # Открываем файл заново
            form = aiohttp.FormData()
            form.add_field('key', '6d207e02198a847aa98d0a2a901485a5')  # Ваш API ключ для freeimage.host
            form.add_field('action', 'upload')
            form.add_field('source', f)  # Используем файл для загрузки

            async with session.post('https://freeimage.host/api/1/upload', data=form) as free_image_response:
                if free_image_response.status == 200:
                    response_json = await free_image_response.json()
                    return response_json['image']['url']  # Проверьте правильность пути к URL в ответе
                else:
                    raise Exception(f"Ошибка загрузки на Free Image Hosting: {free_image_response.status}")



async def parse_yandex_results(img_url):
    search_url = f"https://yandex.ru/images/search?source=collections&rpt=imageview&url={img_url}"
    response = requests.get(search_url)
    soup = BeautifulSoup(response.text, 'lxml')
    
    similar_images = soup.find_all('li', class_='cbir-similar__thumb')
    result_links = []
    for i in similar_images:
        result_links.append(f"https://yandex.ru{i.find('a').get('href')}")
    
    return result_links


async def ai_or_not(update: Update, context: CallbackContext):
    img_url = context.user_data.get('img_url')

    if img_url is None:
        await update.callback_query.answer("Не удалось найти URL изображения.")
        return

    api_user = '1334786424'  # Ваш api_user
    api_secret = 'HaC88eFy4NLhyo86Md9aTKkkKaQyZeEU'  # Ваш api_secret

    params = {
        'url': img_url,
        'models': 'genai',
        'api_user': api_user,
        'api_secret': api_secret
    }

    async with aiohttp.ClientSession() as session:
        for attempt in range(5):  # Пять попыток
            async with session.get('https://api.sightengine.com/1.0/check.json', params=params) as response:
                if response.status == 200:
                    output = await response.json()
                    ai_generated_score = output['type']['ai_generated']

                    keyboard = [
                        [InlineKeyboardButton("Sightengine", url="https://sightengine.com/detect-ai-generated-images")],
                        [InlineKeyboardButton("Illuminarty AI", url="https://app.illuminarty.ai/#/")]
                    ]

                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await update.callback_query.answer()
                    await update.callback_query.message.reply_text(
                        f"Изображение сгенерировано АИ с вероятностью: {ai_generated_score * 100:.2f}% \n\n Вы можете прислать другое изображение для проверки, либо проверить самостоятельно на следующих ресурсах:",
                        reply_markup=reply_markup
                    )

                    return
                elif response.status == 429:
                    await asyncio.sleep(5)  # Ждем 5 секунд перед следующей попыткой
                else:
                    error_message = await response.text()
                    await update.callback_query.answer("Ошибка при обращении к API Sightengine.")
                    print(f"Ошибка API: {response.status} - {error_message}")
                    return

    await update.callback_query.answer("Не удалось обработать изображение после нескольких попыток.")





async def start_search(update: Update, context: CallbackContext) -> int:
    if update.message:
        user_id = update.message.from_user.id  # Когда вызвано командой /search
        message_to_reply = update.message
    elif update.callback_query:
        user_id = update.callback_query.from_user.id  # Когда нажата кнопка "Начать поиск"
        message_to_reply = update.callback_query.message
        await update.callback_query.answer()

    # Устанавливаем флаг для режима поиска и сбрасываем другие флаги
    is_search_mode[user_id] = True
    is_gpt_mode[user_id] = False
    is_ocr_mode[user_id] = False

    # Создаем кнопку "Отменить поиск"
    keyboard = [
        [InlineKeyboardButton("Отменить поиск", callback_data='finish_search')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправляем сообщение с кнопкой
    await message_to_reply.reply_text(
        "Пожалуйста, отправьте изображение для поиска источника или для проверки, сгенерировано ли оно нейросетью.",
        reply_markup=reply_markup
    )
    
    return ASKING_FOR_FILE

async def handle_file(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id

    # Проверка, если пользователь находится в режиме поиска
    if user_id in is_search_mode and is_search_mode[user_id]:
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
            image_path = 'temp_image.jpg'
            await file.download_to_drive(image_path)
            # Здесь логика для поиска по изображению
            return ASKING_FOR_FILE
        elif update.message.document:
            if update.message.document.mime_type.startswith('image/'):
                file = await update.message.document.get_file()
                image_path = 'temp_image.jpg'
                await file.download_to_drive(image_path)
                # Логика для обработки документов
                return ASKING_FOR_FILE
            else:
                await update.message.reply_text("Пожалуйста, отправьте изображение для поиска источников.")
                return ASKING_FOR_FILE
        else:
            await update.message.reply_text("Пожалуйста, отправьте изображение для поиска источников.")
            return ASKING_FOR_FILE
    
    # Проверка, если пользователь находится в режиме OCR
    if user_id in is_ocr_mode and is_ocr_mode[user_id]:
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
            image_path = 'temp_image.jpg'
            await file.download_to_drive(image_path)
            # Логика для OCR-обработки
            return ASKING_FOR_OCR
        elif update.message.document:
            if update.message.document.mime_type.startswith('image/'):
                file = await update.message.document.get_file()
                image_path = 'temp_image.jpg'
                await file.download_to_drive(image_path)
                return ASKING_FOR_OCR
            else:
                await update.message.reply_text("Пожалуйста, отправьте изображение для OCR.")
                return ASKING_FOR_OCR
        else:
            await update.message.reply_text("Пожалуйста, отправьте изображение для OCR.")
            return ASKING_FOR_OCR


    if user_id in is_gpt_mode and is_gpt_mode[user_id]:
        if update.message.text:
            # Обрабатываем текст сообщения через GPT
            user_message = update.message.text
            response = generate_gemini_response(user_id, query=user_message)
            await update.message.reply_text(response)
            return RUNNING_GPT_MODE
        elif update.message.photo or update.message.document:
            await update.message.reply_text("В режиме GPT поддерживается только текстовый ввод.")
            return RUNNING_GPT_MODE            

    # Если пользователь отправил команду /restart, сбрасываем состояние
    if update.message.text == "/restart":
        return await restart(update, context)

    await update.message.reply_text("Пожалуйста, отправьте файл документом или изображение.")
    return ASKING_FOR_FILE

async def finish_search(update: Update, context: CallbackContext) -> int:
    # Проверяем, вызвана ли функция через кнопку или командой
    if update.callback_query:
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()  # Отвечаем на запрос, чтобы убрать индикатор загрузки на кнопке
        await query.edit_message_text(
            "Вы вышли из режима поиска и вернулись к основным функциям бота",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎨 Найти автора или проверить на ИИ 🎨", callback_data='start_search')],
                [InlineKeyboardButton("🌱 Распознать (Растение или текст) 🌱", callback_data='start_ocr')],
                [InlineKeyboardButton("🦊 Поговорить с ботом 🦊", callback_data='run_gpt')],
                [InlineKeyboardButton("‼️ Полный сброс процесса ‼️", callback_data='restart')]
            ])
        )
    else:
        # Если вызов произошел через команду
        user_id = update.message.from_user.id
        await update.message.reply_text(
            "Вы вышли из режима поиска и вернулись к основным функциям бота",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎨 Найти автора или проверить на ИИ 🎨", callback_data='start_search')],
                [InlineKeyboardButton("🌱 Распознать (Растение или текст) 🌱", callback_data='start_ocr')],
                [InlineKeyboardButton("🦊 Поговорить с ботом 🦊", callback_data='run_gpt')],
                [InlineKeyboardButton("‼️ Полный сброс процесса ‼️", callback_data='restart')]
            ])
        )

    is_search_mode[user_id] = False  # Выключаем режим поиска
    return ConversationHandler.END

# Основная логика обработчика сообщений
async def main_logic(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id

    # Если пользователь находится в режиме поиска, игнорируем основную логику
    if is_search_mode.get(user_id, False):
        return

    # Если пользователь находится в режиме OCR, игнорируем основную логику
    if is_ocr_mode.get(user_id, False):
        return ASKING_FOR_OCR

    if is_gpt_mode.get(user_id, False):
        return RUNNING_GPT_MODE        

    # Основная логика обработки сообщений
    await update.message.reply_text("Обрабатываем сообщение в основной логике.")
    return ConversationHandler.END

# Добавим функцию для обработки неизвестных сообщений в режиме поиска
async def unknown_search_message(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Пожалуйста, отправьте фото или документ.")
    return ASKING_FOR_FILE

async def restart(update: Update, context: CallbackContext) -> int:
    # Проверка типа события
    if update.message:
        user_id = update.message.from_user.id
        message_to_reply = update.message
    elif update.callback_query:
        user_id = update.callback_query.from_user.id
        message_to_reply = update.callback_query.message
    else:
        return ConversationHandler.END

    # Удаляем все данные пользователя
    if user_id in user_data:
        del user_data[user_id]  # Удаляем старые данные пользователя  

    if user_id in is_search_mode:
        del is_search_mode[user_id]  # Выключаем режим поиска, если он включен

    if user_id in is_ocr_mode:
        del is_ocr_mode[user_id]

    if user_id in is_gpt_mode:
        del is_gpt_mode[user_id]

    logger.info(f"User {user_id} restarted the process.") 

    # Отправляем сообщение с кнопками
    keyboard = [
        [InlineKeyboardButton("🎨 Найти автора или проверить на ИИ 🎨", callback_data='start_search')],
        [InlineKeyboardButton("🌱 Распознать (Растение или текст) 🌱", callback_data='start_ocr')],
        [InlineKeyboardButton("🦊 Поговорить с ботом 🦊", callback_data='run_gpt')]

    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await message_to_reply.reply_text(
        '✅Бот успешно перезапущен.\n\n'
        '🌠Этот бот поможет вам создать пост для группы Anemone. Изначально пост будет виден исключительно вам, так что не бойтесь экспериментировать и смотреть что получится\n\n'
        'Для начала, пожалуйста, отправьте ссылку на автора. Если у вас её нет, то отправьте любой текст\n\n'
        '<i>Так же вы можете воспользоваться одной из кнопок ниже чтобы найти автора по изображению, найти серию и таймметку аниме по кадру из него, проверить вероятность использования ИИ для создания изображения, распознать текст или растение. либо поговорить с ботом</i>\n\n',
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

    # Устанавливаем новое состояние после перезапуска
    user_data[user_id] = {'status': 'awaiting_artist_link'}
    
    return ASKING_FOR_ARTIST_LINK

async def start_ocr(update: Update, context: CallbackContext) -> int:
    if update.message:
        user_id = update.message.from_user.id  # Когда вызвано командой /search
        message_to_reply = update.message
    elif update.callback_query:
        user_id = update.callback_query.from_user.id  # Когда нажата кнопка "Начать поиск"
        message_to_reply = update.callback_query.message
        await update.callback_query.answer()    

    is_ocr_mode[user_id] = True    
    is_search_mode[user_id] = False
    is_gpt_mode[user_id] = False
  # Устанавливаем флаг для пользователя в режим поиска

    # Создаем кнопку "Отменить поиск"
    keyboard = [
        [InlineKeyboardButton("Отменить режим распознавания", callback_data='finish_ocr')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправляем сообщение с кнопкой
    await message_to_reply.reply_text(
        "Пожалуйста, отправьте изображение для поиска или распознавания. Лучше отправлять сжатые изображения, тогда бот работает быстрее. Оригиналы в виде файлов отправляйте только по необходимости (мелкий текст, мелкие растения и тд)",
        reply_markup=reply_markup
    )
    
    return ASKING_FOR_OCR

async def finish_ocr(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton("🎨 Найти автора или проверить на ИИ 🎨", callback_data='start_search')],
        [InlineKeyboardButton("🌱 Распознать (Растение или текст) 🌱", callback_data='start_ocr')],
        [InlineKeyboardButton("🦊 Поговорить с ботом 🦊", callback_data='run_gpt')],
        [InlineKeyboardButton("‼️ Полный сброс процесса ‼️", callback_data='restart')]

    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:  # Если функция вызвана через нажатие кнопки
        query = update.callback_query
        user_id = query.from_user.id
        is_ocr_mode[user_id] = False  # Выключаем режим поиска
        
        await query.answer()  # Отвечаем на запрос, чтобы убрать индикатор загрузки на кнопке
        await query.edit_message_text(
            "Вы вышли из режима распознавания и вернулись к основным функциям бота. Вы можете продолжить заполнять статью на том моменте на котором остановились, либо воспользоваться одной из кнопок:", 
            reply_markup=reply_markup  # Добавляем кнопки
        )
    
    elif update.message:  # Если функция вызвана через команду /fin_ocr
        user_id = update.message.from_user.id
        is_ocr_mode[user_id] = False  # Выключаем режим поиска
        
        await update.message.reply_text(
            "Вы вышли из режима распознавания и вернулись к основным функциям бота. Вы можете продолжить заполнять статью на том моменте на котором остановились, либо воспользоваться одной из кнопок:", 
            reply_markup=reply_markup  # Добавляем кнопки
        )

    return ConversationHandler.END

# Добавим функцию для обработки неизвестных сообщений в режиме поиска
async def unknown_ocr_message(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Пожалуйста, отправьте фото или документ.")
    return ASKING_FOR_OCR

# Обработчик нажатия на кнопку "Распознать текст"
async def ocr_space_with_url(img_url, api_key):
    ocr_url = "https://api.ocr.space/parse/imageurl"

    async with aiohttp.ClientSession() as session:
        params = {
            'apikey': api_key,
            'url': img_url,
            'language': 'rus',  # Указываем язык
            'isOverlayRequired': 'False',  # Нужно ли накладывать текст на изображение
            'detectOrientation': 'True',  # Определять ориентацию текста
            'scale': 'True'  # Масштабировать изображение
        }

        async with session.get(ocr_url, params=params) as response:
            if response.status == 200:
                result = await response.json()
                try:
                    return result["ParsedResults"][0]["ParsedText"]
                except (KeyError, IndexError):
                    return "Текст не был распознан."
            else:
                return f"Ошибка API OCR.space: {response.status}"


# Измененный обработчик кнопки для OCR
async def button_ocr(update, context):
    query = update.callback_query
    await query.answer()

    # Получаем URL изображения с Catbox
    img_url = context.user_data.get('img_url')

    if query.data == 'recognize_text':
        if img_url:
            # Вызов функции для распознавания текста через Google Cloud Vision API с использованием URL
            api_key = 'K86410931988957'  # Ваш ключ API
            recognized_text = await ocr_space_with_url(img_url, api_key)
            keyboard = [
                [InlineKeyboardButton("Отменить режим распознавания", callback_data='finish_ocr')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            # Отправляем распознанный текст пользователю
            await query.message.reply_text(
                f"Распознанный текст:\n{recognized_text}\n\nОтправьте следующее изображение для распознавания либо нажмите кнопку ниже",
                reply_markup=reply_markup  # Добавляем кнопку к последнему сообщению
            )
        else:
            # Отправляем сообщение об ошибке с кнопкой
            await query.message.reply_text(
                "URL изображения не найден. Попробуйте ещё раз.",
                reply_markup=reply_markup  # Добавляем кнопку к этому сообщению
            )

    elif query.data == 'recognize_plant':
        await recognize_plant(update, context)  # Вызов функции для распознавания растения
    else:
        await query.message.reply_text("Неизвестная команда.")

async def recognize_plant(update: Update, context: CallbackContext) -> None:
    user_id = update.callback_query.from_user.id
    img_url = context.user_data.get('img_url')

    if not img_url:
        await update.callback_query.answer("Сначала загрузите изображение.")
        return

    logger.info(f"URL изображения: {img_url}")

    api_key = "2b10C744schFhHigMMjMsDmV"
    project = "all"  
    lang = "ru"   
    include_related_images = "true"  

    # URL-кодирование для изображения
    encoded_image_url = aiohttp.helpers.quote(img_url)

    # Формирование URL для запроса
    api_url = (
        f"https://my-api.plantnet.org/v2/identify/{project}?"
        f"images={encoded_image_url}&"
        f"organs=auto&"
        f"lang={lang}&"
        f"include-related-images={include_related_images}&"
        f"api-key={api_key}"
    )
    
    # Отправляем сообщение с начальным текстом
    initial_message = await update.callback_query.message.reply_text("Запрос принят...")

    async with aiohttp.ClientSession() as session:
        async with session.get(api_url) as response:
            status = response.status
            logger.info(f"Статус ответа: {status}")

            if status == 200:
                prediction = await response.json()
                logger.info(f"Полный ответ от PlantNet: {prediction}")

                if prediction.get('results'):
                    keyboard = []
                    for idx, plant in enumerate(prediction['results'][:3]):
                        species = plant.get('species', {})
                        scientific_name = species.get('scientificNameWithoutAuthor', 'Неизвестное растение')
                        common_names = species.get('commonNames', [])
                        common_name_str = ', '.join(common_names) if common_names else 'Название отсутствует'
                        
                        # Извлекаем процент сходства
                        similarity_score = plant.get('score', 0) * 100  # Предполагаем, что значение score от 0 до 1
                        similarity_text = f"{similarity_score:.2f}%"  # Форматируем до двух знаков после запятой
                        
                        # Сохраняем данные о растениях в context.user_data для обработки нажатий
                        images = plant.get('images', [])
                        context.user_data[f"plant_{idx}"] = {
                            "scientific_name": scientific_name,
                            "common_names": common_name_str,
                            "images": images
                        }

                        logger.info(f"Plant {idx}: {scientific_name}, Images: {context.user_data[f'plant_{idx}']['images']}")

                        # Создаем кнопку для каждого растения с процентом сходства в начале
                        keyboard.append([InlineKeyboardButton(
                            text=f"{similarity_text} - {scientific_name} ({common_name_str})",
                            callback_data=f"plant_{idx}"
                        )])

                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await initial_message.edit_text(
                        "Выберите одно из предложенных растений:",
                        reply_markup=reply_markup
                    )
                else:
                    await initial_message.edit_text("Растение не найдено.")
            else:
                error_message = await response.text()
                logger.error(f"Ошибка от API PlantNet: {error_message}")
                await initial_message.edit_text("Ошибка при распознавании растения. Данное растение в базе не обнаружено, убедитесь что это именно растение, цветок, фрукт или овощь а не что-то иное. Так же можете попробовать сфотографировать под иным ракурсом")






import wikipediaapi  # Импортируем библиотеку

# Инициализация Wikipedia API с User-Agent
user_agent = "MyPlantBot/1.0 sylar1907942@gmail.com)"
wiki_wiki = wikipediaapi.Wikipedia(language='ru', user_agent=user_agent)  


wikipedia.set_lang('ru')  # Установите язык на русский

async def get_wikipedia_link(scientific_name: str, common_names: list) -> tuple:
    try:
        # Выполняем поиск по научному названию
        search_results = wikipedia.search(scientific_name)
        logger.info(f"Search results for '{scientific_name}': {search_results}")

        # Проверяем, есть ли результаты поиска
        if search_results:
            for article_title in search_results:
                # Проверяем, относится ли статья к категории "растения"
                page = wiki_wiki.page(article_title)
                if page.exists():
                    categories = page.categories
                    # Проверяем наличие ключевых категорий
                    if any('растения' in cat.lower() for cat in categories):
                        logger.info(f"Found article '{article_title}' in category 'plants'")
                        # Формируем и возвращаем ссылку на статью
                        return (f"https://ru.wikipedia.org/wiki/{article_title.replace(' ', '_')}", article_title)

        # Если результаты по научному названию не найдены, ищем по общим названиям
        for name in common_names:
            search_results = wikipedia.search(name)
            logger.info(f"Search results for '{name}': {search_results}")
            if search_results:
                for article_title in search_results:
                    # Проверяем, относится ли статья к категории "растения"
                    page = wiki_wiki.page(article_title)
                    if page.exists():
                        categories = page.categories
                        if any('растения' in cat.lower() for cat in categories):
                            logger.info(f"Found article '{article_title}' in category 'plants'")
                            # Формируем и возвращаем ссылку на статью
                            return (f"https://ru.wikipedia.org/wiki/{article_title.replace(' ', '_')}", article_title)
    
    except Exception as e:
        logger.error(f"Error fetching Wikipedia link: {e}")

    # Если ничего не найдено или статья не относится к растениям, возвращаем None
    return (None, None)


import wikipedia

def escape_markdown_v2(text: str) -> str:
    # Экранирование специальных символов в MarkdownV2, включая символ '='
    return re.sub(r'([\_\*\[\]\(\)\~\`\>\#\+\-\.\!\=])', r'\\\1', text)


async def button_more_plants_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    plant_key = query.data  # Получаем callback_data, например 'plant_0'
    
    logger.info(f"Looking for plant data with key: {plant_key}")

    plant_data = context.user_data.get(plant_key)
    if plant_data:
        scientific_name = plant_data['scientific_name']
        common_names = plant_data['common_names']
        context.user_data['scientific_name'] = scientific_name

        if isinstance(common_names, str):
            common_names = [common_names]  # Преобразуем в список, если это строка
        
        wikipedia_link, article_title = await get_wikipedia_link(scientific_name, common_names)

        description = ""
        if wikipedia_link:
            try:
                # Получаем краткое описание статьи по найденному названию статьи
                summary = wikipedia.summary(article_title, sentences=12)
                description += f"{escape_markdown_v2(summary)}\n\n"
            except Exception as e:
                logger.error(f"Error fetching summary for {article_title}: {e}")
                description += "Краткое описание недоступно\n\n"
        else:
            logger.warning(f"No Wikipedia page found for: {scientific_name} or common names")
            description = "\n\nИнформация по данному растению не найдена\n\n"

        images = plant_data.get('images', [])
        logger.info(f"Retrieved plant data: {plant_data}")

        if images:
            media = []  # Список для хранения объектов медиа
            for idx, img in enumerate(images):
                img_url = img['url']['o'] if 'url' in img else None
                if img_url:
                    if idx == 0:
                        # Подготавливаем подпись и добавляем в лог
                        caption = (
                            f"Растение: {escape_markdown_v2(scientific_name)}\n"
                            f"Общие названия: {', '.join(map(escape_markdown_v2, common_names))}\n"
                            f"{truncate_text_with_link(description, 300, wikipedia_link, scientific_name)}"
                        )
                        logger.info(f"Caption for first image: {caption}")
                        media.append(InputMediaPhoto(media=img_url, caption=caption, parse_mode='MarkdownV2'))
                    else:
                        media.append(InputMediaPhoto(media=img_url))

            if media:
                logger.info(f"Media items ready for sending: {media}")
                logger.info(f"Number of media items: {len(media)}")
                
                try:
                    await query.message.reply_media_group(media)  # Отправляем медиагруппу
                    logger.info("Media group sent successfully.")
                except Exception as e:
                    logger.error(f"Error sending media group: {e}")
                    await query.message.reply_text("Ошибка при отправке изображений. Проверьте форматирование текста.")
            else:
                await query.message.reply_text("Изображения не найдены")
        else:
            await query.message.reply_text("Изображений нет")
        
        # Отправляем сообщение с кнопками после медиа
        keyboard = [
            [InlineKeyboardButton("Подробнее об этом растении", callback_data='gpt_plants_more')],         
            [InlineKeyboardButton("Помощь по уходу за этим растением", callback_data='gpt_plants_help')],        
            [InlineKeyboardButton("Отменить режим распознавания", callback_data='finish_ocr')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            "Для получения более подробной информации об этом растении либо об уходе за ним, воспользуйтесь кнопками ниже. Либо отправьте следующее изображение",
            reply_markup=reply_markup  # Добавляем кнопку к этому сообщению
        )
    else:
        await query.message.reply_text("Данные о растении не найдены")
    
    await query.answer()

async def gpt_plants_more_handler(update, context):
    """Асинхронный обработчик для запроса ухода за растением по научному названию."""
    user_id = update.callback_query.from_user.id
    scientific_name = context.user_data.get("scientific_name")
    await update.callback_query.answer()

    if not scientific_name:
        await update.callback_query.answer("Научное название не указано. Попробуйте снова.")
        return

    # Формируем запрос с научным названием
    query = f"Расскажи больше про {scientific_name}, например, интересные факты, способы применения, особенности и прочее.В ответе используй разметку markdown_v2"

    # Генерация ответа без контекста
    response_text = generate_gemini_response(user_id, query=query, use_context=False)
    response_text = limit_response_length(response_text)
    response_text = escape_gpt_markdown_v2(response_text)

    keyboard = [
        [InlineKeyboardButton("Помощь по уходу за этим растением", callback_data='gpt_plants_help')],        
        [InlineKeyboardButton("Отменить режим распознавания", callback_data='finish_ocr')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Редактируем существующее сообщение с новым ответом и кнопками
    await update.callback_query.message.edit_text(response_text, reply_markup=reply_markup, parse_mode='MarkdownV2')


async def gpt_plants_help_handler(update, context):
    """Асинхронный обработчик для запроса ухода за растением по научному названию."""
    user_id = update.callback_query.from_user.id
    scientific_name = context.user_data.get("scientific_name")
    await update.callback_query.answer()

    if not scientific_name:
        await update.callback_query.answer("Научное название не указано. Попробуйте снова.")
        return

    # Формируем запрос с научным названием
    query = f"Как ухаживать за {scientific_name}?В ответе используй разметку markdown_v2"

    # Генерация ответа без контекста
    response_text = generate_gemini_response(user_id, query=query, use_context=False)
    response_text = limit_response_length(response_text)
    response_text = escape_gpt_markdown_v2(response_text)

    keyboard = [
        [InlineKeyboardButton("Подробнее об этом растении", callback_data='gpt_plants_more')],         
        [InlineKeyboardButton("Отменить режим распознавания", callback_data='finish_ocr')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Редактируем существующее сообщение с новым ответом и кнопками
    await update.callback_query.message.edit_text(response_text, reply_markup=reply_markup, parse_mode='MarkdownV2')



def truncate_text_with_link(text: str, max_length: int, link: str, scientific_name: str) -> str:
    """Обрезает текст до max_length символов, добавляет ссылку на статью или Google-поиск."""
    ellipsis = escape_markdown_v2('...')
    
    # Если ссылка на Википедию отсутствует, формируем ссылку на Google-поиск
    if link:
        link_text = f"\n[Узнать больше на википедии]({escape_markdown_v2(link)})"  # Экранируем ссылку на Википедию
    else:
        google_search_link = f"https://www.google.com/search?q={scientific_name.replace(' ', '+')}"
        link_text = f"\n[Найти в Google]({escape_markdown_v2(google_search_link)})"  # Ссылка на Google
    
    # Вычисляем допустимую длину для текста без учета ссылки
    available_length = max_length - len(link_text) - len(ellipsis)

    # Если текст нужно обрезать
    if len(text) > available_length:
        truncated_text = text[:available_length] + ellipsis
    else:
        truncated_text = text

    # Добавляем ссылку в конце
    return truncated_text + link_text







HELP_TEXT = """
▶️Пост в Анемоне формируется из двух частей \- непосредственно сам пост видимый в телеграме \, плюс статья Telagraph доступная по ссылке\(для примера посмотрите любой из последних постов в группе\) Бот позволяет сделать обе части\. \n\n  Изначально статья и всё её содержание видны только вам\, администрации она станет доступна только после того как вы нажмёте сначала кнопку \" К Завершению Публикации \" и затем \(по желанию\) кнопку \/share \(поделиться\)\. Если после публикации вы не захотите вводить команду share то публикация останется видна только вам\n\n ▶️Статья в Telegraph формируется в порядке отправки вами изображений и текста боту\.\n\n Во время создания статьи\, с помощью соответствующих кнопок вы можете\: \n\-открыть предросмотр\n \-удалить последний добавленный элемент \(работает неограниченное количество раз\, пока статья не станет пустой\)\n \-редактировать всё содержимое вашей статьи через список добавленных изображений и текста\. С любым фрагментом можно делать что угодно\, менять текст на изображение и наоборот\, удалять\,  исправлять\, однако только до тех пор пока вы не используете кнопку \" К Завершению Публикации \", послее её нажатия редактировать статью уже будет больше нельзя\, только наполнить новую\. \n\n▶️Поддерживаемые тэги разметки статьи\(без кавычек\)\n \- \"\*\*\*\" — горизонтальная линия\-разделитель \(отправьте три звёздочки отдельным сообщением\, в этом месте в статье телеграф появится разделитель\)\.\n\- \"\_текст\_\" — курсив\.\n\- \"\*текст\*\" — жирный текст\.\n\- \"\[текст ссылки\]\(ссылка\)\" — гиперссылка\.\n\- \"видео\: \" — вставка видео с Vimeo или YouTube\.\n\- \"цитата\:\" — цитата\.\n\- \"цитата по центру\:\" — центрированная цитата\.\n\- "заголовок:" — заголовок\\.\n\\- "подзаголовок:" — подзаголовок\\.\n\n Последние 5 тэгов пишутся в начале сообщения и применяются ко всему сообщению целиком\. Каждое новое сообщение — это новый абзац\. Сообщения без тэгов — обычный текст\.\n\n Пример\: \(без кавычек\)\n\- \"цитата\: \*Волк\* никогда не будет жить в загоне\, но загоны всегда будут жить в \*волке\*\" — в статье телеграф примет вид цитата\, в которой слово \"волк\" выделено жирным\.\n\- \"видео\: ссылка\_на\_видео\" — вставка интерактивного видео YouTube или Vimeo\.\n\n▶️Кроме того бот поддерживает загрузку GIF файлов\. Для этого переименуйте \.GIF в \.RAR \, затем отправьте файл боту во время оформления поста\. Это нужно для того чтобы телеграм не пережимал GIF файлы\, бот автоматически переименует файл обратно в GIF перед размещением в Телеграф\n\n▶️Так же вы можете отправить что\-то администрации напрямую\, в режиме прямой связи\. Для этого введите команду \/send и после неё все ваши сообщения отправленные боту тут же будут пересылаться администрации\. Это могут быть какие\-то пояснения\, дополнительные изображения или их правильное размещение в посте телеграм\, вопросы\, предложения\, ссылка на самостоятельно созданную статью телеграф\, пойманные в боте ошибки и что угодно ещё\. Для завершения этого режима просто введите \/fin и бот вернётся в свой обычный режим\. Просьба не спамить через этот режим\, писать или отправлять только нужную информацию  \n
"""

async def help_command(update: Update, context: CallbackContext) -> None:
    if update.message:  # Если команда пришла через сообщение
        await update.message.reply_text(HELP_TEXT, parse_mode='MarkdownV2')
    elif update.callback_query:  # Если команда пришла через инлайн-кнопку
        await update.callback_query.message.reply_text(HELP_TEXT, parse_mode='MarkdownV2')
        await update.callback_query.answer()  # Подтверждаем нажатие кнопки

async def handle_artist_link(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    if user_id in user_data and user_data[user_id]['status'] == 'awaiting_artist_link':
        user_data[user_id]['artist_link'] = update.message.text
        logger.info(f"User {user_id} provided author link: {update.message.text}")


        await update.message.reply_text(
            '🌟Хорошо. Теперь отправьте имя автора. \n\n <i>Чтобы скрыть слово "Автор:", используйте символ "^" в начале и конце сообщения. Например: ^Имя^</i>',
            parse_mode='HTML' # Добавляем клавиатуру
        )
        user_data[user_id]['status'] = 'awaiting_author_name'
        return ASKING_FOR_AUTHOR_NAME
    else:
        await update.message.reply_text('🚫Ошибка: данные не найдены.')
        return ConversationHandler.END

# Ввод имени художника
async def handle_author_name(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id

    # Проверка, что пользователь находится в нужном состоянии
    if user_id in user_data and user_data[user_id].get('status') == 'awaiting_author_name':

        # Если авторское имя ещё не сохранено
        if 'author_name' not in user_data[user_id]:
            author_input = update.message.text.strip()

            # Проверяем, если авторское имя обернуто в "^...^"
            match_full = re.match(r'^\^(.*)\^$', author_input, re.S)
            if match_full:
                # Если весь текст внутри "^...^", используем его как заголовок и убираем авторское имя
                title = match_full.group(1).strip()
                user_data[user_id]['title'] = title
                user_data[user_id]['author_name'] = ""  # Очищаем author_name
                user_data[user_id]['extra_phrase'] = ""  # Нет доп. фразы
            else:
                # Проверка на наличие фразы в начале текста "^...^"
                match_partial = re.match(r'^\^(.*?)\^\s*(.*)', author_input, re.S)
                if match_partial:
                    # Извлекаем фразу и имя автора
                    phrase = match_partial.group(1).strip()  # Фраза из "^...^"
                    author_name = match_partial.group(2).strip()  # Остаток текста как автор
                    user_data[user_id]['extra_phrase'] = phrase  # Сохраняем фразу
                    user_data[user_id]['author_name'] = author_name  # Имя автора
                    user_data[user_id]['title'] = author_name  # Используем как заголовок
                else:
                    # Если нет фразы в "^...^", сохраняем всё как имя автора
                    author_name = author_input
                    user_data[user_id]['author_name'] = author_name
                    user_data[user_id]['title'] = author_name  # Заголовок статьи

            logger.info(f"User {user_id} provided author name or title: {author_input}")
        else:
            # Если author_name уже есть, просто используем его для заголовка
            author_name = user_data[user_id]['author_name']
            user_data[user_id]['title'] = author_name  # Обновляем заголовок

        # Переход к следующему этапу
        keyboard = [
            [InlineKeyboardButton("Помощь и разметка", callback_data='help_command')],
            [InlineKeyboardButton("‼️Полный сброс процесса‼️", callback_data='restart')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            'Отлично \n🌌Теперь приступим к наполнению публикации контентом. Отправьте изображения файлом (без сжатия) или текст. Если вы отправите изображение с подписью, то в статье телеграф текст будет так же отображаться как подпись под изображением.\n\n'
            'Текст поддерживает различное форматирование. Для получения списка тэгов нажмите на кнопку помощи.\n\n'
            '<i>Так же вы можете переслать в бот сообщения с текстом и/или изображениями, и бот тут же автоматически перенесет всё это в статью в той же очерёдности</i>',
            parse_mode='HTML',
            reply_markup=reply_markup  # Добавляем клавиатуру
        )
        user_data[user_id]['status'] = 'awaiting_image'
        return ASKING_FOR_IMAGE

    else:
        await update.message.reply_text('🚫Ошибка: данные не найдены. Попробуйте снова или нажмите /restart.')
        return ConversationHandler.END



def compress_image(file_path: str, output_path: str) -> None:
    # Определяем максимальный размер файла в байтах (5 МБ)
    max_size = 5 * 1024 * 1024

    # Проверяем, является ли файл GIF или .rar
    if file_path.endswith('.gif') or file_path.endswith('.rar'):
        return

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

# Функция для загрузки изображения на сloudinary
async def upload_image_to_cloudinary(file_path: str) -> str:
    CLOUDINARY_URL = 'https://api.cloudinary.com/v1_1/dmacjjaho/image/upload'
    UPLOAD_PRESET = 'ml_default'
    timeout = ClientTimeout(total=10)  # Таймаут в 10 секунд    
    
    async with aiohttp.ClientSession() as session:
        with open(file_path, 'rb') as f:
            form = aiohttp.FormData()
            form.add_field('file', f)
            form.add_field('upload_preset', UPLOAD_PRESET)

            async with session.post(CLOUDINARY_URL, data=form) as response:
                if response.status == 200:
                    response_json = await response.json()
                    return response_json['secure_url']
                else:
                    response_text = await response.text()  # Логируем текст ошибки
                    raise Exception(f"Ошибка загрузки на Cloudinary: {response.status}, ответ: {response_text}")


# Функция для загрузки изображения на imgbb
async def upload_image_to_imgbb(file_path: str) -> str:
    timeout = ClientTimeout(total=4)  # Таймаут в 10 секунд
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

# Функция для загрузки изображения на Imgur
async def upload_image_to_imgur(file_path: str) -> str:
    IMGUR_CLIENT_ID = '5932e0bc7fdb523'  # Укажите свой ID клиента Imgur
    headers = {'Authorization': f'Client-ID {IMGUR_CLIENT_ID}'}
    async with aiohttp.ClientSession() as session:
        with open(file_path, 'rb') as f:
            form = aiohttp.FormData()
            form.add_field('image', f)

            async with session.post('https://api.imgur.com/3/image', headers=headers, data=form) as response:
                if response.status == 200:
                    response_json = await response.json()
                    return response_json['data']['link']
                else:
                    raise Exception(f"Ошибка загрузки на Imgur: {response.status}")

# Функция для загрузки изображения на Catbox
async def upload_image_to_catbox(file_path: str) -> str:
    async with aiohttp.ClientSession() as session:
        with open(file_path, 'rb') as f:
            form = aiohttp.FormData()
            form.add_field('reqtype', 'fileupload')
            form.add_field('fileToUpload', f)
            
            # Добавляем ваш userhash
            form.add_field('userhash', '1f68d2a125c66f6ab79a4f89c')

            async with session.post('https://catbox.moe/user/api.php', data=form) as response:
                if response.status == 200:
                    return await response.text()  # возвращает URL загруженного файла
                else:
                    raise Exception(f"Ошибка загрузки на Catbox: {response.status}")

async def upload_image_to_freeimage(file_path: str) -> str:
    async with aiohttp.ClientSession() as session:
        with open(file_path, 'rb') as f:
            form = aiohttp.FormData()
            form.add_field('key', '6d207e02198a847aa98d0a2a901485a5')  # Ваш API ключ для freeimage.host
            form.add_field('action', 'upload')
            form.add_field('source', f)  # Используем файл для загрузки

            async with session.post('https://freeimage.host/api/1/upload', data=form) as response:
                if response.status == 200:
                    response_json = await response.json()
                    return response_json['image']['url']  # Проверьте правильность пути к URL в ответе
                elif response.status == 400:
                    response_text = await response.text()
                    raise Exception(f"Ошибка загрузки на Free Image Hosting: {response_text}")
                else:
                    raise Exception(f"Ошибка загрузки на Free Image Hosting: {response.status}")

# Основная функция загрузки изображения с проверкой доступности сервисов
async def upload_image(file_path: str) -> str:
    try:
        # Попытка загрузки на imgbb
        return await upload_image_to_imgbb(file_path)
    except Exception as e:
        logging.error(f"Ошибка загрузки на imgbb: {e}")
        try:
            # Попытка загрузки на Cloudinary
            return await upload_image_to_cloudinary(file_path)
        except Exception as e:
            logging.error(f"Ошибка загрузки на Cloudinary: {e}")
            try:
                # Попытка загрузки на Free Image Hosting
                return await upload_image_to_freeimage(file_path)
            except Exception as e:
                logging.error(f"Ошибка загрузки на Free Image Hosting: {e}")
                try:
                    # Попытка загрузки на Catbox
                    return await upload_image_to_catbox(file_path)
                except Exception as e:
                    logging.error(f"Ошибка загрузки на Catbox: {e}")
                    try:
                        # Попытка загрузки на Imgur
                        return await upload_image_to_imgur(file_path)
                    except Exception as e:
                        logging.error(f"Ошибка загрузки на Imgur: {e}")
                        raise Exception("Не удалось загрузить изображение на все сервисы.")

import re

# Определяем разметку тегов
markup_tags = {
    '*': 'strong',  # Жирный текст
    '_': 'em',      # Курсив
}

import re

def apply_markup(text: str) -> dict:
    """Применяет разметку к тексту на основе команд и возвращает узел контента в формате Telegra.ph."""
    
    text = text.strip()  # Убираем пробелы в начале и в конце текста
    text_lower = text.lower()

    # Обработка команд
    if text_lower.startswith("подзаголовок: "):
        content = text[len("Подзаголовок: "):].strip()
        content = apply_markup_to_content(content)
        return {"tag": "h4", "children": content}
    elif text_lower.startswith("цитата:"):
        content = text[len("Цитата:"):].strip()
        content = apply_markup_to_content(content)
        return {"tag": "blockquote", "children": content}
    elif text_lower.startswith("заголовок: "):
        content = text[len("Заголовок: "):].strip()
        content = apply_markup_to_content(content)
        return {"tag": "h3", "children": content}
    elif text_lower.startswith("цитата по центру:"):
        content = text[len("Цитата по центру:"):].strip()
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
    link_regex = re.compile(r'\[(.*?)\]\((.*?)\)', re.DOTALL)

    # Сначала обрабатываем гиперссылки
    pos = 0
    temp_nodes = []
    for match in link_regex.finditer(content):
        # Добавляем текст до текущего совпадения
        if pos < match.start():
            temp_nodes.append(content[pos:match.start()])

        # Добавляем узел ссылки
        link_text, url = match.groups()
        temp_nodes.append({"tag": "a", "attrs": {"href": url}, "children": [{"tag": "text", "children": [link_text]}]})

        # Обновляем позицию
        pos = match.end()

    # Добавляем оставшийся текст после обработки гиперссылок
    if pos < len(content):
        temp_nodes.append(content[pos:])

    # Теперь обрабатываем остальную разметку
    for node in temp_nodes:
        if isinstance(node, str):
            # Обрабатываем текст с разметкой
            while True:
                match = regex_markup.search(node)
                if not match:
                    # Если больше нет совпадений, добавляем оставшийся текст
                    nodes.append({"tag": "text", "children": [node]})
                    break
                # Добавляем текст до текущего совпадения
                if match.start() > 0:
                    nodes.append({"tag": "text", "children": [node[:match.start()]]})

                # Определяем тег и добавляем узел
                tag = markup_tags.get(match.group(1))
                if tag:
                    nodes.append({"tag": tag, "children": [match.group(2)]})

                # Обновляем строку: обрезаем её до конца текущего совпадения
                node = node[match.end():]
        else:
            nodes.append(node)

    return nodes

async def edit_article(update: Update, context: CallbackContext) -> None:
    # Проверяем, является ли обновление запросом обратного вызова (нажатие кнопки)
    if update.callback_query:
        query = update.callback_query
        user_id = query.from_user.id
    else:
        user_id = update.message.from_user.id  # Если это сообщение, получаем ID пользователя

    media = user_data[user_id].get('media', [])
    
    if not media:
        await update.message.reply_text("🚫 Ошибка: нет фрагментов для редактирования.")
        return

    # Удаляем предыдущее сообщение с кнопками содержания статьи
    if 'last_content_message_id' in user_data[user_id]:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,  # Используем effective_chat
                message_id=user_data[user_id]['last_content_message_id']
            )
        except Exception as e:
            print(f"Ошибка при удалении сообщения с содержанием: {e}")

    # Настройки пагинации
    items_per_page = 30  # Количество кнопок на странице
    total_pages = (len(media) + items_per_page - 1) // items_per_page  # Общее количество страниц
    current_page = user_data[user_id].get('current_page', 0)  # Текущая страница

    # Ограничиваем текущую страницу
    current_page = max(0, min(current_page, total_pages - 1))
    
    # Создаем новый список кнопок для текущей страницы
    keyboard = []
    image_counter = 1  # Счётчик для изображений
    start_idx = current_page * items_per_page
    end_idx = min(start_idx + items_per_page, len(media))

    for idx in range(start_idx, end_idx):
        item = media[idx]
        if item['type'] == 'text':
            text = item['content']
            if isinstance(text, dict) and 'children' in text:
                text = ''.join(child['children'][0] for child in text['children'] if isinstance(child, dict) and 'children' in child)
            preview_text = (text[:12] + '...') if len(text) > 12 else text
        else:
            preview_text = f"{image_counter} изображение"
            image_counter += 1
        
        keyboard.append([
            InlineKeyboardButton(text=str(preview_text), callback_data=f"preview_{idx}"),
            InlineKeyboardButton(text="Редактировать", callback_data=f"edit_{idx}"),
            InlineKeyboardButton(text="Удалить", callback_data=f"delete_{idx}"),
        ])

    # Добавляем кнопки навигации, если это не первая страница
    if current_page > 0:
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='page_down')])
    
    # Добавляем кнопки навигации, если это не последняя страница
    if current_page < total_pages - 1:
        keyboard.append([InlineKeyboardButton("Вперёд ➡️", callback_data='page_up')])
    
    keyboard.append([InlineKeyboardButton("🌌 Предпросмотр 🌌", callback_data='preview_article')])
    keyboard.append([InlineKeyboardButton("Помощь и разметка", callback_data='help_command')])
    keyboard.append([InlineKeyboardButton("Найти автора, аниме или проверить на ИИ", callback_data='start_search')])
    keyboard.append([InlineKeyboardButton("🌠 К Завершению Публикации 🌠", callback_data='create_article')])
    # Отправляем новое сообщение и сохраняем его ID
    sent_message = await (query.message if update.callback_query else update.message).reply_text(
        "Выберите фрагмент для редактирования или удаления:", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    # Сохраняем ID нового сообщения с кнопками
    user_data[user_id]['last_content_message_id'] = sent_message.message_id
    user_data[user_id]['current_page'] = current_page  # Сохраняем текущую страницу



async def handle_edit_delete(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    action, index = query.data.split('_')
    index = int(index)

    media = user_data[user_id].get('media', [])

    # Проверяем, существует ли фрагмент с таким индексом
    if index >= len(media):
        await query.message.reply_text("🚫 Ошибка: указанный индекс недействителен.")
        return

    if action == 'edit':
        # Если тип контента — изображение, предлагаем отправить новое изображение
        if media[index]['type'] == 'image':
            context.user_data['editing_index'] = index
            await query.message.reply_text("Пожалуйста, отправьте новое изображение или текст:")
            return ASKING_FOR_IMAGE  # Переход к ожиданию нового изображения
        # Если тип контента — текст, предлагаем ввести новый текст
        elif media[index]['type'] == 'text':
            context.user_data['editing_index'] = index
            await query.message.reply_text("Пожалуйста, отправьте новое изображение или текст:")
            return EDITING_FRAGMENT  # Переходим в состояние редактирования текста

    elif action == 'delete':
        if index < len(media):
            media.pop(index)
            user_data[user_id]['media'] = media  # Сохраняем изменения

            # Обновляем кнопки
# Количество кнопок на одной странице
            PAGE_SIZE = 30

            # Получаем текущую страницу из user_data (по умолчанию 1)
            if 'page' not in user_data[user_id]:
                user_data[user_id]['page'] = 1
            current_page = user_data[user_id]['page']

            # Обновляем кнопки
            keyboard = []
            image_counter = 1  # Счётчик для изображений

            # Подсчёт общего количества элементов
            total_items = len(media)
            total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE  # Рассчитываем количество страниц

            # Показ элементов только для текущей страницы
            start_idx = (current_page - 1) * PAGE_SIZE
            end_idx = start_idx + PAGE_SIZE

            for idx, item in enumerate(media[start_idx:end_idx], start=start_idx):
                if item['type'] == 'text':
                    text = item['content']
                    
                    # Извлечение текста, если нужно
                    if isinstance(text, dict) and 'children' in text:
                        text = ''.join(child['children'][0] for child in text['children'] if isinstance(child, dict) and 'children' in child)
                    
                    preview_text = (text[:12] + '...') if len(text) > 12 else text
                else:  # Если элемент — это изображение
                    preview_text = f"{image_counter} изображение"  # Нумерация только для изображений
                    image_counter += 1  # Увеличиваем счётчик только для изображений
                
                # Добавляем кнопки для текущей страницы
                keyboard.append([
                    InlineKeyboardButton(text=str(preview_text), callback_data=f"preview_{idx}"),
                    InlineKeyboardButton(text="Редактировать", callback_data=f"edit_{idx}"),
                    InlineKeyboardButton(text="Удалить", callback_data=f"delete_{idx}"),
                ])

            # Добавляем кнопки для переключения страниц
            navigation_buttons = []
            if current_page > 1:
                navigation_buttons.append(InlineKeyboardButton("⬆️ Предыдущая страница", callback_data=f"prev_page_{current_page - 1}"))
            if current_page < total_pages:
                navigation_buttons.append(InlineKeyboardButton("⬇️ Следующая страница", callback_data=f"next_page_{current_page + 1}"))

            if navigation_buttons:
                keyboard.append(navigation_buttons)

            # Добавляем кнопку предпросмотра
            keyboard.append([InlineKeyboardButton("🌌 Предпросмотр 🌌", callback_data='preview_article')])
            keyboard.append([InlineKeyboardButton("Помощь и разметка", callback_data='help_command')])
            keyboard.append([InlineKeyboardButton("Найти автора, аниме или проверить на ИИ", callback_data='start_search')])
            keyboard.append([InlineKeyboardButton("🌠 К Завершению Публикации 🌠", callback_data='create_article')])

            # Отправляем новое сообщение с обновлённым списком кнопок
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_reply_markup(reply_markup=reply_markup)  # Обновляем клавиатуру

            await query.message.reply_text("✅ Фрагмент удалён.")
        return





async def handle_new_text(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    index = context.user_data['editing_index']
    media = user_data[user_id].get('media', [])

    # Убедимся, что индекс действителен
    if index >= 0 and index < len(media):
        # Если редактируемый элемент — это текст
        if media[index]['type'] == 'text':
            # Обновляем текст
            formatted_text = apply_markup(update.message.text)
            media[index] = {  # Обновляем существующий текст
                'type': 'text',
                'content': formatted_text
            }
            user_data[user_id]['media'] = media  # Сохраняем изменения

            # Удаляем предыдущее сообщение с кнопками содержания статьи
            if 'last_content_message_id' in user_data[user_id]:
                try:
                    await context.bot.delete_message(
                        chat_id=update.message.chat_id, 
                        message_id=user_data[user_id]['last_content_message_id']
                    )
                except Exception as e:
                    print(f"Ошибка при удалении сообщения с содержанием: {e}")

            # Настройки пагинации
            items_per_page = 30  # Количество кнопок на странице
            total_pages = (len(media) + items_per_page - 1) // items_per_page  # Общее количество страниц
            current_page = user_data[user_id].get('current_page', 0)  # Текущая страница

            # Ограничиваем текущую страницу
            current_page = max(0, min(current_page, total_pages - 1))


            # Создаём новый список кнопок для содержания статьи
    # Создаем новый список кнопок для текущей страницы
            keyboard = []
            image_counter = 1  # Счётчик для изображений
            start_idx = current_page * items_per_page
            end_idx = min(start_idx + items_per_page, len(media))

            for idx, item in enumerate(media):
                item = media[idx]
                if item['type'] == 'text':
                    text = item['content']
                    
                    # Извлечение текста, если нужно
                    if isinstance(text, dict) and 'children' in text:
                        text = ''.join(child['children'][0] for child in text['children'] if isinstance(child, dict) and 'children' in child)
                    
                    preview_text = (text[:12] + '...') if len(text) > 12 else text
                else:  # Если элемент — это изображение
                    preview_text = f"{image_counter} изображение"  # Нумерация только для изображений
                    image_counter += 1  # Увеличиваем счётчик только для изображений
                
                # Добавляем кнопки для предпросмотра, редактирования и удаления
                keyboard.append([
                    InlineKeyboardButton(text=str(preview_text), callback_data=f"preview_{idx}"),
                    InlineKeyboardButton(text="Редактировать", callback_data=f"edit_{idx}"),
                    InlineKeyboardButton(text="Удалить", callback_data=f"delete_{idx}"),
                ])

            if current_page > 0:
                keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='page_down')])
            
            # Добавляем кнопки навигации, если это не последняя страница
            if current_page < total_pages - 1:
                keyboard.append([InlineKeyboardButton("Вперёд ➡️", callback_data='page_up')])
            

            keyboard.append([
                InlineKeyboardButton("🌌 Предпросмотр 🌌 ", callback_data='preview_article')
            ])    

            # Отправляем новое сообщение с обновлённым списком кнопок
            reply_markup = InlineKeyboardMarkup(keyboard)
            sent_message = await context.bot.send_message(
                chat_id=update.message.chat_id,
                text='📝 Текущее содержание статьи2:',
                reply_markup=reply_markup
            )

            # Сохраняем ID нового сообщения с кнопками
            user_data[user_id]['last_content_message_id'] = sent_message.message_id
            user_data[user_id]['current_page'] = current_page  
            # Сообщаем пользователю об успешном обновлении текста
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text='✅ Текст обновлён.',
                reply_to_message_id=update.message.message_id
            )

            # Удаляем индекс редактирования после завершения
            del context.user_data['editing_index']

            return ASKING_FOR_IMAGE
        else:
            # Ошибка, если тип редактируемого элемента не текст
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text='🚫 Ошибка: указанный элемент не является текстом.',
                reply_to_message_id=update.message.message_id
            )
            del context.user_data['editing_index']  # Удаляем индекс, если он недействителен
            return ConversationHandler.END
    else:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text='🚫 Ошибка: указанный индекс недействителен.',
            reply_to_message_id=update.message.message_id
        )
        del context.user_data['editing_index']  # Удаляем индекс, если он недействителен
        return ConversationHandler.END


async def handle_new_image(update: Update, context: CallbackContext, index: int, media: list) -> int:
    user_id = update.message.from_user.id
    message_id = update.message.message_id

    if update.message.photo or update.message.document:
        if update.message.photo:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text='🚫 Ошибка: пожалуйста, отправьте изображение как файл (формат JPG, PNG или .RAR для .GIF), без сжатия. Для подробностей введите /help',
                reply_to_message_id=message_id
            )
            return ASKING_FOR_IMAGE 

        elif update.message.document:
            file_name = update.message.document.file_name
            file_ext = file_name.lower().split('.')[-1]
            file = await context.bot.get_file(update.message.document.file_id)

        # Создаем временный файл для сохранения изображения
        with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_ext}') as tmp_file:
            file_path = tmp_file.name
            await file.download_to_drive(file_path)

        if file_ext == 'rar':
            new_file_path = f'{os.path.splitext(file_path)[0]}.gif'
            shutil.move(file_path, new_file_path)
            file_path = new_file_path
            file_name = os.path.basename(file_path)
            file_ext = 'gif'

        if file_ext in ('jpg', 'jpeg', 'png', 'gif'):
            if file_ext == 'gif':
                try:
                    image_url = await upload_image(file_path)
                    media[index] = {  # Обновляем существующее изображение
                        'type': 'image',
                        'url': image_url,
                        'caption': update.message.caption if update.message.caption else ""
                    }
                    user_data[user_id]['media'] = media  # Сохраняем изменения

                    # Удаляем предыдущее сообщение, если оно есть
                    if 'last_image_message_id' in user_data[user_id]:
                        try:
                            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=user_data[user_id]['last_image_message_id'])
                        except Exception as e:
                            print(f"Ошибка при удалении сообщения: {e}")

                    # Отправляем новое сообщение
                    sent_message = await context.bot.send_message(
                        chat_id=update.message.chat_id,
                        text='✅ Изображение очень обновлено.',
                        reply_to_message_id=message_id
                    )

                    # Сохраняем ID нового сообщения
                    user_data[user_id]['last_image_message_id'] = sent_message.message_id

                    # Удаляем индекс редактирования после завершения
                    del context.user_data['editing_index']

                    return ASKING_FOR_IMAGE
                except Exception as e:
                    await context.bot.send_message(
                        chat_id=update.message.chat_id,
                        text=f'🚫 Ошибка при загрузке изображения: {str(e)}. Попробуйте снова.',
                        reply_to_message_id=message_id
                    )
                    return ConversationHandler.END

            else:
                if os.path.getsize(file_path) > 5 * 1024 * 1024:
                    compressed_path = f'{os.path.splitext(file_path)[0]}_compressed.jpg'
                    compress_image(file_path, compressed_path)
                    file_path = compressed_path

                try:
                    image_url = await upload_image(file_path)
                    media[index] = {  # Обновляем существующее изображение
                        'type': 'image',
                        'url': image_url,
                        'caption': update.message.caption if update.message.caption else ""
                    }
                    user_data[user_id]['media'] = media  # Сохраняем изменения
                    os.remove(file_path)

                    # Удаляем предыдущее сообщение, если оно есть
                    if 'last_image_message_id' in user_data[user_id]:
                        try:
                            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=user_data[user_id]['last_image_message_id'])
                        except Exception as e:
                            print(f"Ошибка при удалении сообщения: {e}")

                    # Отправляем новое сообщение
                    sent_message = await context.bot.send_message(
                        chat_id=update.message.chat_id,
                        text='✅ Изображение добавлено.',
                        reply_to_message_id=message_id
                    )

                    # Удаляем предыдущее сообщение с кнопками содержания статьи, если оно существует
                    if 'last_content_message_id' in user_data[user_id]:
                        try:
                            await context.bot.delete_message(
                                chat_id=update.message.chat_id, 
                                message_id=user_data[user_id]['last_content_message_id']
                            )
                        except Exception as e:
                            print(f"Ошибка при удалении сообщения с содержанием: {e}")


                    # Настройки пагинации
                    items_per_page = 30  # Количество кнопок на странице
                    total_pages = (len(media) + items_per_page - 1) // items_per_page  # Общее количество страниц
                    current_page = user_data[user_id].get('current_page', 0)  # Текущая страница

                    # Ограничиваем текущую страницу
                    current_page = max(0, min(current_page, total_pages - 1))

                    # Создаём новый список кнопок для содержания статьи
                    keyboard = []
                    image_counter = 1  # Счётчик для изображений
                    start_idx = current_page * items_per_page
                    end_idx = min(start_idx + items_per_page, len(media))
                    for idx in range(start_idx, end_idx):
                        item = media[idx]
                        if item['type'] == 'text':
                            text = item['content']
                            
                            # Извлечение текста, если нужно
                            if isinstance(text, dict) and 'children' in text:
                                text = ''.join(child['children'][0] for child in text['children'] if isinstance(child, dict) and 'children' in child)
                            
                            preview_text = (text[:12] + '...') if len(text) > 12 else text
                        else:  # Если элемент — это изображение
                            preview_text = f"Обн изобр-ие"  # Нумерация только для изображений
                            image_counter += 1  # Увеличиваем счётчик только для изображений
                        
                        # Добавляем кнопки для предпросмотра, редактирования и удаления
                        keyboard.append([
                            InlineKeyboardButton(text=str(preview_text), callback_data=f"preview_{idx}"),
                            InlineKeyboardButton(text="Редактировать", callback_data=f"edit_{idx}"),
                            InlineKeyboardButton(text="Удалить", callback_data=f"delete_{idx}"),
                        ])

                    if current_page > 0:
                        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='page_down')])
                    
                    # Добавляем кнопки навигации, если это не последняя страница
                    if current_page < total_pages - 1:
                        keyboard.append([InlineKeyboardButton("Вперёд ➡️", callback_data='page_up')])
                    
                    keyboard.append([
                        InlineKeyboardButton("🌌 Предпросмотр 🌌 ", callback_data='preview_article')
                    ])    

                    # Отправляем новое сообщение с обновлённым списком кнопок
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    sent_message = await context.bot.send_message(
                        chat_id=update.message.chat_id,
                        text='📝 Текущее содержание статьи1:',
                        reply_markup=reply_markup
                    )

                    # Сохраняем ID нового сообщения с кнопками
                    user_data[user_id]['last_content_message_id'] = sent_message.message_id
                    user_data[user_id]['current_page'] = current_page  

                    # Удаляем индекс редактирования после завершения
                    del context.user_data['editing_index']

                    return ASKING_FOR_IMAGE
                except Exception as e:
                    await context.bot.send_message(
                        chat_id=update.message.chat_id,
                        text=f'🚫 Ошибка при загрузке изображения: {str(e)}. Попробуйте снова.',
                        reply_to_message_id=message_id
                    )
                    return ConversationHandler.END

        else:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text='Пожалуйста, отправьте изображение в формате JPG, PNG или .RAR для .GIF.',
                reply_to_message_id=message_id
            )
            return ASKING_FOR_IMAGE
    else:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text='🚫 Ошибка: ожидается изображение или файл.',
            reply_to_message_id=message_id
        )
        return ConversationHandler.END




async def handle_image(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    caption = update.message.caption
    message_id = update.message.message_id

    # Проверяем, редактирует ли пользователь что-либо
    if 'editing_index' in context.user_data:
        index = context.user_data['editing_index']
        media = user_data[user_id].get('media', [])

        # Проверяем, если индекс действителен
        if 0 <= index < len(media):
            # Проверяем, если редактируем текст, и если получен текст
            if media[index]['type'] == 'text':
                # Если пользователь прислал текст, обрабатываем его как текст
                if update.message.text:
                    return await handle_new_text_from_image(update, context, index, media)

                # Если вместо текста пришло изображение
                if update.message.photo or update.message.document:
                    return await handle_new_image(update, context, index, media)

            # Проверяем, если редактируем изображение
            if media[index]['type'] == 'image':
                # Проверяем, если пользователь отправил текст вместо изображения
                if update.message.text:
                    return await handle_new_text_from_image(update, context, index, media)

                # Проверка фото
                if update.message.photo:
                    await context.bot.send_message(
                        chat_id=update.message.chat_id,
                        text='Пожалуйста, отправьте изображение как файл (формат JPG, PNG или .RAR для .GIF), без сжатия. Для подробностей введите /help',
                        reply_to_message_id=message_id
                    )
                    return ASKING_FOR_IMAGE

                elif update.message.document:
                    file_name = update.message.document.file_name
                    if file_name:  # Проверка, что файл имеет имя
                        file_ext = file_name.lower().split('.')[-1]

                        # Если не удается определить расширение, выходим
                        if not file_ext:
                            await context.bot.send_message(
                                chat_id=update.message.chat_id,
                                text='🚫 Ошибка: не удалось определить расширение файла. Пожалуйста, отправьте файл с правильным расширением.',
                                reply_to_message_id=message_id
                            )
                            return ConversationHandler.END

                        file = await context.bot.get_file(update.message.document.file_id)
                        # Скачивание и создание временного файла
                        with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_ext}') as tmp_file:
                            file_path = tmp_file.name
                            await file.download_to_drive(file_path)                    

                # Обработка документа

                        if file_ext == 'rar':
                            new_file_path = f'{os.path.splitext(file_path)[0]}.gif'
                            shutil.move(file_path, new_file_path)
                            file_path = new_file_path
                            file_name = os.path.basename(file_path)
                            file_ext = 'gif'

                        if file_ext in ('jpg', 'jpeg', 'png', 'gif'):
                            if file_ext == 'gif':
                                try:
                                    image_url = await upload_image(file_path)
                                    media[index] = {  # Обновляем существующее изображение
                                        'type': 'image',
                                        'url': image_url,
                                        'caption': caption if caption else ""
                                    }
                                    user_data[user_id]['media'] = media  # Сохраняем изменения

                                    # Удаляем предыдущее сообщение, если оно есть
                                    if 'last_image_message_id' in user_data[user_id]:
                                        try:
                                            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=user_data[user_id]['last_image_message_id'])
                                        except Exception as e:
                                            print(f"Ошибка при удалении сообщения: {e}")

                                    # Отправляем новое сообщение
                                    sent_message = await context.bot.send_message(
                                        chat_id=update.message.chat_id,
                                        text='✅ Изображение замечательно обновлено.',
                                        reply_to_message_id=message_id
                                    )

                                    # Сохраняем ID нового сообщения
                                    user_data[user_id]['last_image_message_id'] = sent_message.message_id

                                    # Удаляем индекс редактирования после завершения
                                    del context.user_data['editing_index']

                                    return ASKING_FOR_IMAGE
                                except Exception as e:
                                    await context.bot.send_message(
                                        chat_id=update.message.chat_id,
                                        text=f'🚫 Ошибка при загрузке изображения: {str(e)}. Попробуйте снова.',
                                        reply_to_message_id=message_id
                                    )
                                    return ConversationHandler.END
                            else:
                                if os.path.getsize(file_path) > 5 * 1024 * 1024:
                                    compressed_path = f'{os.path.splitext(file_path)[0]}_compressed.jpg'
                                    compress_image(file_path, compressed_path)
                                    file_path = compressed_path

                                try:




                                    image_url = await upload_image(file_path)
                                    media[index] = {  # Обновляем существующее изображение
                                        'type': 'image',
                                        'url': image_url,
                                        'caption': caption if caption else ""
                                    }
                                    user_data[user_id]['media'] = media # Сохраняем изменения
                                    os.remove(file_path)

                                    # Удаляем предыдущее сообщение, если оно есть
                                    if 'last_image_message_id' in user_data[user_id]:
                                        try:
                                            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=user_data[user_id]['last_image_message_id'])
                                        except Exception as e:
                                            print(f"Ошибка при удалении сообщения: {e}")

                                    # Отправляем новое сообщение
                                    keyboard = []
                                    image_counter = 1  # Счётчик для изображений

                                    # Настройки пагинации
                                    items_per_page = 30  # Количество кнопок на странице
                                    total_pages = (len(media) + items_per_page - 1) // items_per_page  # Общее количество страниц
                                    current_page = user_data[user_id].get('current_page', 0)  # Текущая страница

                                    # Ограничиваем текущую страницу
                                    current_page = max(0, min(current_page, total_pages - 1))

                                    # Создаём новый список кнопок для содержания статьи
                                    start_idx = current_page * items_per_page
                                    end_idx = min(start_idx + items_per_page, len(media))
                                    for idx in range(start_idx, end_idx):
                                        item = media[idx]
                                        if item['type'] == 'text':
                                            text = item['content']
                                            
                                            # Извлечение текста, если нужно
                                            if isinstance(text, dict) and 'children' in text:
                                                text = ''.join(child['children'][0] for child in text['children'] if isinstance(child, dict) and 'children' in child)
                                            
                                            preview_text = (text[:12] + '...') if len(text) > 12 else text
                                        else:  # Если элемент — это изображение
                                            preview_text = f"{image_counter} изображение"  # Нумерация только для изображений
                                            image_counter += 1  # Увеличиваем счётчик только для изображений
                                        
                                        # Добавляем кнопки для предпросмотра, редактирования и удаления
                                        keyboard.append([
                                            InlineKeyboardButton(text=str(preview_text), callback_data=f"preview_{idx}"),
                                            InlineKeyboardButton(text="Редактировать", callback_data=f"edit_{idx}"),
                                            InlineKeyboardButton(text="Удалить", callback_data=f"delete_{idx}"),
                                        ])

                                    # Добавляем кнопки навигации, если это не первая страница
                                    if current_page > 0:
                                        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='page_down')])

                                    # Добавляем кнопки навигации, если это не последняя страница
                                    if current_page < total_pages - 1:
                                        keyboard.append([InlineKeyboardButton("Вперёд ➡️", callback_data='page_up')])

                                    keyboard.append([InlineKeyboardButton("🌌 Предпросмотр 🌌", callback_data='preview_article')])
                                    keyboard.append([InlineKeyboardButton("Помощь и разметка", callback_data='help_command')])
                                    keyboard.append([InlineKeyboardButton("Найти автора, аниме или проверить на ИИ", callback_data='start_search')])
                                    keyboard.append([InlineKeyboardButton("🌠 К Завершению Публикации 🌠", callback_data='create_article')])


                                    # Отправляем новое сообщение с обновлённым списком кнопок
                                    reply_markup = InlineKeyboardMarkup(keyboard)
                                    sent_message_with_buttons = await context.bot.send_message(
                                        chat_id=update.message.chat_id,
                                        text='✅ Изображение Заменено. \n📝 Текущее содержание статьи:',
                                        reply_markup=reply_markup
                                    )

                                    # Сохраняем ID нового сообщения с кнопками
                                    user_data[user_id]['last_image_message_id'] = sent_message_with_buttons.message_id
                                    user_data[user_id]['current_page'] = current_page

                                    # Удаляем индекс редактирования после завершения
                                    del context.user_data['editing_index']

                                    return ASKING_FOR_IMAGE
                                except Exception as e:
                                    await context.bot.send_message(
                                        chat_id=update.message.chat_id,
                                        text=f'🚫 Ошибка при загрузке изображения: {str(e)}. Попробуйте снова.',
                                        reply_to_message_id=message_id
                                    )
                                    return ConversationHandler.END

                        else:
                            await context.bot.send_message(
                                chat_id=update.message.chat_id,
                                text='Пожалуйста, отправьте изображение в формате JPG, PNG или .RAR для .GIF.',
                                reply_to_message_id=message_id
                            )
                            return ASKING_FOR_IMAGE

                    elif media[index]['type'] == 'text':
                        # Если редактируем текст, вызываем обработчик текста
                        return await handle_new_text(update, context)
                    else:
                        await context.bot.send_message(
                            chat_id=update.message.chat_id,
                            text='🚫 Ошибка: указанный элемент имеет недопустимый тип.',
                            reply_to_message_id=message_id
                        )
                        del context.user_data['editing_index']
                        return ConversationHandler.END
                else:
                    await context.bot.send_message(
                        chat_id=update.message.chat_id,
                        text='🚫 Ошибка: указанный индекс изображения недействителен.',
                        reply_to_message_id=message_id
                    )
                    del context.user_data['editing_index']  # Удаляем индекс, если он недействителен
                    return ConversationHandler.END

    # Если не в состоянии редактирования, продолжаем обычную обработку изображений
    if user_id in user_data and user_data[user_id]['status'] == 'awaiting_image':
        if update.message.photo:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text='Пожалуйста, отправьте изображение как файл (формат JPG, PNG или .RAR для .GIF), без сжатия. Для подробностей введите /help',
                reply_to_message_id=message_id
            )
            return ASKING_FOR_IMAGE

        elif update.message.document:
            file_name = update.message.document.file_name
            file_ext = file_name.lower().split('.')[-1]
            file = await context.bot.get_file(update.message.document.file_id)

            with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_ext}') as tmp_file:
                file_path = tmp_file.name
                await file.download_to_drive(file_path)

            if file_ext == 'rar':
                new_file_path = f'{os.path.splitext(file_path)[0]}.gif'
                shutil.move(file_path, new_file_path)
                file_path = new_file_path
                file_name = os.path.basename(file_path)
                file_ext = 'gif'

            if file_ext in ('jpg', 'jpeg', 'png', 'gif'):
                if file_ext == 'gif':
                    try:
                        image_url = await upload_image(file_path)
                        if 'media' not in user_data[user_id]:
                            user_data[user_id]['media'] = []
                        user_data[user_id]['media'].append({
                            'type': 'image',
                            'url': image_url,
                            'caption': caption if caption else ""
                        })

                        # Удаляем предыдущее сообщение, если оно есть
                        if 'last_image_message_id' in user_data[user_id]:
                            try:
                                await context.bot.delete_message(chat_id=update.message.chat_id, message_id=user_data[user_id]['last_image_message_id'])
                            except Exception as e:
                                print(f"Ошибка при удалении сообщения: {e}")

                        keyboard = [
                            [InlineKeyboardButton("‼️Сброс Публикации и Возврат к Началу‼️", callback_data='restart')],
                            [InlineKeyboardButton("📝 Проверить последний текст на ошибки 📝", callback_data='check_text')],  # Новая кнопка                           
                            [InlineKeyboardButton("Удалить последний элемент", callback_data='delete_last')],
                            [InlineKeyboardButton("Предпросмотр", callback_data='preview_article')],
                            [InlineKeyboardButton("Редактировать", callback_data='edit_article')],
                            [InlineKeyboardButton("Помощь и разметка", callback_data='help_command')],
                            [InlineKeyboardButton("🌠 К Завершению Публикации 🌠", callback_data='create_article')]
                        ]

                        reply_markup = InlineKeyboardMarkup(keyboard)                                

                        if 'image_counter' not in user_data[user_id]:
                            user_data[user_id]['image_counter'] = 0

                        # Когда бот получает изображение, увеличиваем счётчик
                        user_data[user_id]['image_counter'] += 1
                        image_counter = user_data[user_id]['image_counter']

                        # Используем счётчик в сообщении
                        image_text = "изображение" if image_counter == 1 else "изображения"
                        sent_message = await context.bot.send_message(
                            chat_id=update.message.chat_id,
                            text=f'✅ {image_counter} {image_text} добавлено.\n\n Дождитесь загрузки остальных изображений, если их больше чем одно. Затем вы можете продолжить присылать изображения или текст.\n\n Так же вы можете использовать следующие команды:',
                            reply_to_message_id=message_id,
                            reply_markup=reply_markup
                        )

                        # Сохраняем ID нового сообщения
                        user_data[user_id]['last_image_message_id'] = sent_message.message_id

                        return ASKING_FOR_IMAGE
                    except Exception as e:
                        await context.bot.send_message(
                            chat_id=update.message.chat_id,
                            text=f'🚫Ошибка при загрузке изображения: {str(e)}. Можете попробовать прислать файл ещё раз через некоторое время или нажать /restart',
                            reply_to_message_id=message_id
                        )
                        return ConversationHandler.END
                else:
                    if os.path.getsize(file_path) > 5 * 1024 * 1024:
                        compressed_path = f'{os.path.splitext(file_path)[0]}_compressed.jpg'
                        compress_image(file_path, compressed_path)
                        file_path = compressed_path

                    try:
                        image_url = await upload_image(file_path)
                        if 'media' not in user_data[user_id]:
                            user_data[user_id]['media'] = []
                        user_data[user_id]['media'].append({
                            'type': 'image',
                            'url': image_url,
                            'caption': caption if caption else ""
                        })
                        os.remove(file_path)

                        # Удаляем предыдущее сообщение, если оно есть
                        if 'last_image_message_id' in user_data[user_id]:
                            try:
                                await context.bot.delete_message(chat_id=update.message.chat_id, message_id=user_data[user_id]['last_image_message_id'])
                            except Exception as e:
                                print(f"Ошибка при удалении сообщения: {e}")

                        keyboard = [
                            [InlineKeyboardButton("‼️Сброс Публикации и Возврат к Началу‼️", callback_data='restart')],
                            [InlineKeyboardButton("📝 Проверить последний текст на ошибки 📝", callback_data='check_text')],  # Новая кнопка                           
                            [InlineKeyboardButton("Удалить последний элемент", callback_data='delete_last')],
                            [InlineKeyboardButton("Предпросмотр", callback_data='preview_article')],
                            [InlineKeyboardButton("Редактировать", callback_data='edit_article')],
                            [InlineKeyboardButton("Помощь и разметка", callback_data='help_command')],
                            [InlineKeyboardButton("🌠 К Завершению Публикации 🌠", callback_data='create_article')]
                        ]

                        reply_markup = InlineKeyboardMarkup(keyboard) 


                        if 'image_counter' not in user_data[user_id]:
                            user_data[user_id]['image_counter'] = 0

                        # Когда бот получает изображение, увеличиваем счётчик
                        user_data[user_id]['image_counter'] += 1
                        image_counter = user_data[user_id]['image_counter']

                        # Используем счётчик в сообщении
                        image_text = "изображение" if image_counter == 1 else "изображения"
                        sent_message = await context.bot.send_message(
                            chat_id=update.message.chat_id,
                            text=f'✅ {image_counter} {image_text} добавлено.\n\n Дождитесь загрузки остальных изображений, если их больше чем одно. Затем вы можете продолжить присылать изображения или текст.\n\n Так же вы можете использовать следующие команды:',
                            reply_to_message_id=message_id,
                            reply_markup=reply_markup
                        )

                        # Сохраняем ID нового сообщения
                        user_data[user_id]['last_image_message_id'] = sent_message.message_id

                        return ASKING_FOR_IMAGE
                    except Exception as e:
                        await context.bot.send_message(
                            chat_id=update.message.chat_id,
                            text=f'🚫Ошибка при загрузке изображения: {str(e)}. Можете попробовать прислать файл ещё раз через некоторое время или нажать /restart',
                            reply_to_message_id=message_id
                        )
                        return ConversationHandler.END

            else:
                await context.bot.send_message(
                    chat_id=update.message.chat_id,
                    text='Пожалуйста, отправьте изображение в формате JPG, PNG или .RAR для .GIF.',
                    reply_to_message_id=message_id
                )
                return ASKING_FOR_IMAGE

        elif update.message.text:
            return await handle_text(update, context)

        else:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text='Пожалуйста, отправьте изображение как файл (формат JPG, PNG или .RAR для .GIF), без сжатия, или текст.',
                reply_to_message_id=message_id
            )
            return ASKING_FOR_IMAGE

    else:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text='🚫Ошибка: данные не найдены. Попробуйте отправить снова. Или нажмите /restart',
            reply_to_message_id=message_id
        )
        return ConversationHandler.END


        
# Функция для обработки текстовых сообщений
async def handle_text(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    message_text = update.message.text

    # Если не в режиме редактирования, продолжаем обычную обработку текстовых сообщений
    user_data_entry = user_data.get(user_id, {})

    # Проверяем статус пользователя
    if user_data_entry.get('status') == 'awaiting_image':
        # Обработка текстовых сообщений с разметкой
        formatted_text = apply_markup(message_text)

        # Проверка наличия раздела 'media' и добавление текста
        if 'media' not in user_data_entry:
            user_data_entry['media'] = []

        user_data_entry['media'].append({'type': 'text', 'content': formatted_text})

        # Сохраняем обновлённые данные
        user_data[user_id] = user_data_entry

        # Удаление предыдущего сообщения, если оно существует
        if 'last_message_id' in user_data_entry:
            try:
                await context.bot.delete_message(
                    chat_id=update.message.chat_id, 
                    message_id=user_data_entry['last_message_id']
                )
            except Exception as e:
                print(f"Ошибка при удалении сообщения: {e}")

        # Отправляем новое сообщение


        keyboard = [
            [InlineKeyboardButton("‼️Сброс Публикации и Возврат к Началу‼️", callback_data='restart')],
            [InlineKeyboardButton("📝 Проверить последний текст на ошибки 📝", callback_data='check_text')],  # Новая кнопка
            [InlineKeyboardButton("Удалить последний элемент", callback_data='delete_last')],
            [InlineKeyboardButton("Предпросмотр", callback_data='preview_article')],
            [InlineKeyboardButton("Редактировать", callback_data='edit_article')],
            [InlineKeyboardButton("Помощь и разметка", callback_data='help_command')],
            [InlineKeyboardButton("🌠 К Завершению Публикации 🌠", callback_data='create_article')],
        ]

        reply_markup = InlineKeyboardMarkup(keyboard) 

        if 'text_counter' not in user_data[user_id]:
            user_data[user_id]['text_counter'] = 0

        # Когда бот получает текст, увеличиваем счётчик
        user_data[user_id]['text_counter'] += 1
        text_counter = user_data[user_id]['text_counter']

        # Используем счётчик текста в сообщении
        text_message = "текст" if text_counter == 1 else "текста"
        sent_message = await update.message.reply_text(
            f'✅ {text_counter} {text_message} успешно добавлено. Вы можете отправить ещё текст или изображения.\n\n'
            'Либо воспользоваться одной из команд ниже:\n',
            reply_to_message_id=update.message.message_id,
            reply_markup=reply_markup  # Добавляем клавиатуру с кнопкой
        )
        # Сохраняем ID нового сообщения
        user_data_entry['last_message_id'] = sent_message.message_id
        user_data[user_id] = user_data_entry

        return ASKING_FOR_IMAGE
    else:
        await update.message.reply_text('🚫 Ошибка: данные не найдены. Попробуйте отправить снова. Или нажмите /restart')
        return ConversationHandler.END

def extract_text_from_json(data):
    if isinstance(data, dict):
        # Если текущий элемент - это словарь, рекурсивно обрабатываем его ключ 'children'
        return ''.join(extract_text_from_json(child) for child in data.get('children', []))
    elif isinstance(data, list):
        # Если текущий элемент - это список, обрабатываем каждый элемент списка
        return ''.join(extract_text_from_json(item) for item in data)
    elif isinstance(data, str):
        # Если текущий элемент - это строка, возвращаем её
        return data
    return ''

async def handle_check_text(update, context):
    user_id = update.effective_user.id
    user_data_entry = user_data.get(user_id, {})

    # Проверяем, есть ли текстовые сообщения в данных пользователя
    if 'media' in user_data_entry and user_data_entry['media']:
        # Ищем последний введенный текст
        last_text_entry = next((entry['content'] for entry in reversed(user_data_entry['media']) if entry['type'] == 'text'), None)

        # Проверяем, извлечен ли последний текст корректно
        if last_text_entry:
            # Извлекаем текстовое содержание из JSON-объекта
            plain_text = extract_text_from_json(last_text_entry)

            # Логируем содержание plain_text
            logging.info(f"Extracted text for user {user_id}: {plain_text}")

            # Формируем запрос для Gemini с текстом для проверки
            query = (
                f"Проверь данный текст в первую очередь на орфографические и пунктуационные ошибки. "
                f"Сначала перечисли самые грубые и очевидные ошибки, такие как пропущенные буквы или очевидные опечатки "
                f"(например, 'даска' вместо 'доска'), перечисли их, либо напиши что явных ошибок в тексте не найдено "
                f"Затем представь полностью исправленный вариант текста. "
                f"В конце добавь любые менее значительные рекомендации по улучшению текста, если они есть. "
                f"Если ошибок не найдено и замечаний нет, укажи, что текст полностью корректен.\n\n{plain_text}"
)            
            # Логируем сформированный запрос
            logging.info(f"Generated query for Gemini: {query}")

            # Отправка запроса в модель Gemini для проверки
            response = generate_gemini_response(user_id, query=query, use_context=False)

            # Проверяем, что модель вернула ответ, и отправляем его пользователю
            await update.callback_query.message.reply_text(response or "Не удалось выполнить проверку текста.")
        else:
            # Ответ пользователю, если текст не найден
            await update.callback_query.answer("Последний текст не найден.")
    else:
        # Ответ пользователю, если нет текстовых сообщений
        await update.callback_query.answer("Тексты для проверки отсутствуют.")


async def handle_new_text_from_image(update: Update, context: CallbackContext, index, media) -> int:
    user_id = update.message.from_user.id
    message_text = update.message.text

    # Проверка наличия данных пользователя и медиа
    if user_id not in user_data or 'media' not in user_data[user_id]:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text='🚫 Ошибка: данные пользователя не найдены. Попробуйте снова.',
            reply_to_message_id=update.message.message_id
        )
        return ConversationHandler.END

    # Проверка корректности индекса
    if not (0 <= index < len(media)):
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text='🚫 Ошибка: недопустимый индекс редактируемого элемента.',
            reply_to_message_id=update.message.message_id
        )
        return ConversationHandler.END

    # Применение разметки к тексту
    formatted_text = apply_markup(message_text)

    # Замена изображения на текст в media
    media[index] = {
        'type': 'text',
        'content': formatted_text
    }
    user_data[user_id]['media'] = media  # Обновляем данные пользователя

    # Уведомление пользователя
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text='✅ Содержание изменено.',
        reply_to_message_id=update.message.message_id
    )

    # Удаляем индекс редактирования только после успешной замены
    del context.user_data['editing_index']

    # Удаляем предыдущее сообщение с кнопками содержания статьи, если оно существует
    if 'last_content_message_id' in user_data[user_id]:
        try:
            await context.bot.delete_message(
                chat_id=update.message.chat_id, 
                message_id=user_data[user_id]['last_content_message_id']
            )
        except Exception as e:
            print(f"Ошибка при удалении сообщения с содержанием: {e}")

        # Настройки пагинации
    items_per_page = 30  # Количество кнопок на странице
    total_pages = (len(media) + items_per_page - 1) // items_per_page  # Общее количество страниц
    current_page = user_data[user_id].get('current_page', 0)  # Текущая страница

    # Ограничиваем текущую страницу
    current_page = max(0, min(current_page, total_pages - 1))        

    # Создаём новый список кнопок для содержания статьи
    keyboard = []
    image_counter = 1  # Счётчик для изображений
    start_idx = current_page * items_per_page
    end_idx = min(start_idx + items_per_page, len(media))
    for idx in range(start_idx, end_idx):
        item = media[idx]
        if item['type'] == 'text':
            text = item['content']
            
            # Извлечение текста, если нужно
            if isinstance(text, dict) and 'children' in text:
                text = ''.join(child['children'][0] for child in text['children'] if isinstance(child, dict) and 'children' in child)
            
            preview_text = (text[:12] + '...') if len(text) > 12 else text
        else:  # Если элемент — это изображение
            preview_text = f"{image_counter} изображение"  # Нумерация только для изображений
            image_counter += 1  # Увеличиваем счётчик только для изображений
        
        # Добавляем кнопки для предпросмотра, редактирования и удаления
        keyboard.append([
            InlineKeyboardButton(text=str(preview_text), callback_data=f"preview_{idx}"),
            InlineKeyboardButton(text="Редактировать", callback_data=f"edit_{idx}"),
            InlineKeyboardButton(text="Удалить", callback_data=f"delete_{idx}"),
        ])
    # Добавляем кнопки навигации, если это не первая страница
    if current_page > 0:
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='page_down')])
    
    # Добавляем кнопки навигации, если это не последняя страница
    if current_page < total_pages - 1:
        keyboard.append([InlineKeyboardButton("Вперёд ➡️", callback_data='page_up')])
    
    keyboard.append([InlineKeyboardButton("🌌 Предпросмотр 🌌", callback_data='preview_article')])
    keyboard.append([InlineKeyboardButton("Помощь и разметка", callback_data='help_command')])
    keyboard.append([InlineKeyboardButton("Найти автора, аниме или проверить на ИИ", callback_data='start_search')])
    keyboard.append([InlineKeyboardButton("🌠 К Завершению Публикации 🌠", callback_data='create_article')])
    # Отправляем новое сообщение с обновлённым списком кнопок
    reply_markup = InlineKeyboardMarkup(keyboard)
    sent_message = await context.bot.send_message(
        chat_id=update.message.chat_id,
        text='📝 Текущее содержание статьи3:',
        reply_markup=reply_markup
    )

    # Сохраняем ID нового сообщения с кнопками
    user_data[user_id]['last_content_message_id'] = sent_message.message_id
    user_data[user_id]['current_page'] = current_page 

    del context.user_data['editing_index']

    return ASKING_FOR_IMAGE
        


@retry(wait=wait_fixed(2), stop=stop_after_attempt(3))
def make_request(url, data):
    response = requests.post(url, json=data, timeout=30)
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

async def send_media_group_with_retries(update, media_group, max_retries=3, delay=2):
    retries = 0
    # Определяем, является ли событие сообщением или callback-запросом
    if update.message:
        message_to_reply = update.message
    elif update.callback_query:
        message_to_reply = update.callback_query.message
    else:
        return False  # Не удалось определить источник

    while retries < max_retries:
        try:
            await message_to_reply.reply_media_group(media_group)
            return True  # Успешная отправка
        except Exception as e:
            logger.error(f"Failed to send media group: {e}")
            retries += 1
            if retries < max_retries:
                logger.info(f"Retrying in {delay} seconds... (Attempt {retries}/{max_retries})")
                await asyncio.sleep(delay)
    return False  # Если все попытки не удались


# Метод для отправки одного изображения с повторными попытками и задержкой
async def send_photo_with_retries(update, photo_url, caption, parse_mode, reply_markup=None, max_retries=3, delay=2):
    retries = 0
    # Определяем, является ли событие сообщением или callback-запросом
    if update.message:
        message_to_reply = update.message
    elif update.callback_query:
        message_to_reply = update.callback_query.message
    else:
        return False  # Не удалось определить источник

    while retries < max_retries:
        try:
            await message_to_reply.reply_photo(
                photo=photo_url,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup  # Добавляем возможность передать клавиатуру с кнопками
            )
            return True  # Успешная отправка
        except Exception as e:
            logger.error(f"Failed to send photo: {e}")
            retries += 1
            if retries < max_retries:
                logger.info(f"Retrying in {delay} seconds... (Attempt {retries}/{max_retries})")
                await asyncio.sleep(delay)
    return False  # Если все попытки не удались



async def delete_last(update: Update, context: CallbackContext) -> None:
    # Определяем, откуда пришёл запрос - через команду или через кнопку
    if update.message:  # Если это сообщение
        user_id = update.message.from_user.id
        chat_id = update.message.chat_id
        message_id = update.message.message_id
    elif update.callback_query:  # Если это callback через кнопку
        user_id = update.callback_query.from_user.id
        chat_id = update.callback_query.message.chat_id
        message_id = update.callback_query.message.message_id
    else:
        return  # Если это что-то другое, то ничего не делаем

    if user_id in user_data and 'media' in user_data[user_id]:
        if user_data[user_id]['media']:
            last_item = user_data[user_id]['media'].pop()  # Удаляем последний элемент
            item_type = last_item['type']
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Удалён последний элемент типа: {item_type}\n\nДля предпросмотра введите команду /preview",
                reply_to_message_id=message_id
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Ваша статья пуста. Нет элементов для удаления.",
                reply_to_message_id=message_id
            )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text="У вас нет активной статьи для редактирования. Используйте /start для начала.",
            reply_to_message_id=message_id
        )



from telegram import InlineKeyboardButton, InlineKeyboardMarkup

async def preview_article(update: Update, context: CallbackContext) -> None:
    # Проверяем, вызвано ли через сообщение или инлайн-кнопку
    if update.message:
        user_id = update.message.from_user.id
    elif update.callback_query:
        user_id = update.callback_query.from_user.id
    else:
        return

    if user_id in user_data:
        try:
            author_name = "Anemone"
            author_link = "https://t.me/anemonn"
            artist_link = user_data[user_id].get('artist_link', '')
            media = user_data[user_id].get('media', [])
            title = user_data[user_id].get('title', 'Предпросмотр статьи')

            # Создаём контент для страницы
            content = [{'tag': 'p', 'children': [{'tag': 'a', 'attrs': {'href': artist_link}, 'children': [artist_link]}]}]

            # Добавление изображений с разделителями
            for index, item in enumerate(media):
                if item['type'] == 'text':
                    content.append({'tag': 'p', 'children': [item['content']]})
                elif item['type'] == 'image':
                    # Создаем фигуру с изображением и подписью
                    figure_content = [{'tag': 'img', 'attrs': {'src': item['url']}}]
                    if item.get('caption'):
                        figure_content.append({'tag': 'figcaption', 'children': [item['caption']]})

                    content.append({'tag': 'figure', 'children': figure_content})

                    # Добавление разделителя после изображения, если это не последнее изображение
                    if index < len(media) - 1:
                        content.append({'tag': 'hr'})

            # Создание статьи в Telegra.ph
            response = requests.post('https://api.telegra.ph/createPage', json={
                'access_token': TELEGRAPH_TOKEN,
                'title': title,
                'author_name': author_name,
                'author_url': author_link,
                'content': content
            })
            response.raise_for_status()
            response_json = response.json()

            if response_json.get('ok'):
                preview_url = f"https://telegra.ph/{response_json['result']['path']}"

                # Создание кнопки
                keyboard = [[InlineKeyboardButton("🌠 К Завершению Публикации 🌠", callback_data='create_article')]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                # Отправляем предпросмотр пользователю
                if update.message:
                    await update.message.reply_text(f'Предпросмотр статьи: {preview_url}', reply_markup=reply_markup)
                elif update.callback_query:
                    await update.callback_query.message.reply_text(f'Предпросмотр статьи: {preview_url}', reply_markup=reply_markup)
            else:
                if update.message:
                    await update.message.reply_text('Ошибка при создании предпросмотра статьи.')
                elif update.callback_query:
                    await update.callback_query.message.reply_text('Ошибка при создании предпросмотра статьи.')

        except requests.RequestException as e:
            if update.message:
                await update.message.reply_text(f'Ошибка при создании предпросмотра: {e}')
            elif update.callback_query:
                await update.callback_query.message.reply_text(f'Ошибка при создании предпросмотра: {e}')
    else:
        if update.message:
            await update.message.reply_text('Нет данных для предпросмотра. Начните с отправки текста или изображений.')
        elif update.callback_query:
            await update.callback_query.message.reply_text('Нет данных для предпросмотра. Начните с отправки текста или изображений.')





async def handle_preview_button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()  # Подтверждаем нажатие

    if query.data == 'preview_article':
        await preview_article(update, context)

async def handle_delete_button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()  # Подтверждаем нажатие

    if query.data == 'delete_last':
        await delete_last(update, context)


async def handle_help_text_button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()  # Подтверждаем нажатие

    if query.data == 'help_command':
        await help_command(update, context)


async def handle_create_article_button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()  # Ответ на нажатие

    # Вызываем функцию publish, которая создаёт статью
    await publish(update, context)


async def handle_restart_button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()  # Подтверждаем нажатие

    if query.data == 'restart':
        await restart(update, context)

async def handle_edit_button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()  # Подтверждаем нажатие

    if query.data == 'edit_article':
        await edit_article(update, context)   

# Добавьте обработчик для переключения страниц
async def handle_page_change(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    
    if query.data == 'page_down':
        user_data[user_id]['current_page'] -= 1
    elif query.data == 'page_up':
        user_data[user_id]['current_page'] += 1

    await edit_article(update, context)  # Повторно вызываем функцию редактирования


# Функция для рекурсивного поиска изображений
def count_images_in_content(content):
    image_count = 0
    for item in content:
        if isinstance(item, dict):
            if item.get('tag') == 'img':
                image_count += 1
            elif item.get('tag') == 'figure' and 'children' in item:
                # Если есть тег figure, проверяем его содержимое
                image_count += count_images_in_content(item['children'])
    return image_count

# Основная функция публикации
async def publish(update: Update, context: CallbackContext) -> None:
    # Проверяем, пришло ли событие от сообщения или от callback_query
    if update.message:
        user_id = update.message.from_user.id
        message_to_reply = update.message
    elif update.callback_query:
        user_id = update.callback_query.from_user.id
        message_to_reply = update.callback_query.message
    else:
        return  # Если ни того, ни другого нет, просто выйдем

    if user_id in user_data:
        try:
            author_name = "Anemone"
            author_link = "https://t.me/anemonn"
            artist_link = user_data[user_id]['artist_link']
            media = user_data[user_id].get('media', [])
            title = user_data[user_id].get('title', 'test')

            # Извлекаем фразу перед "Автор", если она есть
            extra_phrase = user_data[user_id].get('extra_phrase', "")
            author_name_final = user_data[user_id].get('author_name', '')

            # Формируем строку с фразой перед "Автор", если она есть
            if extra_phrase:
                author_line = f"{extra_phrase}\nАвтор: {author_name_final}"
            else:
                author_line = f"Автор: {author_name_final}"

            # Проверяем, есть ли авторское имя
            if not author_name_final:
                author_line = title  # Если это заголовок из "^...^", то используем только заголовок
            else:
                # Формируем строку с фразой перед "Автор", если она есть
                if extra_phrase:
                    author_line = f"{extra_phrase}\nАвтор: {author_name_final}"
                else:
                    author_line = f"Автор: {author_name_final}"


            # Создание статьи в Telegra.ph
            content = [
                {
                    'tag': 'p',
                    'children': [
                        {
                            'tag': 'a',
                            'attrs': {'href': artist_link},
                            'children': [artist_link]
                        }
                    ]
                }
            ]

            # Добавление изображений с разделителями
            for index, item in enumerate(media):
                if item['type'] == 'text':
                    content.append({'tag': 'p', 'children': [item['content']]})
                elif item['type'] == 'image':
                    # Создаем фигуру с изображением и подписью
                    figure_content = [
                        {'tag': 'img', 'attrs': {'src': item['url']}},
                    ]
                    if item.get('caption'):
                        figure_content.append({'tag': 'figcaption', 'children': [item['caption']]})

                    content.append({'tag': 'figure', 'children': figure_content})

                    if index < len(media) - 1:
                        content.append({'tag': 'hr'})

            content.append({'tag': 'hr'})
            content.append({
                'tag': 'i',
                'children': [f'Оригиналы доступны в браузере через меню (⋮)']
            })

            response = requests.post('https://api.telegra.ph/createPage', json={
                'access_token': TELEGRAPH_TOKEN,
                'title': title,
                'author_name': author_name,
                'author_url': author_link,
                'content': content
            })
            response.raise_for_status()
            response_json = response.json()

            if response_json.get('ok'):
                article_url = f"https://telegra.ph/{response_json['result']['path']}"

                article_response = requests.get(f'https://api.telegra.ph/getPage?access_token={TELEGRAPH_TOKEN}&path={response_json["result"]["path"]}&return_content=true')
                article_response.raise_for_status()
                article_data = article_response.json()

                image_count = count_images_in_content(content)
                logger.info(f"Number of images detected: {image_count}")

                if image_count > 1:
                    # Фильтруем только изображения, чтобы избежать смешивания с текстом
                    image_media = [item for item in media if item['type'] == 'image']
                    
                    # Разделение только изображений на группы по 10
                    media_groups = [image_media[i:i + 10] for i in range(0, len(image_media), 10)]
                    media_group_data = []
                    
                    # Для хранения информации о том, был ли добавлен текст
                    text_added = False

                    for group in media_groups:
                        media_group = []

                        for idx, item in enumerate(group):
                            caption = None
                            
                            # Если текст ещё не добавлен, добавляем подпись к первому изображению
                            if not text_added:
                                caption = f'{author_line}\n<a href="{article_url}">Оригинал</a>'
                                text_added = True

                            # Добавляем только изображения в медиа-группу
                            media_group.append(InputMediaPhoto(media=item['url'], caption=caption, parse_mode='HTML' if caption else None))
                            
                            # Запоминаем данные для последующего использования
                            media_group_data.append({
                                "file_id": item['url'],
                                "caption": caption,
                                "parse_mode": 'HTML' if caption else None
                            })

                        # Используем функцию для повторных попыток отправки медиа-группы
                        success = await send_media_group_with_retries(update, media_group)
                        if not success:
                            await message_to_reply.reply_text(f'🚫Ошибка при отправке медиа-группы.')
                            return

                        if caption:
                            await message_to_reply.reply_text(
                                f"✅ Медиагруппа отправлена с подписью.",
                                disable_web_page_preview=True
                            )

                    media_group_storage[user_id] = media_group_data



                if image_count == 1:
                    single_image = next((item for item in media if item['type'] == 'image'), None)
                    if single_image:
                        caption = f'{author_line}\n<a href="{article_url}">Оригинал</a>'
                        
                        media_group_storage[user_id] = [{
                            "file_id": single_image['url'],
                            "caption": caption,
                            "parse_mode": 'HTML'
                        }]
                        
                        # Проверяем, откуда пришел вызов - из команды или инлайн-кнопки
                        success = await send_photo_with_retries(
                            update=update,
                            photo_url=single_image['url'],
                            caption=caption,
                            parse_mode='HTML'
                        )
                        if not success:
                            await message_to_reply.reply_text('🚫Ошибка при отправке изображения. /restart')
                            return

                elif image_count == 0:
                    message_with_link = f'{author_line}\n<a href="{article_url}">Оригинал</a>'
                    await message_to_reply.reply_text(message_with_link, parse_mode='HTML')

                image_text = (
                    "изображение" if image_count % 10 == 1 and image_count % 100 != 11
                    else "изображения" if 2 <= image_count % 10 <= 4 and (image_count % 100 < 10 or image_count % 100 >= 20)
                    else "изображений"
                )

                await message_to_reply.reply_text(
                    f'====--- В статье {image_count} {image_text} ---===='
                )

                publish_data[user_id] = {
                    'title': title,
                    'article_url': article_url,
                    'image_count': image_count,
                    'author_line': author_line
                }

                del user_data[user_id]
                await message_to_reply.reply_text(
                    '✅Все данные для публикации успешно созданы.\n'
                    'Но сейчас они видны только вам, чтобы поделиться ими с администрацией просто нажмите /share '
                    '(эта кнопка будет работать только до вашего следующего нажатия команды publish).\n\n'
                    'Либо создайте другую публикацию, если что-то пошло не так.\n\n'
                    '<i>Вы так же можете ввести команду /send, чтобы перейти в режим прямой связи с администрацией. '
                    'Просто нажмите на эту команду, и после этого любые ваши сообщения, отправленные боту, будут сразу дублироваться администрации. '
                    'Таким образом, вы можете задать вопросы, отправить дополнительные файлы, изображения и пояснения касательно вашей публикации, '
                    'сообщить об обнаруженных багах или что-то ещё.</i>\n\n'
                    '✅*Бот перезапущен успешно.*\n\n'
                    '(=^・ェ・^=)',
                    parse_mode='HTML',  # Добавляем parse_mode
                    reply_markup=ReplyKeyboardRemove()
                )
                logger.info(f"User {user_id}'s data cleared and process completed.")
                await message_to_reply.reply_text('********************************************************')
                # Вызов команды restart
                await restart(update, context)

                return ConversationHandler.END
            else:
                await message_to_reply.reply_text('🚫Ошибка при создании статьи. /restart')
        except requests.RequestException as e:
            logger.error(f"Request error: {e}")
            await message_to_reply.reply_text('🚫Ошибка при создании статьи. /restart')
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            await message_to_reply.reply_text('🚫Произошла неожиданная ошибка. /restart')


import ast



async def unknown_message(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    if user_id not in user_data:
        await update.message.reply_text('🚫Неизвестное сообщение. Пожалуйста, отправьте ссылку на автора, имя автора или изображение. В случае если это сообщение повторяется нажмите /restart')
    else:
        # Обработка сообщений в процессе
        if user_data[user_id]['status'] == 'awaiting_artist_link':
            await handle_artist_link(update, context)
        elif user_data[user_id]['status'] == 'awaiting_author_name':
            await handle_author_name(update, context)
        elif user_data[user_id]['status'] == 'awaiting_image':
            await handle_image(update, context)
            
# Функция для разбиения списка изображений на группы по 10
def chunk_images(images, chunk_size=10):
    for i in range(0, len(images), chunk_size):
        yield images[i:i + chunk_size]

TELEGRAM_API_TIMEOUT = 20  # Увеличьте время ожидания        

async def share(update: Update, context: CallbackContext) -> None:
    global publish_data
    user_id = update.message.from_user.id
    if user_id in publish_data:
        data = publish_data[user_id]
        title = data['title']
        article_url = data['article_url']
        author_line = data['author_line']

        try:
            # Увеличиваем таймаут для запроса к Telegra.ph
            article_response = requests.get(f'https://api.telegra.ph/getPage?path={article_url.split("/")[-1]}&return_content=true', timeout=10)
            article_response.raise_for_status()
            article_data = article_response.json()

            # Ищем изображения в контенте, включая теги figure
            images = []
            for node in article_data['result']['content']:
                if node.get('tag') == 'img' and 'attrs' in node and 'src' in node['attrs']:
                    images.append(node['attrs']['src'])
                elif node.get('tag') == 'figure' and 'children' in node:
                    images_in_figure = count_images_in_content(node['children'])
                    images.extend([img['attrs']['src'] for img in node['children'] if img.get('tag') == 'img' and 'attrs' in img and 'src' in img['attrs']])

            # Отправляем сообщение с текстом
            message_with_link = f'Пользователь {update.message.from_user.username} предложил:\n {author_line}\n<a href="{article_url}">Оригинал</a>'
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message_with_link, parse_mode='HTML', disable_web_page_preview=True)

            # Отправляем изображения группами по 10
            if images:
                for image_group in chunk_images(images):
                    media_group = [InputMediaPhoto(image) for image in image_group]
                    await context.bot.send_media_group(chat_id=GROUP_CHAT_ID, media=media_group)
                    await asyncio.sleep(1)  # Задержка в 1 секунду между отправками медиа-групп
            else:
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text='Изображений в статье нет.')

            # Сообщение об успешной отправке
            await update.message.reply_text('✅Ваше предложение отправлено администрации. Спасибо.')

        except Timeout:
            logger.warning("Request to Telegram timed out, but message sent successfully.")
            await update.message.reply_text('✅Ваше предложение отправлено, но обработка статьи заняла больше времени. Вероятно это произошло из-за большого объёма статьи, на всякий случай сообщите об этом через команду /send и прикрепите там ссылку на вашу статью /restart для перезапуска')
        except Exception as e:
            logger.error(f"Failed to process article: {e}")
            await update.message.reply_text('✅Ваше предложение отправлено, но обработка статьи заняла больше времени. Вероятно это произошло из-за большого объёма статьи, на всякий случай сообщите об этом через команду /send и прикрепите там ссылку на вашу статью /restart для перезапуска')
    else:
        await update.message.reply_text('🚫Нет данных для предложения.')

async def send_mode(update: Update, context: CallbackContext) -> None:
    """Включение режима дублирования сообщений."""
    user_id = update.message.from_user.id
    users_in_send_mode.add(user_id)
    await update.message.reply_text('🔄 Режим прямой связи включен. Все последующие сообщения будут дублироваться администрации. Для завершения режима введите /fin')
    
async def fin_mode(update: Update, context: CallbackContext) -> None:
    """Выключение режима дублирования сообщений и возврат к изначальной логике."""
    user_id = update.message.from_user.id
    if user_id in users_in_send_mode:
        users_in_send_mode.remove(user_id)
        await update.message.reply_text('✅ Режим пересылки сообщений администрации отключен. Бот вернулся к своему основному режиму работы.')
    else:
        await update.message.reply_text('❗ Вы не активировали режим дублирования.')

from telegram import InputMediaPhoto, InputMediaVideo, InputMediaDocument

async def duplicate_message(update: Update, context: CallbackContext) -> None:
    """Дублирование сообщений пользователя в группу, включая медиа-группы, одиночные сообщения и документы."""
    user = update.message.from_user
    user_name = user.username if user.username else user.full_name
    message_prefix = f"{user_name} отправил сообщение:"

    if user.id in users_in_send_mode:
        # Если сообщение является частью медиа-группы
        if update.message.media_group_id:
            media_group = []
            messages = await context.bot.get_updates(offset=update.update_id - 10)  # Получаем несколько предыдущих сообщений для сборки медиа-группы

            # Фильтрация сообщений с тем же media_group_id
            for message in messages:
                if message.message.media_group_id == update.message.media_group_id:
                    if message.message.photo:
                        media_group.append(InputMediaPhoto(message.message.photo[-1].file_id, caption=message.message.caption if message.message.caption else ""))
                    elif message.message.video:
                        media_group.append(InputMediaVideo(message.message.video.file_id, caption=message.message.caption if message.message.caption else ""))
                    elif message.message.document:
                        media_group.append(InputMediaDocument(message.message.document.file_id, caption=message.message.caption if message.message.caption else ""))

            # Отправляем медиа-группу, если она есть
            if media_group:
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message_prefix)
                await context.bot.send_media_group(chat_id=GROUP_CHAT_ID, media=media_group)
                await update.message.reply_text("Сообщение успешно отправлено администрации. Для завершения режима дублирования введите /fin")

        # Обработка одиночных текстовых сообщений
        elif update.message.text:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"{message_prefix}\n{update.message.text}")
            await update.message.reply_text("Сообщение успешно отправлено администрации. Для завершения режима дублирования введите /fin")

        # Обработка одиночных фото
        elif update.message.photo:
            photo = update.message.photo[-1].file_id  # Получаем последнюю фотографию с наибольшим разрешением
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message_prefix)
            await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=photo, caption=update.message.caption)
            await update.message.reply_text("Сообщение успешно отправлено администрации. Для завершения режима дублирования введите /fin")

        # Обработка одиночных документов (включая изображения, отправленные как файл)
        elif update.message.document:
            doc = update.message.document.file_id
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message_prefix)
            await context.bot.send_document(chat_id=GROUP_CHAT_ID, document=doc, caption=update.message.caption)
            await update.message.reply_text("Сообщение успешно отправлено администрации. Для завершения режима дублирования введите /fin")

        # Обработка одиночных видео
        elif update.message.video:
            video = update.message.video.file_id
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message_prefix)
            await context.bot.send_video(chat_id=GROUP_CHAT_ID, video=video, caption=update.message.caption)
            await update.message.reply_text("Сообщение успешно отправлено администрации. Для завершения режима дублирования введите /fin")

        # Обработка стикеров
        elif update.message.sticker:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message_prefix)
            await context.bot.send_sticker(chat_id=GROUP_CHAT_ID, sticker=update.message.sticker.file_id)
            await update.message.reply_text("Сообщение успешно отправлено администрации. Для завершения режима дублирования введите /fin")

        # Обработка аудио
        elif update.message.audio:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message_prefix)
            await context.bot.send_audio(chat_id=GROUP_CHAT_ID, audio=update.message.audio.file_id, caption=update.message.caption)
            await update.message.reply_text("Сообщение успешно отправлено администрации. Для завершения режима дублирования введите /fin")

        # Добавьте обработку других типов сообщений по мере необходимости
    else:
        # Если пользователь не в режиме дублирования, продолжаем с основной логикой
        await start(update, context)




def main() -> None:
    load_context_from_firebase()  # Загружаем историю чатов в user_contexts
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Настройка ConversationHandler для основной логики
    conversation_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('edit', edit_article),
            MessageHandler(filters.TEXT & ~filters.COMMAND, main_logic)  # Основная логика
        ],
        states={
            ASKING_FOR_ARTIST_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_artist_link)],
            ASKING_FOR_AUTHOR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_author_name)],
            EDITING_FRAGMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_text)],
            ASKING_FOR_IMAGE: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_new_image),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
            ],
        },
        fallbacks=[MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_message)],
        per_user=True
    )

    search_handler = ConversationHandler(
        entry_points=[CommandHandler('search', start_search)],
        states={
            ASKING_FOR_FILE: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file),
                MessageHandler(filters.ALL & ~filters.COMMAND, unknown_search_message),
            ],
        },
        fallbacks=[
            CommandHandler('fin_search', finish_search),
            CommandHandler('restart', restart),  # Добавлен обработчик для /restart
        ],
        per_user=True,
        allow_reentry=True
    )

    ocr_handler = ConversationHandler(
        entry_points=[CommandHandler('ocr', start_ocr)],
        states={
            ASKING_FOR_FILE: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file),
                MessageHandler(filters.ALL & ~filters.COMMAND, unknown_ocr_message),
            ],
        },
        fallbacks=[
            CommandHandler('fin_ocr', finish_ocr),
            CommandHandler('restart', restart),  # Добавлен обработчик для /restart
        ],
        per_user=True,
        allow_reentry=True
    )

    gpt_handler = ConversationHandler(
        entry_points=[CommandHandler('gpt', run_gpt)],
        states={
            ASKING_FOR_FILE: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file),
                MessageHandler(filters.ALL & ~filters.COMMAND, unknown_ocr_message),
            ],
        },
        fallbacks=[
            CommandHandler('fin_gpt', stop_gpt),
            CommandHandler('restart', restart),  # Добавлен обработчик для /restart
        ],
        per_user=True,
        allow_reentry=True
    )

    # Добавляем обработчики команд
    application.add_handler(CallbackQueryHandler(handle_edit_button, pattern='edit_article'))
    application.add_handler(CallbackQueryHandler(handle_delete_button, pattern='delete_last'))
    application.add_handler(CallbackQueryHandler(handle_edit_delete, pattern='^edit_|^delete_'))
    application.add_handler(CallbackQueryHandler(handle_preview_button, pattern='preview_article'))
    application.add_handler(CallbackQueryHandler(handle_create_article_button, pattern='create_article'))
    application.add_handler(CallbackQueryHandler(handle_help_text_button, pattern='help_command'))
    application.add_handler(CallbackQueryHandler(handle_restart_button, pattern='restart'))
    application.add_handler(CallbackQueryHandler(handle_page_change, pattern='^page_')) 
    application.add_handler(CallbackQueryHandler(ai_or_not, pattern='ai_or_not'))
    application.add_handler(CallbackQueryHandler(finish_search, pattern='finish_search')) 
    application.add_handler(CallbackQueryHandler(finish_ocr, pattern='finish_ocr'))
    application.add_handler(CallbackQueryHandler(stop_gpt, pattern='stop_gpt'))      
    application.add_handler(CallbackQueryHandler(start_search, pattern='start_search'))
    application.add_handler(CallbackQueryHandler(start_ocr, pattern='start_ocr'))
    application.add_handler(CallbackQueryHandler(button_ocr, pattern='recognize_text'))
    application.add_handler(CallbackQueryHandler(button_ocr, pattern='recognize_plant'))
    application.add_handler(CallbackQueryHandler(button_more_plants_handler, pattern='plant_\\d+'))
    application.add_handler(CallbackQueryHandler(gpt_plants_help_handler, pattern='^gpt_plants_help$'))
    application.add_handler(CallbackQueryHandler(gpt_plants_more_handler, pattern='^gpt_plants_more$'))
    application.add_handler(CallbackQueryHandler(text_rec_with_gpt, pattern='text_rec_with_gpt$'))
    application.add_handler(CallbackQueryHandler(handle_check_text, pattern='^check_text$'))
    application.add_handler(CallbackQueryHandler(run_gpt, pattern='run_gpt')) 
    application.add_handler(CallbackQueryHandler(reset_dialog, pattern='^reset_dialog$')) 
    application.add_handler(CommandHandler('send', send_mode))
    application.add_handler(CommandHandler('fin', fin_mode))
    application.add_handler(CommandHandler('restart', restart))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('publish', publish))
    application.add_handler(CommandHandler('preview', preview_article))  # Добавляем обработчик для /preview
    application.add_handler(CommandHandler('delete', delete_last))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, duplicate_message))  # Обработчик дублирования сообщений
    application.add_handler(CommandHandler('share', share))  # Добавляем обработчик для /share

    # Добавляем обработчики для команд /search и /fin_search
    application.add_handler(search_handler)
    application.add_handler(CommandHandler('fin_search', finish_search))  # Обработчик команды /fin_search

    # Добавляем обработчики для команд /ocr и /fin_ocr
    application.add_handler(ocr_handler)
    application.add_handler(CommandHandler('fin_ocr', finish_ocr)) 

    # Добавляем обработчики для команд /gpt и /fin_gpt
    application.add_handler(gpt_handler)
    application.add_handler(CommandHandler('fin_gpt', stop_gpt))     

    # Добавляем основной conversation_handler
    application.add_handler(conversation_handler)

    logger.info("Bot started and polling...")  
    keep_alive()#запускаем flask-сервер в отдельном потоке. Подробнее ниже...
    application.run_polling() #запуск бота    

if __name__ == '__main__':
    main()
