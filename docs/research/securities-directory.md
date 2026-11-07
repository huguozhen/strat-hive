# 沪深主板股票及 ETF 基础资料调研

调查日期：2026-11-07；以下访问均在本运行环境实际执行。结论：两交易所的网页后台均可无账号获取当前目录，能实现定期快照同步；但这些是网页内部接口而非承诺稳定的公开 API，且可访问不等于有权商用或再分发。四类源分别处理，验证完整后再更新库存，无法保证历史、停牌或退市证券全覆盖。**同次调研后续复验时，上交所 query.sse.com.cn 对此前成功的相同 URL 返回 TLS 自签名证书错误：该环境现在不能可靠无人值守直连，不应关闭 TLS 校验。**

## 交易所目录与实测

| 标的 | 官方入口及可运行的请求 | 当日观测 | 关键字段与范围 |
|---|---|---|---|
| 上交所主板 A 股 | [股票列表页面](https://www.sse.com.cn/assortment/stock/list/share/)；[网页脚本及原始参数](https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_stocksDepositoryReceipts_2021.js)，请求见下面脚本 | HTTP 200，`pageHelp.total=1700`，1000/700 两页 | `STOCK_TYPE=1`；`COMPANY_STATUS=2,4,5,7,8`、`type=inParams` 必须随网页设置。`A_STOCK_CODE`、`SEC_NAME_CN`、`LIST_DATE`、`CSRC_CODE_DESC`。不加状态筛选时返回 1845，不能视为当前目录。列表属于公司证券资料，不能自行假定覆盖历史状态；单独请求 `STOCK_TYPE=2` 如确需主板 B 股。 |
| 深交所主板 A 股 | [股票列表页面](https://www.szse.cn/market/product/stock/list/index.html)；下载 [A 股列表 XLSX](https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1) | HTTP 200，2906 A 股，`板块=主板` 1495 条，`创业板` 1411 条 | `板块`、`A股代码`、`A股简称`、`A股上市日期`、`所属行业`；按板块字段明确过滤，不按代码前缀或名称猜测。XLSX 单元格代码为字符串，保留前导零。`TABKEY=tab2` 是 B 股页面，需单独检验。 |
| 上交所 ETF | [官方 ETF 列表](https://www.sse.com.cn/assortment/fund/list/?type=5)；[网页基金脚本及原始参数](https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_fund_2021.js)，请求见下面脚本 | HTTP 200，ETF 子类过滤后 `total=923`，`pageSize=1000` 一页 | `fundCode`、`fundAbbr`、`secNameFull`、`listingDate`、`companyName`、`subClass`、`INDEX_NAME`。必须同时使用网页的 `fundType=00` 和 `subClass=01,02,03,04,06,08,09,31,32,33,34,35,36,37,38`；单用 `fundType=00` 混入其它基金。上述集合由网页定义，应随其变更。 |
| 深交所 ETF | [基金列表 XLSX](https://fund.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1000_lf&TABKEY=tab1) | HTTP 200，共 1087：ETF 772、LOF 284、不动产基金 31 | `基金代码`、`基金简称`、`基金类别`、`上市日期`、`基金管理人`；筛选 `基金类别=ETF`。ETF 涵盖股票、债券、跨境等；是否限制底层资产须由产品需求另定。 |

上交所统计 [2026-11-04 页面](https://www.sse.com.cn/market/stockdata/statistic/) 主板 1741 **股票**包含 B 股等，与上述 1700 主板 A 股并非同一口径，不能直接用作全量断言。数量只代表请求时快照，不能硬编码。上交所网页样例的状态过滤是页面实现，不能简单等同于“正常交易”，数据中还需核对暂停/退市状态。

## 一次性复现（Python 标准库，不需依赖或 API token）

以下命令在证书链可信且网络可达时打印四类数量和首条记录。初次采样使用 curl 获取四个有效响应；以这些保存的原始响应对示例解析和分页做离线执行，分别得到 1700、1495、923、772。之后重新在线执行时上交所出现证书错误，详见下文；它不是当前环境下已验证可稳定完成的在线脚本。遵循网页的参数设置，校验 HTTP/JSONP、XLSX 和分页返回；任务生产化需更强的空结果、缺页、重复、异动阈值检查。HTTP 429、403、验证码、超时或校验失败应保留上次成功结果并告警，不能把失败解释为全部退市。请求频率与授权须先获确认。

```bash
python3 - <<'PY'
import json, urllib.parse, urllib.request, zipfile, io
import xml.etree.ElementTree as ET

UA = 'Mozilla/5.0'
def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': 'https://www.sse.com.cn/'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def sse(path, params):
    base = 'https://query.sse.com.cn/' + path
    allrows = []
    page = 1
    while True:
        query = dict(params, jsonCallBack='jsonpCallback', isPagination='true',
                     **{'pageHelp.pageSize': 1000, 'pageHelp.pageNo': page,
                        'pageHelp.beginPage': page, 'pageHelp.endPage': page,
                        'pageHelp.cacheSize': 1})
        raw = get(base + '?' + urllib.parse.urlencode(query)).decode('utf-8')
        prefix = 'jsonpCallback('
        if not raw.startswith(prefix): raise ValueError('unexpected JSONP response')
        body = json.loads(raw[len(prefix):].rstrip().removesuffix(';').removesuffix(')'))
        rows, meta = body['result'], body['pageHelp']
        if meta['pageNo'] != page or not rows: raise ValueError('empty or incorrect page')
        allrows.extend(rows)
        if page >= meta['pageCount']:
            if len(allrows) != meta['total']: raise ValueError('incomplete snapshot')
            return allrows
        page += 1

def xlsx(url):
    ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(get(url))) as z:
        sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        # These two observed reports use inline strings. Refuse silent corruption if format changes.
        rows = []
        for row in sheet.findall('.//x:sheetData/x:row', ns):
            cells = []
            for c in row:
                if c.get('t') not in ('inlineStr', None): raise ValueError('unexpected cell type')
                cells.append(''.join(c.itertext()))
            rows.append(cells)
        if len(rows) < 2: raise ValueError('empty workbook')
        return [dict(zip(rows[0], row)) for row in rows[1:]]

stocks_sh = sse('sseQuery/commonQuery.do', {
    'sqlId':'COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L', 'STOCK_TYPE':'1',
    'COMPANY_STATUS':'2,4,5,7,8', 'type':'inParams'})
etfs_sh = sse('commonSoaQuery.do', {
    'sqlId':'FUND_LIST', 'fundType':'00',
    'subClass':'01,02,03,04,06,08,09,31,32,33,34,35,36,37,38'})
stocks_sz = [x for x in xlsx('https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1') if x['板块'] == '主板']
etfs_sz = [x for x in xlsx('https://fund.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1000_lf&TABKEY=tab1') if x['基金类别'] == 'ETF']
for name, rows, code, title in [
    ('SSE main A', stocks_sh, 'A_STOCK_CODE', 'SEC_NAME_CN'),
    ('SZSE main A', stocks_sz, 'A股代码', 'A股简称'),
    ('SSE ETF', etfs_sh, 'fundCode', 'fundAbbr'),
    ('SZSE ETF', etfs_sz, '基金代码', '基金简称')]:
    codes = [x[code] for x in rows]
    if len(codes) != len(set(codes)) or any(len(x) != 6 or not x.isdigit() for x in codes):
        raise ValueError(name + ': invalid or duplicate codes')
    print(name, len(rows), rows[0][code], rows[0][title])
PY
```

示例 XLSX 解析器仅适配本次观察到的 inline string 行单元格，且按连续列读取；正式程序使用成熟 XLSX 库，依据单元格引用对齐列、检查 schema。上交所是 JSONP，必须校验 callback 再解码，不能直接 `json.loads` 整个响应。分页期间源头发生变动也可能导致遗漏或重复：记录抓取时间，唯一键使用 `(exchange, code)`，核对总数及唯一数，数据全量校验通过后原子替换，差异异常时人工复核或延后同步。股票/基金的交易所与六位代码都应保留，代码不得转数字。明确只同步公开目录当前记录，不将缺失直接解释为终止上市；需要历史状态应另接终止/退市目录和公告。

## 限制、失败及替代

- 此次初次读取所示接口均返回 HTTP 200；随后的 curl、Python urllib 复验，上交所 `query.sse.com.cn` 返回 `SSL: CERTIFICATE_VERIFY_FAILED: self-signed certificate`，而深交所 `www.szse.cn` TLS 验证成功。同地址表现改变的原因尚未确定，可能来自网络路径或服务端，不能确定归因；先检查部署环境的信任链或向源站确认。不得以关闭证书验证作为生产方案。已保存的响应证实数据格式，但不证明后续可达。HTTP 200 并不代表官方对稳定性、访问次数、服务级别及后续无人值守提供承诺；未查询到官方的上述网页 API 使用政策或频率额度。应设置有限重试、超时、退避、日志和告警；每日同步足以覆盖基础目录，具体频率需源站许可。不要绕过验证码或访问控制。
- [深交所法律声明](https://www.szse.cn/application/laws/)允许在遵守声明前提下为非商业目的浏览和下载；以向他人出售牟利为目的使用网站内容，包括电子抓取等，须书面许可。[两交易所证券信息联合声明](https://www.sseinfo.com/aboutus/authstatement/)对使用、复制、传输及信息产品开发规定许可；对实际产品的用途应向权利人确认并取得适用授权。这里不推断某个部署的合法性。
- 巨潮资讯 `https://www.cninfo.com.cn/new/data/sse_stock.json` 此次 HTTP 404；`https://www.cninfo.com.cn/new/data/szse_stock.json` HTTP 200，但含 A 股等多个类别，且不是明确主板和 ETF 的完整统一目录，不可作为四类资料已验证替代源。不要因 HTTP 200 就认为其覆盖正确。
- 证监会 [公募基金产品索引](https://www.csrc.gov.cn/csrc/c101900/c1029655/content.shtml) 可下载 XLSX，但为定期公募基金名录，不能替代交易所实时上市 ETF 交易代码和状态；需要经许可的持续生产级供应时，应选择上证所信息网络、深圳证券信息或获授权供应商，确认证券基础资料覆盖、更新、商用和再分发许可、费用及 SLA。可用的第三方接口不能自动继承交易所数据使用权。
