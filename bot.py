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

cities = ("Москва и МО", "Санкт-Петербург и ЛО", "Новосибирская область",)
default_route_names = ("На работу", "Домой", "К маме",)
train = namedtuple('train', ['uid', 'arrival', 'departure', 'duration', 'stops', 'express'])

button_next = telegram.InlineKeyboardButton(text='Следующие 5', callback_data='next_trains')
button_prev = telegram.InlineKeyboardButton(text='Предыдущие 5', callback_data='prev_trains')
next_prev_kbd = telegram.InlineKeyboardMarkup([[button_prev, button_next]])

# number of trains to show
n_trains = 5
# Define all possible states of a chat
SELECT_CITY, MAIN_MENU, START, MEETING, CREATE_ROUTE, ROUTE_NAME, FROM_STATION, TO_STATION, ROUTE_READY, STILL_NO_ROUTES, CHOOSE_ROUTE, CHOOSE_TIMETABLE, NEXT_TRAIN, ROUTE_CHECK, NAME_CHANGE, FROM_CHANGE, TO_CHANGE, NAME_CHECK, RASP, RASP_DATE = range(20)


class Route:
    def __init__(self, user_id=None, name=None, from_st=None, to_st=None, from_name=None, to_name=None):
        if user_id:
            self.name = name
            self.from_st, self.to_st, self.from_name, self.to_name = db_transaction(sqlite3.connect(db_name), "SELECT `from_`, `to_`, `from_name`, `to_name` FROM `routes` WHERE (`uid`='" + str(user_id) + "' AND `name`='" + name + "')")[0]
        else:
            self.name = "%tmp%"
            self.from_st, self.to_st, self.from_name, self.to_name = from_st, to_st, from_name, to_name


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
    stop_words = ('республика', 'область', 'край', 'округ', 'ская')
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


def get_trains(from_esr, to_esr, date=None):
    if date is None:
        date = datetime.datetime.now()
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


def next_train(from_esr, to_esr, user_id):
    now = datetime.datetime.now()+datetime.timedelta(hours=timezones[dd[user_id]['user_city']]-3)
    for i in range(7):
        sorted_trains = sorted(get_trains(from_esr, to_esr, datetime.datetime.now()+datetime.timedelta(days=i)), key=lambda t: t.departure-now)
        print(i, sorted_trains)
        if sorted_trains:
            print('\t\t>>>break')
            break
    rest_trains = [t for t in sorted_trains if (now-t.departure) < datetime.timedelta(0, 0, 0)]
    # TODO: find the next train even if there is no trains today
    if rest_trains:
        return rest_trains[0]
    else:
        return []


