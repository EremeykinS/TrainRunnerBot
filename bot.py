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

cities = ("Москва и МО", "Санкт-Петербург и ЛО", "Новосибирская область",)
default_route_names = ("На работу", "Домой", "К маме",)
db_name = 'DB.sqlite'

# Define all possible states of a chat
SELECT_CITY, MAIN_MENU, START, MEETING, CREATE_ROUTE, ROUTE_NAME, FROM_STATION, TO_STATION, ROUTE_READY, STILL_NO_ROUTES, CHOOSE_ROUTE, CHOOSE_TIMETABLE = range(12)


def db_transaction(db, q):
    # TODO: prevent SQL injection
    # http://stackoverflow.com/questions/7929364/python-best-practice-and-securest-to-connect-to-mysql-and-execute-queries
    cursor = db.cursor()
    cursor.execute(q)
    db.commit()
    result = cursor.fetchall()
    return result


def distance(a, b):
    """Рассчет расстояния Левенштейна между a и b"""
    n, m = len(a), len(b)
    if n > m:
        # Make sure n <= m, to use O(min(n,m)) space
        a, b = b, a
        n, m = m, n

    current_row = range(n+1)  # Keep current and previous row, not entire matrix
    for i in range(1, m+1):
        previous_row, current_row = current_row, [i]+[0]*n
        for j in range(1, n+1):
            add, delete, change = previous_row[j]+1, current_row[j-1]+1, previous_row[j-1]
            if a[j-1] != b[i-1]:
                change += 1
            current_row[j] = min(add, delete, change)

    return current_row[n]


