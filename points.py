# -*- coding: utf-8 -*-
"""积分商品、购买闭环与 AI 计费。

真实支付只复用 payment.VibePayChannel 的 page.pay + query；本模块不保存
任何密钥。mock 订单明确标记「模拟已付款」，不产生真实扣款。
"""
import secrets
from datetime import datetime

import db
import payment


PRODUCTS = {
    "starter": {"name": "体验积分包", "points": 100, "price": 0.01},
    "standard": {"name": "标准积分包", "points": 500, "price": 9.90},
    "pro": {"name": "专业积分包", "points": 2000, "price": 29.90},
}

# 仅真实大模型 API 调用预扣；本地模板/本地证据检索免费。
AI_COSTS = {
    "relation_judge": 10,
    "greeting": 2,
    "deep_analysis": 5,
    "insight_report": 10,
}


def product_choices():
    return ["%s｜%d 积分｜¥%.2f" % (p["name"], p["points"], p["price"])
            for p in PRODUCTS.values()]


def product_key_from_choice(text):
    text = (text or "").split("｜", 1)[0].strip()
    for key, product in PRODUCTS.items():
        if product["name"] == text:
            return key
    raise ValueError("请选择有效的积分包")


def _out_trade_no():
    return "JBYHP%s%s" % (datetime.now().strftime("%Y%m%d%H%M%S"),
                           secrets.token_hex(4).upper())


def create_purchase(product_key, channel="vibe", user_key=None):
    """建单并生成支付意图。mock 直接模拟到账；vibe 只打开收银台，不代付。"""
    if product_key not in PRODUCTS:
        raise ValueError("未知积分包：%s" % product_key)
    if channel not in ("mock", "vibe"):
        raise ValueError("未知支付通道：%s" % channel)
    db.init_db()
    product = PRODUCTS[product_key]
    user_key = user_key or db.current_account()
    chan = None
    if channel == "vibe":
        chan = payment.VibePayChannel()
        ready, why = chan.ready()
        if not ready:
            raise payment.PaymentError("Vibe Pay 未就绪：%s" % why)
    out_trade_no = _out_trade_no()
    oid = db.create_points_order(
        user_key, product_key, product["name"], product["points"],
        product["price"], channel, out_trade_no)
    if channel == "mock":
        bal, _ = db.points_mark_order_paid(
            oid, "MOCK-" + out_trade_no, status="模拟已付款")
        return {"order_id": oid, "out_trade_no": out_trade_no,
                "channel": channel, "status": "模拟已付款",
                "balance": bal, "pay_url": "", "product": product}
    try:
        intent = chan.start_payment(
            out_trade_no, product["price"],
            "进步壹号%s %d积分" % (product["name"], product["points"]))
    except Exception:
        db.set_points_order_status(oid, "下单失败")
        raise
    db.set_points_order_status(oid, "等待买家付款")
    return {"order_id": oid, "out_trade_no": out_trade_no,
            "channel": channel, "status": "等待买家付款",
            "pay_url": intent["pay_url"], "product": product}


def sync_purchase(order_id, channel=None):
    """主动查单并幂等入账，返回 (WAIT/SUCCESS/FAIL, 说明, 当前余额)。"""
    order = db.get_points_order(order_id)
    if not order:
        raise ValueError("积分订单不存在：%s" % order_id)
    bal = db.points_balance(order["user_key"])
    if order["status"] in ("已付款", "模拟已付款"):
        return "SUCCESS", "已到账，无需重复入账", bal
    if order["channel"] == "mock":
        return "FAIL", "模拟订单未到账，请重新下单", bal
    chan = channel or payment.VibePayChannel()
    state, msg, trade_no = chan.query(order["out_trade_no"])
    if state == "SUCCESS":
        bal, credited = db.points_mark_order_paid(order_id, trade_no)
        return "SUCCESS", ("支付成功，已入账 %d 积分" % order["points"]
                           if credited else "该订单此前已入账"), bal
    if state == "FAIL":
        db.set_points_order_status(order_id, "支付失败")
        return "FAIL", msg, bal
    db.set_points_order_status(order_id, "等待买家付款")
    return "WAIT", msg or "等待买家付款", bal


def begin_api_charge(feature, user_key=None):
    """真实 API 调用前预扣；返回用于失败退款的票据。"""
    if feature not in AI_COSTS:
        raise ValueError("未知计费功能：%s" % feature)
    user_key = user_key or db.current_account()
    amount = AI_COSTS[feature]
    operation_id = secrets.token_hex(12)
    balance = db.points_consume(user_key, amount, "AI功能：%s" % feature)
    return {"feature": feature, "user_key": user_key, "amount": amount,
            "operation_id": operation_id, "balance": balance}


def refund_api_charge(ticket):
    """API 失败/降级时退回预扣；同一操作重复退款会被唯一索引拒绝。"""
    if not ticket:
        return db.points_balance()
    return db.points_refund(
        ticket["user_key"], ticket["amount"],
        "API失败退回：%s" % ticket["feature"],
        "APIREFUND:" + ticket["operation_id"])
