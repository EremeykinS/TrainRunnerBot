from config import *
from collections import namedtuple
from urllib import request
from urllib.parse import urlencode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import telegram
import logging
import sqlite3
import json
import datetime

# TODO: write logs to file
# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger('TrainRunnerBot.' + __name__)

# States are saved in a dict that maps chat_id -> state
state = dict()
# dict in dict
dd = dict()

db_name = 'DB.sqlite'
cities = ("Москва и МО", "Санкт-Петербург и ЛО", "Новосибирская область",)
default_route_names = ("На работу", "Домой", "К маме",)
train = namedtuple('train', ['uid', 'arrival', 'departure', 'duration', 'stops', 'express'])



# Define all possible states of a chat
SELECT_CITY, MAIN_MENU, START, MEETING, CREATE_ROUTE, ROUTE_NAME, FROM_STATION, TO_STATION, ROUTE_READY, STILL_NO_ROUTES, CHOOSE_ROUTE, CHOOSE_TIMETABLE, NEXT_TRAIN, ROUTE_CHECK, NAME_CHANGE, FROM_CHANGE, TO_CHANGE, NAME_CHECK = range(18)


def db_transaction(db, q):
    # TODO: prevent SQL injection
    # http://stackoverflow.com/questions/7929364/python-best-practice-and-securest-to-connect-to-mysql-and-execute-queries
    cursor = db.cursor()
    cursor.execute(q)
    db.commit()
    result = cursor.fetchall()
    return result

sql_station = db_transaction(sqlite3.connect(db_name), 'SELECT station FROM codes ')

def canonize(source):
    stop_symbols = '.,!?:;-\n\r()'
    stop_words = (u'республика', u'область', u'край', u'округ',u'ская')

    return ( [x for x in [y.strip(stop_symbols) for y in source.lower().split()] if x and (x not in stop_words)] )

def distance(a, b):
    """Рассчет расстояния Левенштейна между a и b"""
    n, m = len(a), len(b)
    if n > m:
        # Make sure n <= m, to use O(min(n,m)) space
        a, b = b, a
        n, m = m, n

    current_row = range(n + 1)  # Keep current and previous row, not entire matrix
    for i in range(1, m + 1):
        previous_row, current_row = current_row, [i] + [0] * n
        for j in range(1, n + 1):
            add, delete, change = previous_row[j] + 1, current_row[j - 1] + 1, previous_row[j - 1]
            if a[j - 1] != b[i - 1]:
                change += 1
            current_row[j] = min(add, delete, change)

    return current_row[n]


def get_trains(from_esr, to_esr, date):
    params = {'apikey': ya_apikey, 'format': 'json', 'lang': 'ru', 'system': 'esr', 'transport_types': 'suburban'}
    params['from'] = from_esr
    params['to'] = to_esr
    params['date'] = date.date().strftime('%Y-%m-%d')
    url = ya_apiurl + urlencode(params)
    r = json.loads(request.urlopen(url).read().decode("utf-8"))
    d_format = '%Y-%m-%d %H:%M:%S'
    trains = [train(uid=t['thread']['uid'],
                    arrival=datetime.datetime.strptime(t['arrival'], d_format),
                    departure=datetime.datetime.strptime(t['departure'], d_format),
                    duration=t['duration'],
                    stops=t['stops'],
                    express=bool(t['thread']['express_type']))
              for t in r['threads']]
    return trains


def next_train(from_esr, to_esr):
    now = datetime.datetime.now()
    sorted_trains = sorted(get_trains(from_esr, to_esr, now), key=lambda t: t.departure-now)
    rest_trains = [t for t in sorted_trains if (now-t.departure) < datetime.timedelta(0, 0, 0)]
    # TODO: find the next train even if there is no trains today
    if rest_trains:
        return rest_trains[0]
    else:
        return []


