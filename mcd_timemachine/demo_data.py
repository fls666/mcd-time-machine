"""示例数据生成器（--demo 专用）。

用于在没有 MCP Token 的情况下演示完整链路：**全部为虚构数据**，
不来自任何真实账号，也不包含任何真实个人信息。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Dict, List

from .collector import _guess_category

MENU = [
    ("巨无霸", 24.0), ("麦辣鸡腿堡", 21.5), ("板烧鸡腿堡", 20.0),
    ("双层吉士汉堡", 13.5), ("麦香鱼", 15.0), ("麦麦脆汁鸡", 13.0),
    ("麦乐鸡（5块）", 11.0), ("中薯条", 12.0), ("大薯条", 14.5),
    ("麦辣鸡翅", 13.0), ("麦旋风", 9.5), ("新地", 8.0),
    ("甜筒", 6.0), ("中可乐", 9.0), ("拿铁", 15.0), ("美式咖啡", 12.0),
    ("玉米杯", 7.0), ("早餐全餐", 18.0), ("皮蛋瘦肉粥", 10.0), ("薯饼", 6.5),
]

STORES = [
    "麦当劳（科技园得来速餐厅）", "麦当劳（中心书城餐厅）",
    "麦当劳（万象城餐厅）", "麦当劳（公司楼下餐厅）",
]

CHANNELS = ["麦乐送", "到店取餐", "堂食", "得来速"]


def generate(orders: int = 160, seed: int = 1024, days: int = 400) -> List[Dict[str, Any]]:
    """生成一份可直接走分析流程的示例订单数据。"""
    rng = random.Random(seed)
    end = datetime.now()
    start = end - timedelta(days=days)

    result: List[Dict[str, Any]] = []
    for i in range(orders):
        moment = start + timedelta(
            seconds=rng.randint(0, int((end - start).total_seconds()))
        )
        # 工作日偏午餐，周末偏下午茶与夜宵
        if moment.weekday() >= 5:
            hour = rng.choices([11, 12, 15, 16, 18, 21, 22], weights=[2, 2, 3, 3, 2, 2, 1])[0]
        else:
            hour = rng.choices([8, 9, 12, 13, 18, 19, 22], weights=[2, 2, 4, 3, 3, 3, 1])[0]
        moment = moment.replace(hour=hour, minute=rng.randint(0, 59))

        picked = rng.sample(MENU, rng.randint(1, 4))
        items = [
            {
                "name": name,
                "qty": rng.randint(1, 2),
                "price": price,
                "category": _guess_category(name),
            }
            for name, price in picked
        ]
        total = round(sum(it["price"] * it["qty"] for it in items) + rng.choice([0, 6, 6, 9]), 2)
        result.append(
            {
                "order_id": f"DEMO{moment.strftime('%Y%m%d')}{i:04d}",
                "time": moment.strftime("%Y-%m-%d %H:%M"),
                "date": moment.strftime("%Y-%m-%d"),
                "hour": moment.hour,
                "weekday": moment.weekday(),
                "store": rng.choice(STORES),
                "channel": rng.choice(CHANNELS),
                "total": total,
                # 示例数据的 total 是按单价×数量另加配送费算出来的，
                # 语义上代表「实际支付金额」，与真实数据的 actual 口径一致
                "amount_source": "actual",
                "discount": rng.choice([0, 3, 5, 8]),
                "items": items,
            }
        )

    result.sort(key=lambda o: o["time"], reverse=True)
    return result


__all__ = ["generate"]
