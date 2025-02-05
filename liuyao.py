#!/usr/bin/env python
# pylint: disable=unused-argument
# This program is dedicated to the public domain under the CC0 license.

"""This example showcases how PTBs "arbitrary callback data" feature can be used.

For detailed info on arbitrary callback data, see the wiki page at
https://github.com/python-telegram-bot/python-telegram-bot/wiki/Arbitrary-callback_data

Note:
To use arbitrary callback data, you must install PTB via
`pip install "python-telegram-bot[callback-data]"`
"""
import logging
from datetime import datetime, timezone, timedelta
import asyncio

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    PicklePersistence,
    MessageHandler,
    filters,
)
from cozepy import (
    Coze, TokenAuth, Message, ChatEventType, COZE_CN_BASE_URL
)
from telegram.constants import ParseMode
from superbase_client import (
    init_base_tier, get_or_create_user, get_user_daily_limit,
    get_today_usage_count, create_project, update_project_messages, get_user_membership_info
)
from config import TG_BOT_TOKEN, COZE_TOKEN, COZE_BOT_ID

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Coze API 配置
coze = Coze(
    auth=TokenAuth(token=COZE_TOKEN),
    base_url=COZE_CN_BASE_URL
)
BOT_ID = COZE_BOT_ID

async def initialize_user_data(context: ContextTypes.DEFAULT_TYPE, tg_user_id: str, user_name: str) -> None:
    """初始化或更新用户数据"""
    # 获取北京时间
    beijing_tz = timezone(timedelta(hours=8))
    current_date = datetime.now(beijing_tz).date()

    # 获取用户信息
    user = await get_or_create_user(tg_user_id, user_name)
    if not user:
        logger.error(f"Failed to get or create user for tg_user_id: {tg_user_id}")
        return
        
    # 获取用户每日限制和已使用次数
    daily_limit = await get_user_daily_limit(user['user_id'])
    used_count = await get_today_usage_count(user['user_id'], current_date.isoformat())
    
    context.user_data['daily_limit'] = daily_limit
    context.user_data['daily_count'] = daily_limit - used_count
    context.user_data['last_date'] = current_date.isoformat()
    context.user_data['user_id'] = user['user_id']  # 存储用户ID而不是整个用户对象

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """开始算卦流程"""
    # 初始化用户数据
    print("start")
    await initialize_user_data(context, str(update.effective_user.id), update.effective_user.first_name + " " + update.effective_user.last_name)
    
    # 检查是否还有剩余次数
    if context.user_data['daily_count'] <= 0:
        await update.message.reply_text("今日算卦次数已用完，请明日再来。")
        return

    await update.message.reply_text("请输入你所求之事：")
    context.user_data['waiting_for_question'] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理用户输入的问题"""
    if context.user_data.get('waiting_for_question'):
        await initialize_user_data(context, str(update.effective_user.id), update.effective_user.first_name + " " + update.effective_user.last_name)
        
        if context.user_data['daily_count'] <= 0:
            await update.message.reply_text("今日算卦次数已用完，请明日再来。")
            return

        question = update.message.text
        context.user_data['waiting_for_question'] = False
        
        # 创建新的项目记录
        user_id = context.user_data['user_id']
        project = await create_project(user_id, question)
        if not project:
            await update.message.reply_text("系统错误，请稍后再试。")
            return
            
        messages = []
        text_buffer = ""
        message = None
        content_buffer = ""
        BUFFER_SIZE = 30
        
        try:
            user_id = project['project_id']  # 使用项目ID作为会话ID
            for event in coze.chat.stream(
                bot_id=BOT_ID,
                user_id=user_id,
                additional_messages=[
                    Message.build_user_question_text(question),
                ],
            ):
                if event.event == ChatEventType.CONVERSATION_MESSAGE_DELTA:
                    content = event.message.content
                    if content.startswith("![") and "](" in content and content.endswith(")"):
                        img_url = content.split("](")[1][:-1]
                        await update.message.reply_photo(img_url)
                        messages.append({'role': 'assistant', 'content': content})
                    elif content.startswith("开始起卦"):
                        await update.message.reply_text("开始起卦")
                        messages.append({'role': 'assistant', 'content': content})
                    else:
                        content_buffer += content
                        
                        if message is None:
                            message = await update.message.reply_text(
                                f"您所问的事：{question}\n\n卦象解析：\n{content_buffer}"
                            )
                            text_buffer = f"您所问的事：{question}\n\n卦象解析：\n{content_buffer}"
                        elif len(content_buffer) >= BUFFER_SIZE:
                            text_buffer += content_buffer
                            text_buffer = text_buffer.replace("<br><br>", "\n")
                            try:
                                await message.edit_text(text_buffer)
                            except Exception as e:
                                logger.warning(f"Failed to update message: {str(e)}")
                            content_buffer = ""
            
            # 最后更新一次消息和项目记录
            if content_buffer and message:
                text_buffer += content_buffer
                messages.append({'role': 'assistant', 'content': text_buffer})
                try:
                    await message.edit_text(text_buffer)
                    await update_project_messages(project['project_id'], messages)
                except Exception as e:
                    logger.warning(f"Failed to update final message: {str(e)}")
                
        except Exception as e:
            logger.error(f"算卦出错: {str(e)}")
            await update.message.reply_text("抱歉，算卦系统暂时遇到问题，请稍后再试。")
    else:
        await update.message.reply_text("请先发送 /start 开始算卦流程。")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """显示用户资料和今日剩余算卦次数"""
    await initialize_user_data(context, str(update.effective_user.id), update.effective_user.first_name + " " + update.effective_user.last_name)
    
    user_name = update.effective_user.first_name
    daily_count = context.user_data['daily_count']
    daily_limit = context.user_data['daily_limit']
    
    # 获取会员信息
    membership_info = await get_user_membership_info(context.user_data['user_id'])
    
    if membership_info:
        # 转换时间为北京时间
        beijing_tz = timezone(timedelta(hours=8))
        start_time = datetime.fromisoformat(membership_info['start_time']).astimezone(beijing_tz)
        end_time = datetime.fromisoformat(membership_info['end_time']).astimezone(beijing_tz)
        
        await update.message.reply_text(
            f"用户：{user_name}\n"
            f"会员等级：{membership_info['tier_name']}\n"
            f"会员说明：{membership_info['description']}\n"
            f"会员有效期：{start_time.strftime('%Y-%m-%d')} 至 {end_time.strftime('%Y-%m-%d')}\n"
            f"今日剩余算卦次数：{daily_count}\n"
            f"每日限额：{daily_limit}次"
        )
    else:
        await update.message.reply_text(
            f"用户：{user_name}\n"
            f"会员等级：免费用户\n"
            f"今日剩余算卦次数：{daily_count}\n"
            f"每日限额：{daily_limit}次"
        )

def suangua(question: str) -> str:
    """调用 Coze API 进行算卦"""
    logger.info(f"算卦问题：{question}")
    result = ""
    try:
        user_id = f"user_{hash(question)}"
        
        for event in coze.chat.stream(
            bot_id=BOT_ID,
            user_id=user_id,
            additional_messages=[
                Message.build_user_question_text(
                    f"{question}\n"
                ),
            ],
        ):
            print(f"收到事件：{event.event}")
            if event.event == ChatEventType.CONVERSATION_MESSAGE_DELTA:
                result += event.message.content
                print(f"收到消息：{event.message.content}")
            if event.event == ChatEventType.CONVERSATION_CHAT_COMPLETED:
                print(f"Token usage: {event.chat.usage.token_count}")
                
        # 确保所有特殊字符都被正确转义
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in special_chars:
            result = result.replace(char, f'\\{char}')
                
        return result if result else "抱歉，算卦失败，请稍后再试。"
        
    except Exception as e:
        logger.error(f"算卦出错: {str(e)}")
        return "抱歉，算卦系统暂时遇到问题，请稍后再试。"

if __name__ == "__main__":
    # 让 python-telegram-bot 处理事件循环
    application = Application.builder().token(TG_BOT_TOKEN).build()
    # 添加处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling(allowed_updates=Update.ALL_TYPES)