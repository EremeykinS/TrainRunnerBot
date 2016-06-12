from config import *
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import logging
import sqlite3

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger('TrainRunnerBot.'+__name__)

# States are saved in a dict that maps chat_id -> state
state = dict()

citys = ("Москва", "Санкт-Петербург", "Новосибирск", "Усть-Илимск", "Северодвинск", "д. Гадюкино")

# Define all possible states of a chat
SELECT_CITY, MAIN_MENU = range(2)


def start(bot, update):
    text = "Выберите Ваш город из списка"
    city_kbd = [[city] for city in citys]
    city_kbd = telegram.ReplyKeyboardMarkup(city_kbd)
    state[update.message.from_user.id] = SELECT_CITY
    bot.sendMessage(update.message.chat_id, text=text, reply_markup=city_kbd)


def help(bot, update):
    bot.sendMessage(update.message.chat_id, text='Help!')


def echo(bot, update):
    bot.sendMessage(update.message.chat_id, text=update.message.text)


def error(bot, update, error):
    logger.warn('Update "%s" caused error "%s"' % (update, error))


def chat(bot, update):
    user_id = update.message.from_user.id
    text = update.message.text
    chat_state = state.get(user_id, MAIN_MENU)

    if chat_state == SELECT_CITY:
        city = text
        if city in citys:
            # DB connection
            con = sqlite3.connect('/home/es/PycharmProjects/TrainRunnerBot/DB.sqlite')
            cur = con.cursor()
            cur.execute('INSERT INTO citys (uid, city) VALUES ("' + str(user_id) + '", "' + city + '")')
            con.commit()
            text = "Теперь можете искать электрички в своем городе"
            main_kbd = [[[1], [2]], [[3], [4]]]
            main_kbd = telegram.ReplyKeyboardMarkup(main_kbd)
            state[user_id] = MAIN_MENU
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=main_kbd)
        else:
            del state[user_id]
            text = "Теперь не сможете искать электрички в своем городе"
            bot.sendMessage(update.message.from_user.id, text=text)


def main():
    # Create the EventHandler and pass it your bot's token.
    updater = Updater(telegram_token)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help))

    # on noncommand i.e message - echo the message on Telegram
    dp.add_handler(MessageHandler([Filters.text], chat))

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    updater.start_polling()

    updater.idle()


if __name__ == '__main__':
    main()
