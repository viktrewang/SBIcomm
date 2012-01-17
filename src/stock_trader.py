#!/bin/usr/env python
# -*- coding:utf-8 -*-
try:
    import psyco
    psyco.full()
except ImportError:
    pass

import sys, time, datetime, cPickle, traceback
import yaml, workdays
from SBIcomm import *
from util import *
import StockSimulator
import yahoo_finance_jp
from yahoo_finance_jp import OPEN, CLOSE, MAX, MIN, VOLUME, N_DATA
from code import CODE
import logging
import logging.config
logging.config.fileConfig('log.conf')
logger = logging.getLogger('trade')

from pylab import *
import ConfigParser
# configファイルの読み込み
conf = ConfigParser.SafeConfigParser()
conf.read('trader.conf')
USERNAME = conf.get('infomation', 'username')
PASSWARD = conf.get('infomation', 'password')

STOCK_UNIT = 100
DOWN_RATE, AVG_VAL, TM, TMS = [-0.12, 2000000.0, 120, 6]

class Order:
    def __init__(self, day, value, num):
        self.day = day
        self.value = value
        self.num = num

class Trader:
    orders = {}
    def __init__(self, init_res, sbi, max_order=5, use_time=OPEN):
        self.resource = init_res
        self.sbi = sbi
        self.max_order = max_order

    def buy(self, code, num=None):
        if len(self.orders) >= self.max_order:
            return False
        value = self.sbi.get_value(code)
        try:
            margin = self.sbi.get_purchase_margin()
            if num is None:
                num = int(margin*0.90/(value[1][CLOSE]*STOCK_UNIT))*STOCK_UNIT
                if num <= 0:
                    return False
            if margin*0.90 > value[1][CLOSE] * num:
                order_no = self.sbi.buy_order(code, num, order='MRK_YORI')
                if order_no is None:
                    return False
            else:
                return False
        except:
            logger.info("Cannot Buy %d" % code)
            logger.info(traceback.format_exc())
            return False
        logger.info("Buy code:%s, value:%d, num:%d" % (code, value[1][CLOSE], num))
        return True

    def sell(self, code):
        if code in self.orders: 
            order_no = self.sbi.sell_order(code, self.orders[code].num, order='MRK_YORI')
            if order_no is None:
                return False
            logger.info("Sell code:%s, num:%d" % (code, self.orders[code].num))
            del(self.orders[code])
            return True
        else:
            return False

    def check_stock(self, day):
        info = self.sbi.get_hold_stock_info()
        for key, val in info.items():
            if key in self.orders.keys():
                if self.orders[key].num != val["number"]:
                    self.orders[key] = Order(day, val["value"], val["number"])
            else:
                self.orders[key] = Order(day, val["value"], val["number"])

    def get_total_resource(self):
        total_res = self.sbi.get_total_eval() + self.sbi.get_purchase_margin()
        return total_res