def print_train(train, user_id):
    departure = train.departure.strftime('%H:%M')
    arrival = train.arrival.strftime('%H:%M')
    now = datetime.datetime.now()+datetime.timedelta(hours=timezones[dd[user_id]['user_city']]-3)
    dt = train.departure-now
    if dt > datetime.timedelta(hours=24):
        dt = "еще нескоро"
    elif dt < datetime.timedelta(seconds=0):
        dt = "ушла"
    elif dt < datetime.timedelta(minutes=1):
        dt = "сейчас"
    elif dt < datetime.timedelta(hours=1):
        dt = "через " + str(dt.seconds//60) + " мин"
    else:
        dt = "через " + str(dt.seconds//3600) + " ч " + str((dt.seconds//60) % 60) + " мин"
    # TODO: add information about stops
    return "Отправляется в " + departure + " (" + dt + "), прибывает в " + arrival + " (экспресс)" if train.express else "Отправляется в " + departure + " (" + dt + "), прибывает в " + arrival


def print_next_train(user_id, route):
        # TODO: make sure that next_train() is not an empty list
        if route.name.startswith("%tmp"):
            text = "Ближайшая электричка " + route.from_name + " - " + route.to_name + ":\n" + print_train(next_train(route.from_st, route.to_st, user_id), user_id)
        else:
            text = "Ближайшая электричка по маршруту '" + route.name + "' (" + route.from_name + " - " + route.to_name + ").\n" + print_train(next_train(route.from_st, route.to_st, user_id), user_id)
        return text


def print_rasp(bot, user_id, trains=None, route=None, date=None, n=None):
    if trains is None:
        if route.name.startswith("%tmp"):
            text = "Расписание электричек \"" + route.from_name + "\" - \"" + route.to_name + "\":\n"
        else:
            text = "Расписание электричек по маршруту '" + route.name + "' (" + route.from_name + " - " + route.to_name + ").\n"
        trains = get_trains(route.from_st, route.to_st, date=date)
        dd[user_id]['scrollable_trains'] = list(trains)
        dd[user_id]['scroll_trains_offset'] = 0
        if 'scrollable_message_id' in dd[user_id]:
            bot.edit_message_reply_markup(chat_id=user_id, message_id=dd[user_id]['scrollable_message_id'])
    else:
        text = ''
        n = len(trains)
    if n is None:
        n = min(len(trains), 9)
    else:
        n = min(len(trains), n)
    trains = trains[:n-1]
    for train in trains:
        text += "\n" + print_train(train, user_id) + "\n"
    return text


def scroll(bot, update):
    w_text = ''
    action = update.callback_query.data
    user_id = update.callback_query.message.chat_id
    trains = dd[user_id]['scrollable_trains']
    if action == 'next_trains':
        if dd[user_id]['scroll_trains_offset'] >= len(trains)-n_trains:
            w_text = "Достигнут концец списка"
        else:
            dd[user_id]['scroll_trains_offset'] += n_trains
            offset = dd[user_id]['scroll_trains_offset']
            text = print_rasp(bot, user_id, trains=trains[offset:min(offset+n_trains, len(trains))])
    elif action == 'prev_trains':
        if dd[user_id]['scroll_trains_offset'] <= 0:
            w_text = "Достигнуто начало списка"
        else:
            dd[user_id]['scroll_trains_offset'] -= n_trains
            offset = dd[user_id]['scroll_trains_offset']
            text = print_rasp(bot, user_id, trains=trains[offset:min(offset+n_trains, len(trains))])
    if w_text:
        bot.answerCallbackQuery(text=w_text, callback_query_id=update.callback_query.id)
    else:
        bot.answerCallbackQuery(callback_query_id=update.callback_query.id)
        bot.editMessageText(text=text, chat_id=update.callback_query.message.chat.id, message_id=update.callback_query.message.message_id, reply_markup=next_prev_kbd)


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
        dd[user_id]['user_city'] = user_city
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
    if user_id not in dd:
        dd[user_id] = dict()
        route_name = ''
    elif 'route_name' in dd[user_id]:
        route_name = dd[user_id]['route_name']
    else:
        route_name = ''
    if chat_state != MEETING:
        # TODO: cache some sql results?
        sql_result = db_transaction(db, 'SELECT user_name, city FROM cities WHERE uid=' + str(user_id))
        user_name, user_city = sql_result[0]
        if 'user_city' not in dd[user_id]:
            dd[user_id]['user_city'] = user_city

    # Keyboards
    city_kbd = telegram.ReplyKeyboardMarkup([[city] for city in cities] + [['Главное меню']])
    main_kbd = telegram.ReplyKeyboardMarkup([['Расписание', 'Ближайшая электричка'], ['Маршруты', 'INFO']])
    yes_no_kbd = telegram.ReplyKeyboardMarkup([['Да'], ['Нет']])
    route_name_kbd = telegram.ReplyKeyboardMarkup([[route_name] for route_name in default_route_names] + [['Главное меню']],  one_time_keyboard=True, resize_keyboard=True)
    no_routes_kbd = telegram.ReplyKeyboardMarkup([['Новый маршрут'], ['Главное меню']])
    date_kbd = telegram.ReplyKeyboardMarkup([['Вчера'], ['Сегодня'], ['Завтра'], ['Главное меню']])
    empty_kbd = telegram.ReplyKeyboardHide()

    if answer == 'Главное меню':
        state[user_id] = MAIN_MENU
        text = user_name + ", предлагаю продолжить пользоваться раписанием"
        bot.sendMessage(user_id, text=text, reply_markup=main_kbd)
        for key in ('current_route', 'route_name', 'from_', 'from_name', 'to_', 'to_name'):
            if key in dd[user_id]:
                del dd[user_id][key]
    elif answer == 'Маршруты':
        user_routes = db_transaction(db, 'SELECT name FROM routes WHERE uid=' + str(user_id))
        if not user_routes:
            text = user_name + ', Вы пока не создавали маршрутов :( Чтобы создать маршрут, нажмите кнопку "Новый маршрут"'
            state[user_id] = STILL_NO_ROUTES
            bot.sendMessage(user_id, text=text, reply_markup=no_routes_kbd)
        else:
            # TODO: create new route from inline keyboard?
            user_routes_kbd = telegram.ReplyKeyboardMarkup(user_routes + [("Создать новый маршрут", "Главное меню")])
            text = user_name + ", выберите созданный маршрут или создайте новый"
            state[user_id] = CHOOSE_ROUTE
            bot.sendMessage(user_id, text=text, reply_markup=user_routes_kbd)
    elif answer == 'Ближайшая электричка':
        if dd[user_id].get('current_route'):
            text = print_next_train(user_id, route=dd[user_id]['current_route'])
            state[user_id] = MAIN_MENU
            bot.sendMessage(user_id, text=text, reply_markup=main_kbd)
            del dd[user_id]['current_route']
        else:
            user_routes = db_transaction(db, 'SELECT name FROM routes WHERE uid=' + str(user_id))
            if not user_routes:
                text = user_name + ', Вы пока не создавали маршрутов :( Чтобы создать маршрут, нажмите кнопку "Новый маршрут"'
                state[user_id] = STILL_NO_ROUTES
                bot.sendMessage(user_id, text=text, reply_markup=no_routes_kbd)
            else:
                user_routes_kbd = telegram.ReplyKeyboardMarkup(user_routes + [("Другой маршрут", "Главное меню")])
                text = user_name + ", выберите маршрут"
                state[user_id] = NEXT_TRAIN
                bot.sendMessage(user_id, text=text, reply_markup=user_routes_kbd)
    elif answer == 'Расписание':
        if dd[user_id].get('current_route'):
            text = "Выберите дату, на которую интересует расписание. Можете ввести другую в формате день.месяц.год (например 4.07.2015 для того, чтобы выбрать 4 июля 2015 года). "
            state[user_id] = RASP_DATE
            bot.sendMessage(user_id, text=text, reply_markup=date_kbd)
        else:
            user_routes = db_transaction(db, 'SELECT name FROM routes WHERE uid=' + str(user_id))
            if not user_routes:
                text = user_name + ', Вы пока не создавали маршрутов :( Чтобы создать маршрут, нажмите кнопку "Новый маршрут"'
                state[user_id] = STILL_NO_ROUTES
                bot.sendMessage(user_id, text=text, reply_markup=no_routes_kbd)
            else:
                user_routes_kbd = telegram.ReplyKeyboardMarkup(user_routes + [("Другой маршрут", "Главное меню")])
                text = user_name + ", выберите маршрут"
                state[user_id] = RASP
                bot.sendMessage(user_id, text=text, reply_markup=user_routes_kbd)
    elif chat_state == NEXT_TRAIN:
        if answer == "Другой маршрут":
            dd[user_id]['route_name'] = "%tmp.next_train%"
            text = user_name + ", введите название станцию отправления"
            state[user_id] = FROM_STATION
            bot.sendMessage(update.message.chat_id, text=text, reply_markup=empty_kbd)
        else:
            text = print_next_train(user_id, Route(user_id, name=answer))
            state[user_id] = MAIN_MENU
            bot.sendMessage(user_id, text=text, reply_markup=main_kbd)
    elif chat_state == RASP:
        if answer == "Другой маршрут":
            dd[user_id]['route_name'] = "%tmp.rasp%"
            text = user_name + ", введите название станцию отправления"
            state[user_id] = FROM_STATION
            bot.sendMessage(update.message.chat_id, text=text, reply_markup=empty_kbd)
        else:
            text = "Выберите дату, на которую интересует расписание. Можете ввести другую в формате день.месяц.год (например 4.07.2015 для того, чтобы выбрать 4 июля 2015 года). "
            state[user_id] = RASP_DATE
            bot.sendMessage(user_id, text=text, reply_markup=date_kbd)
            dd[user_id]['current_route'] = Route(user_id, name=answer)
    elif chat_state == STILL_NO_ROUTES:
        if answer == 'Новый маршрут':
            text = user_name + ', давайте вместе создадим новый маршрут. Предлагаю Вам выбрать для него название из списка - или можете ввести свое'
            state[user_id] = ROUTE_NAME
            bot.sendMessage(user_id, text=text, reply_markup=route_name_kbd)
        else:
            state[user_id] = MAIN_MENU
            text = user_name + ", предлагаю продолжить пользоваться раписанием"
            bot.sendMessage(user_id, text=text, reply_markup=main_kbd)
    elif chat_state == CHOOSE_ROUTE:
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
            your_routes_kbd = telegram.ReplyKeyboardMarkup([['Ближайшая электричка'], ['Расписание'], ['Главное меню']])
            bot.sendMessage(user_id, text=text, reply_markup=your_routes_kbd)
            dd[user_id]['current_route'] = Route(user_id, name=answer)
    elif chat_state == MEETING:
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
            bot.sendMessage(user_id, text=text, reply_markup=city_kbd)
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
        text = "Отлично, " + user_name + ", теперь введите название станцию отправления"
        state[user_id] = FROM_STATION
        bot.sendMessage(update.message.chat_id, text=text, reply_markup=empty_kbd)
    elif chat_state == FROM_STATION:
        levenstein = []
        sql_user_station = db_transaction(sqlite3.connect(db_name), 'SELECT station FROM codes WHERE city = "' + user_city + '" ')
        for i in range(len(sql_user_station)):
            levenstein.append(distance(sql_user_station[i][0], answer))
        sql_real_station = db_transaction(db, 'SELECT real_station FROM codes WHERE station ="' + sql_user_station[levenstein.index(min(levenstein))][0] + '" AND city = "' + user_city + '" ')
        sql_esr = db_transaction(db, 'SELECT esr FROM codes WHERE station ="' + sql_user_station[levenstein.index(min(levenstein))][0] + '" AND city = "' + user_city + '" ')
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
        user_station = sql_user_station[levenstein.index(min(levenstein))][0]
        sql_real_station = db_transaction(db, 'SELECT real_station FROM codes WHERE station ="' + user_station + '" AND city = "' + user_city + '" ')
        sql_esr = db_transaction(db, 'SELECT esr FROM codes WHERE station ="' + user_station + '" AND city = "' + user_city + '" ')
        dd[user_id]['to_'] = sql_esr[0][0]
        dd[user_id]['to_name'] = sql_real_station[0][0]
        if dd[user_id]['from_name'] != dd[user_id]['to_name']:
            if dd[user_id]['route_name'] == "%tmp.next_train%":
                text = "Ближайшая электричка:\n" + print_train(next_train(dd[user_id]['from_'], dd[user_id]['to_'], user_id), user_id)
                state[user_id] = MAIN_MENU
                bot.sendMessage(user_id, text=text, reply_markup=main_kbd)
            elif dd[user_id]['route_name'] == "%tmp.rasp%":
                text = "Выберите дату, на которую интересует расписание. Можете ввести другую в формате день.месяц.год (например 4.07.2015 для того, чтобы выбрать 4 июля 2015 года). "
                state[user_id] = RASP_DATE
                bot.sendMessage(user_id, text=text, reply_markup=date_kbd)
                dd[user_id]['current_route'] = Route(from_st=dd[user_id]['from_'], from_name=dd[user_id]['from_name'], to_st=dd[user_id]['to_'], to_name=dd[user_id]['to_name'])
                # TODO: save the route as ...
            else:
                # ordinary route
                text = user_name + ', пожалуйста, проверьте правильность составленного маршрута. Название маршрута: "' + dd[user_id]['route_name'] + '". Станция отправления: "' + dd[user_id]['from_name'] + '". Станция прибытия: "' + dd[user_id]['to_name'] + '"'
                route_ready_kbd = telegram.ReplyKeyboardMarkup([['Все верно', 'Неверное название маршрута'], ['Неверное название станции отправления', 'Неверное название станции прибытия'], ['Главное меню']])
                state[user_id] = ROUTE_READY
                bot.sendMessage(user_id, text=text, reply_markup=route_ready_kbd)
        else:
            # INCORRECT!!! text
            text = user_name + ', к сожалению, по созданному вами маршруту: "' + dd[user_id]['route_name'] + '" между станцией "' + dd[user_id]['from_name'] + '" и станцией "' + dd[user_id]['to_name'] + '" нет прямой электрички. Возможно, вы неправильно ввели название станции?'
            route_ready_kbd = telegram.ReplyKeyboardMarkup([['Неверное название станции отправления'], ['Неверное название станции прибытия'], ['Главное меню']])
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
        route_ready_kbd = telegram.ReplyKeyboardMarkup([['Все верно', 'Неверное название маршрута'], ['Неверное название станции отправления', 'Неверное название станции прибытия'], ['Главное меню']])
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
        route_ready_kbd = telegram.ReplyKeyboardMarkup([['Все верно', 'Неверное название маршрута'], ['Неверное название станции отправления', 'Неверное название станции прибытия'], ['Главное меню']])
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
        route_ready_kbd = telegram.ReplyKeyboardMarkup([['Все верно', 'Неверное название маршрута'], ['Неверное название станции отправления', 'Неверное название станции прибытия'], ['Главное меню']])
        state[user_id] = ROUTE_READY
        bot.sendMessage(user_id, text=text, reply_markup=route_ready_kbd)
    elif chat_state == RASP_DATE:
        if answer == "Сегодня":
            date = datetime.datetime.now()
        elif answer == "Завтра":
            date = datetime.datetime.now() + datetime.timedelta(days=1)
        elif answer == "Вчера":
            date = datetime.datetime.now() - datetime.timedelta(days=1)
        else:
            try:
                day, month, year = (int(part) for part in answer.split(".", maxsplit=2))
                date = datetime.datetime(year, month, day)
            except ValueError:
                text = "Это какая-то непонятная дата. Пробуй-ка еще раз ввести дату в формате \"день.месяц.год\"!"
                bot.sendMessage(user_id, text=text, reply_markup=date_kbd)
                return
        text = print_rasp(bot, user_id, route=dd[user_id]['current_route'], date=date, n=n_trains)
        state[user_id] = MAIN_MENU
        dd[user_id]['scrollable_message_id'] = bot.sendMessage(user_id, text=text, reply_markup=next_prev_kbd).message_id
        del dd[user_id]['current_route']
    else:
        text = "Ваши намерения не ясны! Попробуйте еще, в этот раз у Вас все получится!"
        state[user_id] = MAIN_MENU
        bot.sendMessage(user_id, text=text, reply_markup=main_kbd)


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

    # inline keyboard handler
    dp.add_handler(telegram.ext.CallbackQueryHandler(scroll))
    # TODO: update messages with time information

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    updater.start_polling()

    updater.idle()


if __name__ == '__main__':
    main()