def print_next_train(user_id, route_name):
        from_st, to_st = db_transaction(sqlite3.connect(db_name), "SELECT `from_`, `to_` FROM `routes` WHERE (`uid`='" + str(user_id) + "' AND `name`='" + route_name + "')")[0]
        nt = next_train(from_st, to_st)
        dt = nt.departure-datetime.datetime.now()
        text = "Ближайшая электричка по маршруту '" + route_name + "' отправляется через " + str(dt.seconds//3600) + " ч " + str((dt.seconds//60)%60) + " м:\nотправление в " + nt.departure.strftime('%H:%M') + "\nприбытие в " + nt.arrival.strftime('%H:%M')
        return text


def start(bot, update):
    user_id = update.message.from_user.id
    db = sqlite3.connect(db_name)
    sql_result = db_transaction(db, 'SELECT user_name, city FROM cities WHERE uid=' + str(user_id))
    if user_id not in dd:
        dd[user_id] = dict()
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
    sql_result = db_transaction(db, 'SELECT user_name, city FROM cities WHERE uid=' + str(user_id))
    user_name, user_city = sql_result[0]
    if user_id not in dd:
        dd[user_id] = dict()
        route_name = ''
    elif 'route_name' in dd[user_id]:
        route_name = dd[user_id]['route_name']
    else:
        route_name = ''
    # TODO: cache some sql results? (but not this one)
    # sql_result = db_transaction(db, "SELECT name FROM routes WHERE uid=" + str(user_id))
    # if sql_result:
    #     route_name = sql_result[-1][0]  # the last record in the DB for this user
    # else:
    #     route_name = ''
    if chat_state in (TO_STATION, FROM_STATION):
        user_city = db_transaction(db, 'SELECT city FROM cities WHERE uid=' + str(user_id))[0][0]
    sql_result = db_transaction(db, 'SELECT user_name FROM cities WHERE uid=' + str(user_id))
    if chat_state != MEETING:
        user_name = sql_result[0][0]

    # Keyboards
    city_kbd = telegram.ReplyKeyboardMarkup([[city] for city in cities])
    main_kbd = telegram.ReplyKeyboardMarkup([['Раписание', 'Ближайшая электричка'], ['Маршруты', 'INFO']])
    yes_no_kbd = telegram.ReplyKeyboardMarkup([['Да'], ['Нет']])
    route_name_kbd = telegram.ReplyKeyboardMarkup([[route_name] for route_name in default_route_names])
    no_routes_kbd = telegram.ReplyKeyboardMarkup([['Новый маршрут'], ['Главное меню']])
    empty_kbd = telegram.ReplyKeyboardHide()
    
    if answer == 'Главное меню':
        state[user_id] = MAIN_MENU
        text = user_name + ", предлагаю продолжить пользоваться раписанием"
        bot.sendMessage(user_id, text=text, reply_markup=main_kbd)
    
    if answer == 'Маршруты':
        user_routes = db_transaction(db, 'SELECT name FROM routes WHERE uid=' + str(user_id))
        if not user_routes:
            text = user_name + ', Вы пока не создавали маршрутов :( Чтобы создать маршрут, нажмите кнопку "Новый маршрут"'
            state[user_id] = STILL_NO_ROUTES
            bot.sendMessage(user_id, text=text, reply_markup=no_routes_kbd)
        else:
            # TODO: create new route from inline keyboard?
            user_routes_kbd = telegram.ReplyKeyboardMarkup(user_routes + [("Создать новый маршрут",)])
            text = user_name + ", выберите созданный маршрут или создайте новый"
            state[user_id] = CHOOSE_ROUTE
            bot.sendMessage(user_id, text=text, reply_markup=user_routes_kbd)

    if answer == 'Ближайшая электричка':
        if dd[user_id].get('current_route'):
            text = print_next_train(user_id, dd[user_id]['current_route'])
            state[user_id] = MAIN_MENU
            bot.sendMessage(user_id, text=text, reply_markup=main_kbd)
            del dd[user_id]['current_route']
            return
        else:
            user_routes = db_transaction(db, 'SELECT name FROM routes WHERE uid=' + str(user_id))
            if not user_routes:
                text = user_name + ', Вы пока не создавали маршрутов :( Чтобы создать маршрут, нажмите кнопку "Новый маршрут"'
                state[user_id] = STILL_NO_ROUTES
                bot.sendMessage(user_id, text=text, reply_markup=no_routes_kbd)
            else:
                user_routes_kbd = telegram.ReplyKeyboardMarkup(user_routes)
                text = user_name + ", выберите маршрут"
                state[user_id] = NEXT_TRAIN
                bot.sendMessage(user_id, text=text, reply_markup=user_routes_kbd)

    if chat_state == NEXT_TRAIN:
        text = print_next_train(user_id, answer)
        state[user_id] = MAIN_MENU
        bot.sendMessage(user_id, text=text, reply_markup=main_kbd)

    if chat_state == STILL_NO_ROUTES:
        if answer == 'Новый маршрут':
            text = user_name + ', давайте вместе создадим новый маршрут. Предлагаю Вам выбрать для него название из списка - или можете ввести свое'
            state[user_id] = ROUTE_NAME
            route_name_kbd = telegram.ReplyKeyboardMarkup([[route_name] for route_name in default_route_names])
            bot.sendMessage(user_id, text=text, reply_markup=route_name_kbd)
        else:
            state[user_id] = MAIN_MENU
            text = user_name + ", предлагаю продолжить пользоваться раписанием"
            bot.sendMessage(user_id, text=text, reply_markup=main_kbd)

    if chat_state == CHOOSE_ROUTE:
        if answer == "Создать новый маршрут":
            text = user_name + ', предлагаю Вам выбрать название из списка - или можете ввести свое'
            state[user_id] = ROUTE_NAME
            bot.sendMessage(user_id, text=text, reply_markup=route_name_kbd)
        else:
            user_routes = db_transaction(db, 'SELECT * FROM routes WHERE uid=' + str(user_id))
            keys = [user_routes[i][3] for i in range(len(user_routes))]
            values = [[user_routes[i][1], user_routes[i][2], user_routes[i][4], user_routes[i][5]] for i in range(len(user_routes))]
            routes = dict(zip(keys, values))
            text = 'Станция отправления: ' + routes[answer][2] + '; Станция прибытия: ' + routes[answer][3] + '. Вы можете посмотреть ближайщую электричку или расписание'
            your_routes_kbd = telegram.ReplyKeyboardMarkup([['Ближайшая электричка'], ['Расписание']])
            bot.sendMessage(user_id, text=text, reply_markup=your_routes_kbd)
            dd[user_id]['current_route'] = answer

    if chat_state == MEETING:
        db_transaction(db, 'INSERT INTO cities (uid, user_name) VALUES ("' + str(user_id) + '", "' + answer + '")')
        text = "Отлично, " + answer + ", вот и познакомились! Теперь расскажите мне, в каком городе Вы живете? Выберите из списка или введите область проживания самостоятельно"
        state[user_id] = SELECT_CITY
        bot.sendMessage(update.message.chat_id, text=text, reply_markup=city_kbd)

    elif chat_state == START:
        # TODO: replace this string constants with something like sure_kbd[0][0]
        if answer == 'Да, изменить':
            text = user_name + ", пожалуйста, выберите свой город из списка, или же можете ввести область проживания самостоятельно"
            state[user_id] = SELECT_CITY
            bot.sendMessage(update.message.chat_id, text=text, reply_markup=city_kbd)
        else:
            state[user_id] = MAIN_MENU
            text = "Хорошо, " + user_name + ". Я понял, город менять не будем. Предлагаю продолжить пользоваться раписанием"
            bot.sendMessage(update.message.chat_id, text=text, reply_markup=main_kbd)

    elif chat_state == SELECT_CITY:
        sql_result = db_transaction(db, 'SELECT city FROM cities WHERE uid=' + str(user_id))
        sql_city = db_transaction(db, 'SELECT DISTINCT city FROM codes')
        levenstein = []
        for i in range(len(sql_city)):
            levenstein.append(distance(canonize(sql_city[i][0])[0], answer))
        dd[user_id]['user_city'] = sql_city[levenstein.index(min(levenstein))][0]
        # no user in DB
        # is it possible???
        text = user_name + ", правильно ли я понимаю, что место вашего проживания: " + dd[user_id]['user_city']
        state[user_id] = NAME_CHECK
        bot.sendMessage(user_id, text=text, reply_markup=yes_no_kbd)
    elif chat_state == NAME_CHECK:
        if answer == 'Да':
            db_transaction(db, 'UPDATE cities SET city = "' + dd[user_id]['user_city'] + '" WHERE uid = "' + str(user_id) + '"')
            text = "Отлично, " + user_name + ", теперь Вы можете приступить к использованию бота! Желаете добавить маршрут, которым часто пользуетесь?"
            state[user_id] = CREATE_ROUTE
            bot.sendMessage(user_id, text=text, reply_markup=yes_no_kbd)
        elif answer == 'Нет':
            state[user_id] = SELECT_CITY
            del dd[user_id]['user_city']
            text = user_name + ", давайте попробуем ввести город/область еще раз (если вы живете не в областном центре, то вводите название области)"
            name_check_kbd = telegram.ReplyKeyboardMarkup([[city] for city in cities])
            bot.sendMessage(user_id, text=text, reply_markup=name_check_kbd)
        elif answer == 'Главное меню':
            state[user_id] = MAIN_MENU
            text = user_name + ", предлагаю продолжить пользоваться раписанием"
            bot.sendMessage(user_id, text=text, reply_markup=main_kbd)
    elif chat_state == CREATE_ROUTE:
        # TODO: route name must be unique
        if answer == 'Да':
            text = user_name + ', предлагаю Вам выбрать название из списка - или можете ввести свое'
            state[user_id] = ROUTE_NAME
            bot.sendMessage(user_id, text=text, reply_markup=route_name_kbd)
        else:
            state[user_id] = MAIN_MENU
            text = str('Хорошо, ' + user_name + ', Вы всегда можете создать маршрут перейдя во вкладку "Маршруты". Желаю приятного использования TrainRunner. За дополнительной информацией нажмите /help')
            bot.sendMessage(user_id, text=text, reply_markup=main_kbd)
    elif chat_state == ROUTE_NAME:
        dd[user_id]['route_name'] = answer
        text = "Отлично, " + user_name + ", теперь введите название станции отправления"
        state[user_id] = FROM_STATION
        bot.sendMessage(update.message.chat_id, text=text, reply_markup=empty_kbd)
    elif chat_state == FROM_STATION:
        levenstein = []
        sql_user_station = db_transaction(sqlite3.connect(db_name), 'SELECT station FROM codes WHERE city = "' + user_city + '" ')
        for i in range(len(sql_user_station)):
            levenstein.append(distance(sql_user_station[i][0], answer))
        sql_real_station = db_transaction(db, 'SELECT real_station FROM codes WHERE station ="' + sql_user_station[levenstein.index(min(levenstein))][0] + '" AND city = "' + user_city + '" ')
        sql_esr = db_transaction(db, 'SELECT esr FROM codes WHERE station ="' + sql_user_station[levenstein.index(min(levenstein))][0] + '" AND city = "' + user_city + '" ')
        # db_transaction(db, 'UPDATE routes SET from_ = "' + sql_esr[0][0] + '" WHERE uid= "' + str(user_id) + '" AND name = ("' + route_name + '")')
        # db_transaction(db, 'UPDATE routes SET from_name = "' + sql_real_station[0][0] + '" WHERE uid= "' + str(user_id) + '" AND name = ("' + route_name + '")')
        dd[user_id]['from_'] = sql_esr[0][0]
        dd[user_id]['from_name'] = sql_real_station[0][0]
        text = user_name + ', ну и станцию прибытия, пожалуйста'
        state[user_id] = TO_STATION
        bot.sendMessage(user_id, text=text, reply_markup=empty_kbd)
    elif chat_state == TO_STATION:
        levenstein = []
        sql_user_station = db_transaction(db, 'SELECT station FROM codes WHERE city = "' + user_city + '" ')
        for i in range(len(sql_user_station)):
            levenstein.append(distance(sql_user_station[i][0], answer))
        sql_real_station = db_transaction(db, 'SELECT real_station FROM codes WHERE station ="' + sql_user_station[levenstein.index(min(levenstein))][0] + '" AND city = "' + user_city + '" ')
        sql_esr = db_transaction(db, 'SELECT esr FROM codes WHERE station ="' + sql_user_station[levenstein.index(min(levenstein))][0] + '" AND city = "' + user_city + '" ')
        # db_transaction(db, 'UPDATE routes SET to_ = "' + sql_esr[0][0] + '" WHERE uid= "' + str(user_id) + '" AND name = ("' + route_name + '")')
        # db_transaction(db, 'UPDATE routes SET to_name = "' + sql_real_station[0][0] + '" WHERE uid= "' + str(user_id) + '" AND name = ("' + route_name + '")')
        dd[user_id]['to_'] = sql_esr[0][0]
        dd[user_id]['to_name'] = sql_real_station[0][0]
        sql_from_division = db_transaction(db, 'SELECT division FROM codes WHERE real_station ="' + dd[user_id]['from_name'] +'" ')
        print(sql_from_division)
        sql_to_division = db_transaction(db, 'SELECT division FROM codes WHERE real_station ="' + dd[user_id]['to_name'] +'" ')
        print(sql_to_division)
        if sql_from_division == sql_to_division:
            text = user_name + ', пожалуйста, проверьте правильность составленного маршрута. Название маршрута: "' + dd[user_id]['route_name'] + '". Станция отправления: "' + dd[user_id]['from_name'] + '". Станция прибытия: "' + dd[user_id]['to_name'] + '"'
            route_ready_kbd = telegram.ReplyKeyboardMarkup([['Все верно', 'Неверное название маршрута'], ['Неверное название станции отправления', 'Неверное название станции прибытия']])
            state[user_id] = ROUTE_READY
            bot.sendMessage(user_id, text=text, reply_markup=route_ready_kbd)
        else:
            text = user_name + ', к сожалению, по созданному вами маршруту: "' + dd[user_id]['route_name'] + '" между станцией "' + dd[user_id]['from_name'] + '" и станцией "' + dd[user_id]['to_name'] + '" нет прямой электрички. Возможно, вы неправильно ввели название станции?'
            route_ready_kbd = telegram.ReplyKeyboardMarkup([['Неверное название станции отправления', 'Неверное название станции прибытия']])
            state[user_id] = ROUTE_READY
            bot.sendMessage(user_id, text=text, reply_markup=route_ready_kbd)
    elif chat_state == ROUTE_READY:
        if answer == 'Все верно':
            text = user_name + ', поздравляю, маршрут "' + dd[user_id]['route_name'] + '" успешно создан! Приятного использования TrainRunner'
            state[user_id] = MAIN_MENU
            bot.sendMessage(user_id, text=text, reply_markup=main_kbd)
            q = 'INSERT INTO `routes` (`uid`, `name`, `from_`, `from_name`, `to_`, `to_name`) VALUES ("' + str(user_id) + '", "' + dd[user_id]['route_name'] + '", "' + dd[user_id]['from_'] + '", "' + dd[user_id]['from_name'] + '", "' + dd[user_id]['to_'] + '", "' + dd[user_id]['to_name'] + '")'
            db_transaction(db, q)
            del dd[user_id]  
        elif answer == "Неверное название маршрута":
            del dd[user_id]['route_name']
            text = user_name + ', попробуйте ввести название маршрута еще раз'
            state[user_id] = NAME_CHANGE
            bot.sendMessage(user_id, text=text, reply_markup=route_name_kbd)
        elif answer == "Неверное название станции отправления":
            del dd[user_id]['from_']
            del dd[user_id]['from_name']
            text = user_name + ', попробуйте ввести название станции отправления еще раз'
            state[user_id] = FROM_CHANGE
            bot.sendMessage(user_id, text=text, reply_markup=empty_kbd)
        elif answer == "Неверное название станции прибытия":
            del dd[user_id]['to_']
            del dd[user_id]['to_name']
            text = user_name + ', попробуйте ввести название станции прибытия еще раз'
            state[user_id] = TO_CHANGE
            bot.sendMessage(user_id, text=text, reply_markup=empty_kbd)
    elif chat_state == NAME_CHANGE:
        route_name = answer
        dd[user_id]['route_name'] = route_name
        text = user_name + ', после исправлений Ваш маршрут выглядит следующим образом. Теперь все правильно? Название маршрута: "' + route_name + '". Станция отправления: "' + dd[user_id]['from_name'] + '". Станция прибытия: "' + dd[user_id]['to_name'] + '"'
        route_ready_kbd = telegram.ReplyKeyboardMarkup([['Все верно', 'Неверное название маршрута'], ['Неверное название станции отправления', 'Неверное название станции прибытия']])
        state[user_id] = ROUTE_READY
        bot.sendMessage(user_id, text=text, reply_markup=route_ready_kbd)
    elif chat_state == FROM_CHANGE:
        levenstein = []
        sql_user_station = db_transaction(sqlite3.connect(db_name), 'SELECT station FROM codes WHERE city = "' + user_city + '" ')
        for i in range(len(sql_user_station)):
            levenstein.append(distance(sql_user_station[i][0], answer))
        sql_real_station = db_transaction(db, 'SELECT real_station FROM codes WHERE station ="' + sql_user_station[levenstein.index(min(levenstein))][0] + '" AND city = "' + user_city + '" ')
        sql_esr = db_transaction(db, 'SELECT esr FROM codes WHERE station ="' + sql_user_station[levenstein.index(min(levenstein))][0] + '" AND city = "' + user_city + '" ')
        dd[user_id]['from_'] = sql_esr[0][0]
        dd[user_id]['from_name'] = sql_real_station[0][0]
        text = user_name + ', после исправлений Ваш маршрут выглядит следующим образом. Теперь все правильно? Название маршрута: "' + route_name + '". Станция отправления: "' + dd[user_id]['from_name'] + '". Станция прибытия: "' + dd[user_id]['to_name'] + '"'
        route_ready_kbd = telegram.ReplyKeyboardMarkup([['Все верно', 'Неверное название маршрута'], ['Неверное название станции отправления', 'Неверное название станции прибытия']])
        state[user_id] = ROUTE_READY
        bot.sendMessage(user_id, text=text, reply_markup=route_ready_kbd)
    elif chat_state == TO_CHANGE:
        levenstein = []
        sql_user_station = db_transaction(sqlite3.connect(db_name), 'SELECT station FROM codes WHERE city = "' + user_city + '" ')
        for i in range(len(sql_user_station)):
            levenstein.append(distance(sql_user_station[i][0], answer))
        sql_real_station = db_transaction(db, 'SELECT real_station FROM codes WHERE station ="' + sql_user_station[levenstein.index(min(levenstein))][0] + '" AND city = "' + user_city + '" ')
        sql_esr = db_transaction(db, 'SELECT esr FROM codes WHERE station ="' + sql_user_station[levenstein.index(min(levenstein))][0] + '" AND city = "' + user_city + '" ')
        dd[user_id]['to_'] = sql_esr[0][0]
        dd[user_id]['to_name'] = sql_real_station[0][0]
        text = user_name + ', после исправлений Ваш маршрут выглядит следующим образом. Теперь все правильно? Название маршрута: "' + route_name + '". Станция отправления: "' + dd[user_id]['from_name'] + '". Станция прибытия: "' + dd[user_id]['to_name'] + '"'
        route_ready_kbd = telegram.ReplyKeyboardMarkup([['Все верно', 'Неверное название маршрута'], ['Неверное название станции отправления', 'Неверное название станции прибытия']])
        state[user_id] = ROUTE_READY
        bot.sendMessage(user_id, text=text, reply_markup=route_ready_kbd)


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