def start(bot, update):
    user_id = update.message.from_user.id
    db = sqlite3.connect(db_name)
    sql_result = db_transaction(db, 'SELECT user_name, city FROM cities WHERE uid=' + str(user_id))
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
    db = sqlite3.connect(db_name)
    # TODO: cache some sql results? (but not this one)
    sql_result = db_transaction(db, "SELECT name FROM routes WHERE uid=" + str(user_id))
    if sql_result:
        route_name = sql_result[-1][0]  # the last record in the DB for this user
    else:
        route_name = ''
    if chat_state in (TO_STATION, FROM_STATION):
        user_city = db_transaction(db, 'SELECT city FROM cities WHERE uid=' + str(user_id))[0][0]
    sql_result = db_transaction(db, 'SELECT user_name FROM cities WHERE uid=' + str(user_id))
    if chat_state != MEETING:
        user_name = sql_result[0][0]

    # Keyboards
    city_kbd = telegram.ReplyKeyboardMarkup([[city] for city in cities])
    main_kbd = telegram.ReplyKeyboardMarkup([['Раписание', 'Ближайшая электричка'], ['Маршруты', 'INFO']])
    yes_no_kbd = telegram.ReplyKeyboardMarkup([['Да'], ['Нет, создам позже']])
    route_name_kbd = telegram.ReplyKeyboardMarkup([[route_name] for route_name in default_route_names])

    if answer == 'Маршруты':
        user_routes = db_transaction(db, 'SELECT * FROM routes WHERE uid=' + str(user_id))
        if not user_routes:
            text = str(user_name) + ', Вы пока не создавали маршрутов :( Чтобы создать маршрут, нажмите кнопку "Новый маршрут"'
            state[user_id] = STILL_NO_ROUTES
            no_routes_kbd = telegram.ReplyKeyboardMarkup([['Новый маршрут'], ['Главное меню']])
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=no_routes_kbd)
        else:
            user_routes_kbd = telegram.ReplyKeyboardMarkup([[route_name] for route_name in [user_routes[i][3] for i in range(len(user_routes))]])
            text = user_name + ", выберите созданный маршрут или создайте новый"
            state[user_id] = CHOOSE_ROUTE
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=user_routes_kbd)

    if chat_state == STILL_NO_ROUTES:
        if answer == 'Новый маршрут':
            text = user_name + ', давайте вместе создадим новый маршрут. Предлагаю Вам выбрать для него название из списка - или можете ввести свое'
            state[user_id]= ROUTE_NAME
            route_name_kbd = telegram.ReplyKeyboardMarkup([[route_name] for route_name in default_route_names])
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=route_name_kbd)
        else:
            state[user_id] = MAIN_MENU
            text = user_name + ", предлагаю продолжить пользоваться раписанием"
            bot.sendMessage(update.message.chat_id, text=text, reply_markup=main_kbd)

    if chat_state == CHOOSE_ROUTE:
        user_routes = db_transaction(db, 'SELECT * FROM routes WHERE uid=' + str(user_id))
        keys = [user_routes[i][3] for i in range(len(user_routes))]
        values = [[user_routes[i][1],user_routes[i][2],user_routes[i][4],user_routes[i][5]] for i in range(len(user_routes))]
        routes = dict(zip(keys, values))
        text = 'Станция отправления: '+ routes[answer][2]+ '; Станция прибытия: '+routes[answer][3]+ '. Вы можете посмотреть ближайщую электричку или расписание'
        your_routes_kbd = telegram.ReplyKeyboardMarkup([['Ближайшая электричка'], ['Расписание']])
        bot.sendMessage(update.message.from_user.id, text=text, reply_markup=your_routes_kbd)
        state[user_id] = CHOOSE_TIMETABLE

    if chat_state == MEETING:
        db_transaction(db, 'INSERT INTO cities (uid, user_name) VALUES ("' + str(user_id) + '", "' + answer + '")')
        text = "Отлично, " + answer + ", вот и познакомились! Теперь расскажите мне, в каком городе Вы живете?"
        state[user_id] = SELECT_CITY
        bot.sendMessage(update.message.chat_id, text=text, reply_markup=city_kbd)

    elif chat_state == START:
        # TODO: replace this string constants with something like sure_kbd[0][0]
        if answer == 'Да, изменить':
            text = user_name + ", пожалуйста, выберите свой город из списка"
            state[user_id] = SELECT_CITY
            bot.sendMessage(update.message.chat_id, text=text, reply_markup=city_kbd)
        else:
            state[user_id] = MAIN_MENU
            text = "Хорошо, " + user_name + ". Я понял, город менять не будем. Предлагаю продолжить пользоваться раписанием"
            bot.sendMessage(update.message.chat_id, text=text, reply_markup=main_kbd)
            
    elif chat_state == SELECT_CITY:
        if answer in cities:
            sql_result = db_transaction(db, 'SELECT city FROM cities WHERE uid=' + str(user_id))
            if sql_result:
                db_transaction(db, 'UPDATE cities SET city = "' + answer + '" WHERE uid = "' + str(user_id) + '"')
            else:
                # no user in DB
                # is it possible???
                db_transaction(db, 'INSERT INTO cities (city) VALUES ("' + answer + '")')
            text = "Отлично, " + user_name + ", теперь Вы можете приступить к использованию бота! Желаете добавить маршрут, которым часто пользуетесь?"
            state[user_id] = CREATE_ROUTE
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=yes_no_kbd)
        else:
            state[user_id] = MAIN_MENU
            text = str('Блин, ' + user_name + ', ну что Вам сказать - хреново, нет у нас вашего города. Попробуйте еще в другой раз')
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=main_kbd)
    elif chat_state == CREATE_ROUTE:
        if answer == 'Да':
            text = user_name + ', предлагаю Вам выбрать название из списка - или можете ввести свое'
            state[user_id] = ROUTE_NAME
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=route_name_kbd)
        else:
            state[user_id] = MAIN_MENU
            text = str('Хорошо, ' + user_name + ', Вы всегда можете создать маршрут перейдя во вкладку "Маршруты". Желаю приятного использования TrainRunner. За дополнительной информацией нажмите /help')
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=main_kbd)
    elif chat_state == ROUTE_NAME:
        route_name = answer
        db_transaction(db, 'INSERT INTO routes (uid, name) VALUES ("' + str(user_id) + '", "' + answer + '")')
        text = "Отлично, " + user_name + ", теперь введите название станции отправления"
        state[user_id] = FROM_STATION
        wrong_route_kbd = telegram.ReplyKeyboardMarkup([["Я неправильно ввел название маршрута!"]])
        bot.sendMessage(update.message.chat_id, text=text, reply_markup=wrong_route_kbd)
    elif chat_state == FROM_STATION:
        if answer == "Я неправильно ввел название маршрута!":
            db_transaction(db, 'DELETE FROM routes WHERE uid=' + str(user_id) + ' AND name IN ("' + route_name + '")') 
            text = user_name + ', попробуйте ввести название маршрута еще раз'
            state[user_id] = ROUTE_NAME
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=route_name_kbd)
        else:
            sql_station = db_transaction(db, 'SELECT station FROM codes ')
            levenstein = []
            for i in range(len(sql_station)):
                levenstein.append(distance((str(sql_station[i])[2:-3]), answer))
            sql_real_station = db_transaction(db, 'SELECT real_station FROM codes WHERE station ="'+str(sql_station[levenstein.index(min(levenstein))])[2:-3]+'" AND city = "' + str(user_city)[3:-4].lower() + '" ')
            sql_esr = db_transaction(db, 'SELECT esr FROM codes WHERE station ="' + str(sql_station[levenstein.index(min(levenstein))])[2:-3] + '" AND city = "' + user_city.lower() + '" ')
            db_transaction(db, 'UPDATE routes SET from_ = "' + str(sql_esr)[3:-4] + '" WHERE uid= "' + str(user_id) + '" AND name = ("' + route_name + '")')
            db_transaction(db, 'UPDATE routes SET from_name = "' + str(sql_real_station)[3:-4] + '" WHERE uid= "' + str(user_id) + '" AND name = ("' + route_name + '")')
            text = user_name + ', ну и станцию прибытия, пожалуйста'
            state[user_id] = TO_STATION
            from_kbd = telegram.ReplyKeyboardHide()
            bot.sendMessage(update.message.from_user.id, text=text, reply_markup=from_kbd)
    elif chat_state == TO_STATION:
        sql_station = db_transaction(db, 'SELECT station FROM codes ')
        levenstein = []
        for i in range(len(sql_station)):
            levenstein.append(distance((str(sql_station[i])[2:-3]), answer))
        sql_real_station = db_transaction(db, 'SELECT real_station FROM codes WHERE station ="'+str(sql_station[levenstein.index(min(levenstein))])[2:-3]+'" AND city = "' + str(user_city)[3:-4].lower() + '" ')
        sql_esr = db_transaction(db, 'SELECT esr FROM codes WHERE station ="'+str(sql_station[levenstein.index(min(levenstein))])[2:-3]+'" AND city = "' + str(user_city)[3:-4].lower() + '" ')
        db_transaction(db, 'UPDATE routes SET to_ = "' + str(sql_esr)[3:-4] + '" WHERE uid= "' + str(user_id) + '" AND name = ("' + route_name + '")')
        db_transaction(db, 'UPDATE routes SET to_name = "'+str(sql_real_station)[3:-4]+'" WHERE uid= "' + str(user_id) + '" AND name = ("' + route_name + '")')
        text = user_name + ', поздравляю, маршрут "' + route_name + '" успешно создан! Приятного использования TrainRunner'
        state[user_id] = ROUTE_READY
        bot.sendMessage(update.message.from_user.id, text=text, reply_markup=main_kbd)


def main():
    # Create the EventHandler and pass it your bot's token.
    updater = Updater(telegram_token)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", helper))

    # on noncommand i.e message
    dp.add_handler(MessageHandler([Filters.text], chat))

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    updater.start_polling()

    updater.idle()


if __name__ == '__main__':
    main()
