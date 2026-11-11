"""从沪深交易所获取主板股票和 ETF 目录。"""

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SH = "https://query.sse.com.cn/sseQuery/"
SZ = "https://www.szse.cn/api/report/ShowReport/data"


def fetch(url, params, referer):
    request = Request(f"{url}?{urlencode(params)}",
                      headers={"Referer": referer, "User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except (OSError, ValueError):
            if attempt == 2:
                raise


def text(value):
    return html.unescape(re.sub(r"<[^>]*>", "", value)).strip()


def shanghai(kind):
    stock = kind == "stock"
    params = {"sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L", "STOCK_TYPE": "1"} if stock else {
        "sqlId": "FUND_LIST", "fundType": "00",
    }
    params.update({"isPagination": "true", "pageHelp.pageSize": 2500, "pageHelp.pageNo": 1,
                   "pageHelp.beginPage": 1, "pageHelp.endPage": 1, "pageHelp.cacheSize": 1})
    data = fetch(SH + ("commonQuery.do" if stock else "commonSoaQuery.do"),
                 params, "https://www.sse.com.cn/")["pageHelp"]
    rows = data["data"]
    if len(rows) != int(data["total"]):
        raise ValueError(f"incomplete Shanghai {kind} catalog: {len(rows)} / {data['total']}")
    if stock:
        return [("SH", row["A_STOCK_CODE"], kind, row["SEC_NAME_CN"], row["FULL_NAME"]) for row in rows
                if row["DELIST_DATE"] == "-" and row["SEC_NAME_CN"] != "-"]
    return [("SH", row["fundCode"], kind, row["secNameFull"], row.get("fundFullName") or row["secNameFull"])
            for row in rows]


def shenzhen(kind):
    stock = kind == "stock"
    def page(index):
        return fetch(SZ, {"SHOWTYPE": "JSON", "CATALOGID": "1110" if stock else "1945",
                          "TABKEY": "tab1", "tab1PAGENO": index, "PAGENO": index},
                     "https://www.szse.cn/")[0]

    first = page(1)
    with ThreadPoolExecutor(max_workers=8) as executor:
        remaining = list(executor.map(page, range(2, int(first["metadata"]["pagecount"]) + 1)))
    rows = [row for result in (first, *remaining) for row in result["data"]]
    code_field = "agdm" if stock else "sys_key"
    if len({text(row[code_field]) for row in rows}) != int(first["metadata"]["recordcount"]):
        raise ValueError(f"incomplete Shenzhen {kind} catalog")
    if stock:
        return [("SZ", row["agdm"], kind, text(row["agjc"]), row.get("gsqc") or text(row["agjc"]))
                for row in rows if row["bk"] == "主板"]
    return [("SZ", text(row["sys_key"]), kind, text(row["kzjcurl"]),
             row.get("jjqc") or text(row["kzjcurl"])) for row in rows]


EASTMONEY = "https://push2.eastmoney.com/api/qt/clist/get"


def shenzhen_market_directory(kind):
    market_filter = "m:0+t:6" if kind == "stock" else "b:MK0021,b:MK0022,b:MK0023,b:MK0024"

    def page(index):
        return fetch(EASTMONEY, {"pn": index, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                                 "fid": "f12", "fs": market_filter, "fields": "f12,f13,f14"},
                     "https://www.szse.cn/")["data"]

    first = page(1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        pages = [first, *executor.map(page, range(2, (first["total"] + 99) // 100 + 1))]
    rows = [row for result in pages for row in result["diff"]]
    if len({(row["f13"], row["f12"]) for row in rows}) != first["total"]:
        raise ValueError(f"incomplete market directory for Shenzhen {kind}")
    return [("SZ", row["f12"], kind, row["f14"], row["f14"]) for row in rows if row["f13"] == 0]


def shenzhen_with_fallback(kind):
    try:
        return shenzhen(kind), SZ
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return shenzhen_market_directory(kind), EASTMONEY


def fetch_catalog():
    with ThreadPoolExecutor(max_workers=4) as executor:
        stocks = executor.submit(shanghai, "stock")
        funds = executor.submit(shanghai, "etf")
        sz_stocks = executor.submit(shenzhen_with_fallback, "stock")
        sz_funds = executor.submit(shenzhen_with_fallback, "etf")
        sz_stock_rows, sz_stock_source = sz_stocks.result()
        sz_fund_rows, sz_fund_source = sz_funds.result()
        rows = [*stocks.result(), *funds.result(), *sz_stock_rows, *sz_fund_rows]
    sources = {"SH stock": SH, "SH etf": SH, "SZ stock": sz_stock_source, "SZ etf": sz_fund_source}
    return [
        dict(symbol=f"{market}.{code}", market=market, code=code, kind=kind,
             name=text(name), full_name=text(full_name))
        for market, code, kind, name, full_name in rows
    ], sources
