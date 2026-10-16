from pyrogram import filters
from pyrogram.enums import ParseMode
from bot import Bot
from config import OWNER_ID
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from database.database import get_user_enrollments


@Bot.on_callback_query(filters.regex(r"^(my_enrollments|back_start|close)$"))
async def cb_handler(client: Bot, query: CallbackQuery):
    data = query.data

    if data == "my_enrollments":
        user_id = query.from_user.id
        enrollments = await get_user_enrollments(user_id)

        if not enrollments:
            await query.answer("You currently have no active enrollments.", show_alert=True)
            return

        text = "<b>Your Active Enrollments</b>\n\n"
        for e in enrollments:
            status_text = "🟢 Active" if e["status"] == "active" else "🔴 Inactive"
            text += f"<blockquote><b>Batch ID:</b> <code>{e['batch_id']}</code>\n<b>Status:</b> {status_text}</blockquote>\n\n"

        await query.message.edit_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Return to Dashboard", callback_data="back_start")]]
            )
        )

    elif data == "back_start":
        from config import START_MSG
        caption = START_MSG.format(
            first=query.from_user.first_name,
            last=query.from_user.last_name or "",
            username=f"@{query.from_user.username}" if query.from_user.username else "",
            mention=query.from_user.mention,
            id=query.from_user.id
        )
        reply_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📚 My Enrollments", callback_data="my_enrollments"),
                    InlineKeyboardButton("📞 Support", url="https://t.me/aryan_patel_99")
                ]
            ]
        )
        await query.message.edit_text(
            text=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    elif data == "close":
        await query.message.delete()
    else:
        await query.continue_propagation()