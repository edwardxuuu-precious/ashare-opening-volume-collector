"""Bounded official SH/SZ current-list reconciliation; no historical membership."""
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlencode


def sh_sz(source, as_of):
    stocks = []
    receipts = []
    for kind in ('1', '8'):
        query = urlencode({'STOCK_TYPE': kind, 'REG_PROVINCE': '', 'CSRC_CODE': '',
            'STOCK_CODE': '', 'sqlId': 'COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L',
            'COMPANY_STATUS': '2,4,5,7,8', 'type': 'inParams', 'isPagination': 'true',
            'pageHelp.pageSize': '10000', 'pageHelp.pageNo': '1'})
        url = 'https://query.sse.com.cn/sseQuery/commonQuery.do?' + query
        raw = source.read_bytes(url, referer='https://www.sse.com.cn/')
        value = json.loads(raw)
        rows = value['result']
        if not rows or len(rows) != int(value['pageHelp']['total']):
            raise ValueError('SSE_LIST_INCOMPLETE')
        for row in rows:
            day = datetime.strptime(row['LIST_DATE'], '%Y%m%d').date().isoformat()
            if day <= as_of:
                stocks.append({'code': row['A_STOCK_CODE'], 'name': row['SEC_NAME_CN'],
                               'listed': day, 'secid': '1.' + row['A_STOCK_CODE']})
        receipts.append(source.receipt(url, raw, provider='SSE_CURRENT_LIST', total=len(rows)))
    url = 'https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1'
    raw = source.read_bytes(url, referer='https://www.szse.cn/')
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 40000000:
            raise ValueError('SZSE_LIST_SIZE_BOUND')
        strings = [''.join(item.itertext()) for item in ET.fromstring(
            archive.read('xl/sharedStrings.xml')).findall('m:si', ns)] if 'xl/sharedStrings.xml' in archive.namelist() else []
        parsed = []
        for row in ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('.//m:row', ns):
            cells = {}
            for cell in row:
                number = cell.find('m:v', ns)
                text = cell.find('m:is', ns)
                cells[cell.attrib['r'].rstrip('0123456789')] = (
                    ''.join(text.itertext()) if cell.get('t') == 'inlineStr' and text is not None
                    else strings[int(number.text)] if cell.get('t') == 's'
                    else number.text if number is not None else '')
            parsed.append(cells)
    if not parsed or parsed[0].get('E') != 'A股代码' or parsed[0].get('G') != 'A股上市日期':
        raise ValueError('SZSE_LIST_SCHEMA_CHANGED')
    for row in parsed[1:]:
        code = row.get('E', '')
        if not code:  # B-only listings do not have an A-share security.
            continue
        day = datetime.strptime(row['G'], '%Y-%m-%d').date().isoformat()
        if day <= as_of:
            stocks.append({'code': code, 'name': row['F'], 'listed': day, 'secid': '0.' + code})
    if len(stocks) < 1000 or len(stocks) > 15000 or len({s['code'] for s in stocks}) != len(stocks):
        raise ValueError('OFFICIAL_SH_SZ_LIST_COUNT_INVALID')
    if any(len(s['code']) != 6 or not s['code'].isdigit() or not s['code'].startswith(('0', '3', '6')) for s in stocks):
        raise ValueError('OFFICIAL_SH_SZ_LIST_CODE_INVALID')
    receipts.append(source.receipt(url, raw, provider='SZSE_CURRENT_LIST', total=len(parsed)-1))
    return stocks, receipts
