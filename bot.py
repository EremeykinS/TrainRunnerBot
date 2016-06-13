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
SELECT_CITY, MAIN_MENU, START, MEETING = range(4)


def db_transaction(db, q):
    cursor = db.cursor()
    cursor.execute(q)
    print(">>>  ", q)
    print("<<<  ", cursor.fetchall())
    return cursor.fetchall()


def start(bot, update):
    user_id = update.message.from_user.id
    db = sqlite3.connect('DB.sqlite')
    sql_result = db_transaction(db, 'SELECT user_name, city FROM citys WHERE uid=' + str(user_id))
    if not sql_result:
        text = "Добро пожаловать в TrainRunnerBot - сервис для тех, кто хочет всегда успевать на свою электричку. Давайте знакомиться. Я - Олег. А как Вас зовут?"
        state[user_id] = MEETING
        bot.sendMessage(update.message.chat_id, text=text)
    else:
        user_name, user_city = sql_result[0]
        text = user_name + ", Вы уже есть в нашей базе данных. Желаете изменить " + user_city + " на какой-то другой город?"
        sure_kbd = [['Да, изменить'], ['Нет, оставить ' + user_city]]
        sure_kbd = telegram.ReplyKeyboardMarkup(sure_kbd)
        state[update.message.from_user.id] = START
        bot.sendMessage(update.message.chat_id, text=text, reply_markup=sure_kbd)


def helper(bot, update):
    bot.sendMessage(update.message.chat_id, text='Help!')


def error(bot, update, error):
    logger.warn('Update "%s" caused error "%s"' % (update, error))


def chat(bot, update):
    user_id = update.message.from_user.id
    answer = update.message.text
    chat_state = state.get(user_id)
    db = sqlite3.connect('DB.sqlite')
    sql_result = db_transaction(db, 'SELECT user_name FROM citys WHERE uid=' + str(user_id))
    if chat_state != MEETING:
        print(sql_result)
        user_name = sql_result[0][0]
    city_kbd = telegram.ReplyKeyboardMarkup([[city] for city in citys])
    main_kbd = telegram.ReplyKeyboardMarkup([['Раписание', 'Ближайшая электричка'], ['Маршруты', 'INFO']])
    
    if chat_state == MEETING:
        db_transaction(db, 'INSERT INTO citys (uid, user_name) VALUES ("' + str(user_id) + '", "' + answer + '")')
        text = "Отлично, " + answer + ", вот и познакомились! Теперь расскажите мне, в каком городе Вы живете?"
        state[user_id] = SELECT_CITY
        bot.sendMessage(update.message.chat_id, text=text, reply_markup=city_kbd)

    elif chat_state == START:
        if answer == 'Да, изменить':
            text = user_name + ", пожалуйста, выберите свой город из списка"
            state[user_id] = SELECT_CITY
            bot.sendMessage(update.message.chat_id, text=text, reply_markup=city_kbd)
        else:
            del state[user_id]
            text = "Хорошо, " + user_name + ". Я понял, город менять не будем. Предлагаю продолжить пользоваться раписанием"
            bot.sendMessage(update.message.chat_id, text=text, reply_markup=main_kbd)
            
    elif chat_state == SELECT_CITY:
        if answer in citys:
            sql_result = db_transaction(db, 'SELECT city FROM citys WHERE uid=' + str(user_id))
            if sql_result:
                db_transaction(db, 'UPDATE citys SET city = "' + answer + '" WHERE uid = "' + str(user_id) + '"')
            else:
                # no user in DB
                db_transaction(db, 'INSERT INTO citys (city) VALUES ("' + answer + '")')
            text = "Отлично, " + user_name + ", теперь Вы можете приступить к использованию бота! Желаете добавить маршрут, которым часто пользуетесь?"
            state[user_id] = MAIN_MENU
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=main_kbd)
        else:
            del state[user_id]
            text = str('Бля, ' + user_name + ', ну что Вам сказать - хуёво, нет у нас вашего города')
            bot.sendMessage(update.message.from_user.id, text=text)


def main():
    # Create the EventHandler and pass it your bot's token.
    updater = Updater(telegram_token)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", helper))

    # on noncommand i.e message - echo the message on Telegram
    dp.add_handler(MessageHandler([Filters.text], chat))

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    updater.start_polling()

    updater.idle()


if __name__ == '__main__':
    main()