class TradeManeger:
    use_time = CLOSE
    simulate = True

    cnt = 0
    credit_records = {}
    detail_info = {}
    filt_rec = {}
    filt_avg = []
    max_down_rate = 1.0

    def __init__(self, username, password, offset_days=0, simulate=True):
        self.simulate = simulate
        self.offset_days = offset_days
        if self.simulate == True:
            self.sbi = StockSimulator.BrokerSimulator()
            self.filt_value = [{}, {}]
        else:
            self.sbi = SBIcomm(username, password)
            try:
                f = open("filt_value.dat", 'r')
                day, self.filt_value = yaml.load(f)
                f.close()
            except:
                logger.info(traceback.format_exc())
                self.filt_value = [{}, {}]

        self.total_res = self.get_total_resource()
        self.trader = Trader(self.total_res, self.sbi)
        logger.info("Init Res :%d" % self.total_res)

    def trade(self, down_rate, avg_val, tm, tms):
        if self.simulate == False:
            today = datetime.date.today()
            # 祝日はトレードできない
            if today in holidays_list(today.year):
                logger.info("Today is holiday! : " + str(today))
                return

        nikkei_avg = self.sbi.get_nikkei_avg()
        if len(self.filt_avg) != 0:
            self.filt_avg = lowpass_filter(60, nikkei_avg, self.filt_avg)
        else:
            self.filt_avg = nikkei_avg

        stock_value = {}
        for code in CODE.values():
            value = self.sbi.get_value(code)
            if not value[1] is None:
                stock_value[code] = value[1]
            day = value[0]
        self.trader.check_stock(day)
        logger.info("****** Start Trading %s ******" % str(day))

        for key, data in stock_value.items():
            if key in self.filt_value[0].keys():
                self.filt_value[0][key] = lowpass_filter(tm, data, self.filt_value[0][key])
                self.filt_value[1][key] = lowpass_filter(tms, data, self.filt_value[1][key])
            else:
                self.filt_value[0][key] = data
                self.filt_value[1][key] = data

        # 信用取引関連のデータを週に一度取得する
        if len(self.credit_records) == 0 or \
                (day.weekday() > workdays.workday(day, 1, holidays_list(day.year)).weekday()):
            for code in CODE.values():
                rec = self.sbi.get_credit_record(code)
                if not rec is None:
                    self.credit_records[code] = rec

            for key, data in self.credit_records.items():
                if key in self.filt_rec.keys():
                    self.filt_rec[key] = lowpass_filter(10, self.credit_records[key], self.filt_rec[key])
                else:
                    self.filt_rec[key] = self.credit_records[key]

            if self.simulate == False:
                f = open("credit_records_%s.dat" % str(day), 'w')
                cPickle.dump(self.credit_records, f)
                f.close()

                for code in CODE.values():
                    info = yahoo_finance_jp.getDetailInfo(code)
                    if not info is None:
                        self.detail_info[code] = info
                f = open("detail_info_%s.dat" % str(day), 'w')
                cPickle.dump(self.detail_info, f)
                f.close()

        if self.simulate == True:
            if self.sbi.t <= tm:
                return
        else:
            # データの保存
            f = open("filt_value.dat", 'w')
            yaml.dump([today, self.filt_value], f)
            f.close()
            self.data_save(day, stock_value)
            if self.offset_days > 0:
                self.offset_days -= 1
                return

        # 買い判定
        # 条件を満たすものを検索
        searched_codes = {}
        buy_condition = [lambda key, v:calc_rate(v[self.use_time], self.filt_value[0][key][self.use_time]) > 0.05,
                         lambda key, v:calc_rate(nikkei_avg[self.use_time], self.filt_avg[self.use_time]) > 0.0,
                         #lambda key, v:calc_rate(self.credit_records[key]["ratio"], self.filt_rec[key]["ratio"]) > 0.0,
                         lambda key, v:self.filt_value[0][key][VOLUME]*self.filt_value[0][key][self.use_time] > avg_val,
                         lambda key, v:calc_rate(v[self.use_time], self.filt_value[1][key][self.use_time]) < down_rate]
        for key, v in stock_value.items():
            try:
                if all([cnd(key, v) for cnd in buy_condition]):
                    searched_codes[key] = v
            except KeyError:
                logger.info(traceback.format_exc())

        if len(searched_codes) > 0:
            for key, v in sorted(searched_codes.items(), key = lambda x: calc_rate(v[self.use_time], self.filt_value[1][x[0]][self.use_time]), reverse=False):
                self.trader.buy(key)

        # 売り判定
        sell_condition = [lambda code:calc_rate(stock_value[code][self.use_time], self.filt_value[0][code][self.use_time]) < 0.0,
                          lambda code:calc_rate(stock_value[code][self.use_time], self.filt_value[1][code][self.use_time]) > 0.1]
        for code in self.trader.orders.keys():
            if code in stock_value:
                if any([cnd(code) for cnd in sell_condition]):
                    # 条件合致で全部売る
                    self.trader.sell(code)

        # Loss Cut
        self.loss_cut(day, stock_value)

        # calc down rate
        self.calc_down_rate()

    def loss_cut(self, day, stock_value, down_rate=0.05, hold_period=10):
        for code, order in self.trader.orders.items():
            if code in stock_value:
                loss = (stock_value[code][self.use_time] - order.value)*order.num
                if loss < -self.sbi.get_total_eval()*down_rate or \
                        day > workdays.workday(order.day, hold_period, holidays_list(day.year)):
                    logger.info("Loss Cut code: %s" % code)
                    self.trader.sell(code)
        logger.info("***** End Trading *****")

    def calc_down_rate(self):
        total_res = self.sbi.get_total_eval() + self.sbi.get_purchase_margin()
        if self.cnt == 0:
            self.rate_base = total_res
        else:
            if self.max_down_rate > total_res/self.rate_base:
                self.max_down_rate = total_res/self.rate_base
            elif total_res/self.rate_base > 1.0:
                self.rate_base = total_res
        self.cnt += 1

    def get_max_down_rate(self):
        return self.max_down_rate

    def get_total_resource(self):
        self.total_res = self.sbi.get_total_eval() + self.sbi.get_purchase_margin()
        logger.info("Res: %d" % self.total_res)
        return self.total_res

    def data_save(self, day, stock_value):
        f = open("stock_value_%s.dat" % str(day), 'w')
        cPickle.dump([day, stock_value], f)
        f.close()
        f = open("market_indices_%s.dat" % str(day), 'w')
        indices = dict((idx, self.sbi.get_market_index(idx)) for idx in INDICES)
        cPickle.dump([day, indices], f)
        f.close()
        f = open("quotes_%s.dat" % str(day), 'w')
        cPickle.dump([day, realtime_quotes()], f)
        f.close()
        f = open("market_info_%s.dat" % str(day), 'w')
        cPickle.dump([self.sbi.get_market_info(i) for i in range(1,8)], f)
        f.close()
        f = open("market_news_%s.dat" % str(day), 'w')
        cPickle.dump(self.sbi.get_market_news(), f)
        f.close()

