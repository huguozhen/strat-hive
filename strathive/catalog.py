"""Replaceable public exchange directory source; replaces the snapshot only when complete."""
import io
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from .ledger import LedgerError

UA = 'Mozilla/5.0'

def fetch(url):
    request=urllib.request.Request(url,headers={'User-Agent':UA,'Referer':'https://www.sse.com.cn/'})
    with urllib.request.urlopen(request,timeout=30) as response:
        return response.read(30_000_001)

def sse(path, params):
    allrows=[]; page=1; expected=None
    while True:
        query=dict(params,jsonCallBack='jsonpCallback',isPagination='true',**{'pageHelp.pageSize':1000,'pageHelp.pageNo':page,'pageHelp.beginPage':page,'pageHelp.endPage':page,'pageHelp.cacheSize':1})
        raw=fetch('https://query.sse.com.cn/'+path+'?'+urllib.parse.urlencode(query)).decode('utf-8').strip()
        if not raw.startswith('jsonpCallback(') or not raw.rstrip(';').endswith(')'): raise ValueError('上交所响应格式异常')
        body=json.loads(raw[len('jsonpCallback('):].rstrip(';')[:-1])
        rows, meta=body['result'],body['pageHelp']
        if int(meta['pageNo']) != page or not rows: raise ValueError('上交所分页缺失')
        if expected is None: expected=int(meta['total'])
        if int(meta['total']) != expected: raise ValueError('上交所目录在更新期间发生变化')
        allrows.extend(rows)
        if page >= int(meta['pageCount']):
            if len(allrows)!=expected: raise ValueError('上交所目录不完整')
            return allrows
        page+=1
        if page>100: raise ValueError('上交所分页异常')

def xlsx(url):
    ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    raw=fetch(url)
    if len(raw)>30_000_000: raise ValueError('深交所目录文件过大')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.getinfo('xl/worksheets/sheet1.xml').file_size > 100_000_000: raise ValueError('深交所目录文件过大')
        sheet=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        rows=[]
        for row in sheet.findall('.//x:sheetData/x:row',ns):
            cells={}
            for c in row:
                if c.get('t') not in ('inlineStr',None): raise ValueError('深交所文件单元格类型发生变化')
                match=re.match(r'([A-Z]+)\d+$',c.get('r',''))
                if not match: raise ValueError('深交所文件单元格地址无效')
                idx=0
                for char in match.group(1): idx=idx*26+ord(char)-64
                cells[idx]=''.join(c.itertext())
            rows.append(cells)
        if len(rows)<2: raise ValueError('深交所目录为空')
        header=rows[0]
        return [{name:row.get(i,'') for i,name in header.items()} for row in rows[1:]]

def sync(ledger):
    try:
        groups=[
            (sse('sseQuery/commonQuery.do',{'sqlId':'COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L','STOCK_TYPE':'1','COMPANY_STATUS':'2,4,5,7,8','type':'inParams'}),'A_STOCK_CODE','SEC_NAME_CN','stock'),
            (sse('commonSoaQuery.do',{'sqlId':'FUND_LIST','fundType':'00','subClass':'01,02,03,04,06,08,09,31,32,33,34,35,36,37,38'}),'fundCode','fundAbbr','etf'),
            ([x for x in xlsx('https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1') if x['板块']=='主板'],'A股代码','A股简称','stock'),
            ([x for x in xlsx('https://fund.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1000_lf&TABKEY=tab1') if x['基金类别']=='ETF'],'基金代码','基金简称','etf')]
        securities=[]
        for rows,code,name,kind in groups:
            if not rows: raise ValueError('证券目录某个类别为空')
            securities.extend({'code':str(row[code]).strip(),'name':str(row[name]).strip(),'type':kind} for row in rows)
        # A drastically reduced snapshot is likely incomplete; retain the previous copy.
        previous=ledger.execute('catalog.search',{})['metadata']
        if previous and len(securities)<previous['count']*0.5: raise ValueError('新目录数量骤降，已保留原目录')
        return ledger.execute('catalog.import',{'source':'上海证券交易所 / 深圳证券交易所公开目录','securities':securities})
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise LedgerError(f'公开目录同步失败，原目录未更改：{exc}') from exc
