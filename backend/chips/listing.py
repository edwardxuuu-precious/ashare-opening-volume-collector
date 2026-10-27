"""Bounded official SH/SZ current-list reconciliation; no historical membership."""
import io
import hashlib
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlencode


ARCHIVED_LISTING_SCOPE = 'LISTING_DATE_ONLY_NOT_CURRENT_MEMBERSHIP'
ARCHIVED_FACT_CHECKSUM = 'SHA256_JSON_UTF8_ENSURE_ASCII_FALSE_COMPACT_SORT_KEYS_WITHOUT_SHA256'


def _sha256(value, *, sort_keys=True):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=sort_keys,
        separators=(',',':')).encode()).hexdigest()


def _valid_sha256(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def _date(value):
    return isinstance(value,str) and datetime.strptime(value,'%Y-%m-%d').date().isoformat()==value


def _trusted_archived_listing(stock, evidence, as_of):
    try:
        listed=stock.get('listed');source_as_of=evidence.get('source_as_of');adopted=evidence.get('as_of')
        code=stock.get('code')
        if (not isinstance(code,str) or len(code)!=6 or not code.isdigit()
            or evidence.get('security_id')!=code or evidence.get('name')!=stock.get('name')
            or not stock.get('name') or evidence.get('listed')!=listed
            or not all(_date(day) for day in (listed,source_as_of,adopted))
            or not listed<=source_as_of<adopted or (as_of is not None and adopted!=as_of)
            or evidence.get('source_key')!=f'collector/chips/universe-verified/{source_as_of}.json'
            or evidence.get('fact_scope')!=ARCHIVED_LISTING_SCOPE
            or evidence.get('sha256_basis')!=ARCHIVED_FACT_CHECKSUM
            or evidence.get('current_membership_verified') is not False
            or not all(_valid_sha256(evidence.get(k)) for k in ('source_bundle_sha256','source_stocks_sha256'))
            or evidence.get('sha256')!=_sha256({k:v for k,v in evidence.items() if k!='sha256'})):
            return None
        return listed
    except (TypeError,ValueError):
        return None


def trusted_listing(stock, as_of=None):
    """Distinguish an official dated listing fact from a vendor-reported field."""
    evidence=stock.get('listing_evidence') or {}
    if evidence.get('provider')=='ARCHIVED_OFFICIAL_LISTING_FACT':
        return _trusted_archived_listing(stock,evidence,as_of)
    if (evidence.get('security_id')!=stock.get('code') or evidence.get('listed')!=stock.get('listed')
        or evidence.get('provider') not in ('SSE_CURRENT_LIST','SZSE_CURRENT_LIST','OFFICIAL_CURRENT_LIST_RECONCILIATION')
        or len(evidence.get('sha256',''))!=64
        or any(c not in '0123456789abcdef' for c in evidence.get('sha256',''))
        or not evidence.get('as_of') or (as_of is not None and evidence['as_of']!=as_of)):
        return None
    return stock['listed']


def verified_current(bundle, stocks, as_of):
    """Use a separately recorded official reconciliation only for its exact date/set."""
    if not bundle:return None
    rows=bundle.get('stocks',[])
    canonical=json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode()
    if (bundle.get('schema')!='stock.chips.official-current-universe.v1'
        or bundle.get('as_of')!=as_of or bundle.get('historical_membership') is not False
        or hashlib.sha256(canonical).hexdigest()!=bundle.get('stocks_sha256')
        or not 1000<len(rows)<15000 or len({s['code'] for s in rows})!=len(rows)
        or {s['code'] for s in rows}!={s['code'] for s in stocks}):
        raise ValueError('OFFICIAL_RECONCILIATION_DATE_HASH_OR_SET_MISMATCH')
    providers={r.get('provider') for r in bundle.get('receipts',[])}
    if not {'SSE_CURRENT_LIST','SZSE_CURRENT_LIST','BSE_CURRENT_LIST_VISIBLE_BROWSER_TABLE'}<=providers:
        raise ValueError('OFFICIAL_RECONCILIATION_MARKET_MISSING')
    for row in rows:
        if not row.get('name') or len(row['code'])!=6 or not row['code'].isdigit():
            raise ValueError('OFFICIAL_RECONCILIATION_SECURITY_INVALID')
        day=row.get('listed')
        if not day or datetime.strptime(day,'%Y-%m-%d').date().isoformat()!=day or day>as_of:
            raise ValueError('OFFICIAL_RECONCILIATION_LISTING_DATE_INVALID')
    receipt={'provider':'OFFICIAL_CURRENT_LIST_RECONCILIATION','as_of':as_of,
        'stocks_sha256':bundle['stocks_sha256'],'total':len(rows),
        'observed_at':bundle['observed_at'],'sources':bundle['receipts'],'historical_membership':False}
    verified=[{**row,'listing_evidence':{'security_id':row['code'],'listed':row['listed'],
        'provider':'OFFICIAL_CURRENT_LIST_RECONCILIATION','sha256':bundle['stocks_sha256'],'as_of':as_of}} for row in rows]
    return verified,receipt


def restore_archived_listing_facts(stocks, bundle, as_of, source_key):
    """Carry an already known listing date, never archived current membership."""
    source_as_of=(bundle or {}).get('as_of')
    try:
        dated=all(_date(day) for day in (source_as_of,as_of))
    except (TypeError,ValueError):
        dated=False
    if (not dated or source_as_of>=as_of
        or source_key!=f'collector/chips/universe-verified/{source_as_of}.json'):
        raise ValueError('ARCHIVED_LISTING_SOURCE_DATE_OR_KEY_INVALID')
    # Validate the donor's own exact dated set, hash and all three market sources.
    verified=verified_current(bundle,bundle.get('stocks',[]),source_as_of)
    if not verified:
        raise ValueError('ARCHIVED_LISTING_SOURCE_UNVERIFIED')
    source_rows={row['code']:row for row in verified[0]}
    # Match the archived bundle writer's compact UTF-8, preserving field order.
    bundle_hash=_sha256(bundle,sort_keys=False)
    restored=[];count=0
    for stock in stocks:
        if trusted_listing(stock,as_of):
            restored.append(stock)
            continue
        fact=source_rows.get(stock['code'])
        if fact is None:
            restored.append(stock)
            continue
        if stock.get('name')!=fact['name']:
            raise ValueError('ARCHIVED_LISTING_SECURITY_NAME_CONFLICT')
        if stock.get('listed') is not None and stock['listed']!=fact['listed']:
            raise ValueError('ARCHIVED_LISTING_DATE_CONFLICT')
        evidence={'provider':'ARCHIVED_OFFICIAL_LISTING_FACT','security_id':stock['code'],
            'name':stock['name'],'listed':fact['listed'],'as_of':as_of,
            'source_as_of':source_as_of,'source_key':source_key,
            'source_bundle_sha256':bundle_hash,'source_stocks_sha256':bundle['stocks_sha256'],
            'fact_scope':ARCHIVED_LISTING_SCOPE,'current_membership_verified':False,
            'sha256_basis':ARCHIVED_FACT_CHECKSUM}
        evidence['sha256']=_sha256(evidence)
        restored.append({**stock,'listed':fact['listed'],'listing_evidence':evidence})
        count+=1
    receipt={'provider':'ARCHIVED_OFFICIAL_LISTING_FACTS','as_of':as_of,'restored_count':count,
        'source_as_of':source_as_of,'source_key':source_key,'source_bundle_sha256':bundle_hash,
        'source_stocks_sha256':bundle['stocks_sha256'],'fact_scope':ARCHIVED_LISTING_SCOPE,
        'current_membership_verified':False}
    return restored,receipt


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
                               'listed': day, 'secid': '1.' + row['A_STOCK_CODE'],
                               'listing_evidence':{'security_id':row['A_STOCK_CODE'],'listed':day,
                                   'provider':'SSE_CURRENT_LIST','sha256':hashlib.sha256(raw).hexdigest(),'as_of':as_of}})
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
            stocks.append({'code': code, 'name': row['F'], 'listed': day, 'secid': '0.' + code,
                'listing_evidence':{'security_id':code,'listed':day,'provider':'SZSE_CURRENT_LIST',
                    'sha256':hashlib.sha256(raw).hexdigest(),'as_of':as_of}})
    if len(stocks) < 1000 or len(stocks) > 15000 or len({s['code'] for s in stocks}) != len(stocks):
        raise ValueError('OFFICIAL_SH_SZ_LIST_COUNT_INVALID')
    if any(len(s['code']) != 6 or not s['code'].isdigit() or not s['code'].startswith(('0', '3', '6')) for s in stocks):
        raise ValueError('OFFICIAL_SH_SZ_LIST_CODE_INVALID')
    receipts.append(source.receipt(url, raw, provider='SZSE_CURRENT_LIST', total=len(parsed)-1))
    return stocks, receipts