def real_trade():
    """
    自動売買
    """
    from apscheduler.scheduler import Scheduler
    import signal

    # Start the scheduler
    sched = Scheduler()
    sched.start()

    maneger = TradeManeger(USERNAME, PASSWARD, simulate=False)

    sched.add_cron_job(maneger.trade, day_of_week='mon-fri', hour=7, args=[DOWN_RATE, AVG_VAL, TM, TMS])
    sched.add_cron_job(maneger.get_total_resource, day_of_week='mon-fri', hour=17)
    signal.pause()

def view_data(days, data):
    """
    データのグラフ化
    """
    from matplotlib.dates import  DateFormatter, WeekdayLocator, DayLocator, MONDAY, date2num, num2date
    from matplotlib.finance import candlestick, plot_day_summary, candlestick2

    mondays       = WeekdayLocator(MONDAY)  # major ticks on the mondays
    alldays       = DayLocator()            # minor ticks on the days
    weekFormatter = DateFormatter('%b %d')  # Eg, Jan 12
    dayFormatter  = DateFormatter('%d')     # Eg, 12

    fig = figure()
    fig.subplots_adjust(bottom=0.2)
    ax = fig.add_subplot(111)
    ax.xaxis.set_major_locator(mondays)
    ax.xaxis.set_minor_locator(alldays)
    ax.xaxis.set_major_formatter(weekFormatter)
    ax.plot(days, data)

    ax.xaxis_date()
    ax.autoscale_view()
    setp( gca().get_xticklabels(), rotation=45, horizontalalignment='right')

    #show()
    savefig('graph.png')

def sim_trade(params, graph=True):
    """
    トレードのシミュレーション
    """
    maneger = TradeManeger(USERNAME, PASSWARD)
    days = []
    data = []
    while maneger.sbi.is_ended() == False:
        maneger.trade(*params)
        days.append(maneger.sbi.get_today())
        maneger.sbi.step()
        data.append(maneger.get_total_resource())
        evl = float(500000)/data[-1] + 1.0/maneger.get_max_down_rate()
    if graph:
        view_data(days, data)
    return evl

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'sim':
        sim_trade([DOWN_RATE, AVG_VAL, TM, TMS])
    else:
        real_trade()
