"""Reviewed company notices, scoped to exact dates. Never infer future suspension."""
NOTICES = {
    ('000016', '2026-09-07'): ('2026-69', '2026-09-04',
        'https://epaper.cs.com.cn/zgzqb/html/2026-09/04/nw.D110000zgzqb_20260904_6-B015.htm'),
    ('002731', '2026-09-07'): ('2026-109', '2026-09-07',
        'https://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESZ_STOCK/2026/2026-9/2026-09-07/12586056.PDF'),
    ('002743', '2026-09-07'): ('2026-062', '2026-09-05',
        'https://epaper.cs.com.cn/zgzqb/html/2026-09/05/nw.D110000zgzqb_20260905_4-B024.htm'),
    ('002870', '2026-09-07'): ('2026-069', '2026-09-08',
        'https://static.cninfo.com.cn/finalpage/2026-09-08/1225550443.PDF'),
    ('002998', '2026-09-07'): ('2026-053', '2026-09-08',
        'https://static.cninfo.com.cn/finalpage/2026-09-08/1225551828.PDF'),
    ('301139', '2026-09-07'): ('2026-092', '2026-09-07',
        'https://static.cninfo.com.cn/finalpage/2026-09-07/1225551366.PDF'),
    ('600825', '2026-09-07'): ('2026-014', '2026-09-08',
        'https://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESH_STOCK/2026/2026-9/2026-09-08/12588319.PDF'),
    ('600929', '2026-09-07'): ('2026-034', '2026-09-05',
        'https://static.cninfo.com.cn/finalpage/2026-09-05/1225548997.PDF'),
}


def evidence(code, day):
    notice = NOTICES.get((code, day))
    if not notice: return None
    number, disclosed, url = notice
    return dict(kind='company_suspension_notice', code=code, date=day, noticeId=number,
                disclosedAt=disclosed, url=url, validatedDates=[day], reviewedAt='2026-09-08',
                evidenceTiming=('retrospective_confirmation' if disclosed > day else
                                'ongoing_suspension_notice' if code == '600929' else 'contemporaneous'))


def valid_notice(value, code):
    return isinstance(value, dict) and value.get('code') == code and value == evidence(code, value.get('date'))
